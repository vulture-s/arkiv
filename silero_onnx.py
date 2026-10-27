"""Silero VAD on onnxruntime + numpy — no torch.

Why this file exists: torch was in the desktop bundle only to run silero VAD
(`transcribe._vad_filter`, one `torch.from_numpy`). It was 46% of the installed
files on Windows (11,956 of 25,764) and 526 MB of the macOS .app, and the file
count is what made every Windows uninstall/upgrade slow and fragile (see
scripts/upgrade_desktop_windows.py). onnxruntime is already in the bundle
(chromadb depends on it), so the model can run without torch at all.

What is ours vs vendored:
  - `SileroVAD` is a numpy port of silero_vad.utils_vad.OnnxWrapper (16 kHz
    only — `_vad_filter` never calls it at any other rate): same 512-sample
    window, same 64-sample context carried between windows, same LSTM state.
  - `get_speech_timestamps_from_probs` is copied VERBATIM from silero-vad 6.2.3
    (utils_vad.py), minus the `visualize_probs` plot. Do not "tidy" it: its
    padding/merging rules are what turn probabilities into the stamps
    `_vad_filter` cuts on, and they must match the torch path byte for byte.
  - `models/silero_vad.onnx` is silero-vad 6.2.3's `data/silero_vad.onnx`
    (sha256 1a153a22…8788e3). MIT, Silero Team — models/SILERO_VAD_LICENSE.

Equivalence was measured, not assumed: tests/test_silero_onnx.py pins the
timestamp logic, and the torch-vs-onnx comparison over real library audio is
recorded in the PR that introduced this file.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np

MODEL_PATH = Path(__file__).resolve().parent / "models" / "silero_vad.onnx"
SAMPLE_RATE = 16000
WINDOW = 512   # samples per model call at 16 kHz
CONTEXT = 64   # trailing samples of the previous window prepended to the next


class SileroVAD:
    """Stateful per-window speech probability, as silero's OnnxWrapper."""

    def __init__(self, path: Path | str = MODEL_PATH):
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        # Same as silero: one thread each. The model is tiny and called once per
        # 32 ms window; thread fan-out costs more than it buys.
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"], sess_options=opts)
        self.reset_states()

    def reset_states(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self._context, chunk.reshape(1, WINDOW).astype(np.float32, copy=False)], axis=1)
        out, self._state = self.session.run(None, {
            "input": x, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)})
        self._context = x[:, -CONTEXT:]
        return float(out.reshape(-1)[0])


def speech_probs(audio: np.ndarray, model: SileroVAD) -> List[float]:
    """One probability per 512-sample window; the last window is zero-padded."""
    model.reset_states()
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    probs = []
    for start in range(0, len(audio), WINDOW):
        chunk = audio[start:start + WINDOW]
        if len(chunk) < WINDOW:
            chunk = np.pad(chunk, (0, WINDOW - len(chunk)))
        probs.append(model(chunk))
    return probs


def get_speech_timestamps(audio: np.ndarray, model: SileroVAD, *, sampling_rate: int = SAMPLE_RATE,
                          **kwargs) -> List[dict]:
    """silero's get_speech_timestamps for 16 kHz float32 numpy audio."""
    if sampling_rate != SAMPLE_RATE:
        raise ValueError("SileroVAD runs at 16000 Hz only, got %r" % (sampling_rate,))
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    return get_speech_timestamps_from_probs(
        speech_probs(audio, model), sampling_rate=SAMPLE_RATE,
        audio_length_samples=len(audio), **kwargs)


# ── vendored from silero-vad 6.2.3 utils_vad.py (MIT) — verbatim below ────────
def get_speech_timestamps_from_probs(speech_probs: List[float],
                                     sampling_rate: int = 16000,
                                     threshold: float = 0.5,
                                     min_speech_duration_ms: int = 250,
                                     max_speech_duration_s: float = float('inf'),
                                     min_silence_duration_ms: int = 100,
                                     speech_pad_ms: int = 30,
                                     return_seconds: bool = False,
                                     time_resolution: int = 1,
                                     visualize_probs: bool = False,
                                     neg_threshold: float = None,
                                     min_silence_at_max_speech: int = 98,
                                     use_max_poss_sil_at_max_speech: bool = True,
                                     audio_length_samples: int = None,
                                     step: int = 1):
    """
    Convert a sequence of per-frame speech probabilities into speech timestamps.

    Parameters
    ----------
    speech_probs: List[float]
        Per-frame speech probabilities, one per window_size_samples frame,
        in order. window_size_samples is 512 for 16000 Hz and 256 for 8000 Hz.

    audio_length_samples: int (default - None)
        Total number of samples in the original audio (at ``sampling_rate``).
        If None, it is estimated as len(speech_probs) * window_size_samples.

    step: int (default - 1)
        Decimation step applied to the input audio before framing (used when
        the caller down-sampled a multiple-of-16000 rate to 16000). Output
        sample coordinates are multiplied by ``step`` to map back.

    All other parameters match ``get_speech_timestamps``.

    Returns
    ----------
    speeches: list of dicts
        list containing ends and beginnings of speech chunks (samples or seconds based on return_seconds)
    """
    window_size_samples = 512 if sampling_rate == 16000 else 256
    if audio_length_samples is None:
        audio_length_samples = len(speech_probs) * window_size_samples

    min_speech_samples = sampling_rate * min_speech_duration_ms / 1000
    speech_pad_samples = sampling_rate * speech_pad_ms / 1000
    max_speech_samples = sampling_rate * max_speech_duration_s - window_size_samples - 2 * speech_pad_samples
    min_silence_samples = sampling_rate * min_silence_duration_ms / 1000
    min_silence_samples_at_max_speech = sampling_rate * min_silence_at_max_speech / 1000

    triggered = False
    speeches = []
    current_speech = {}

    if neg_threshold is None:
        neg_threshold = max(threshold - 0.15, 0.01)
    temp_end = 0  # to save potential segment end (and tolerate some silence)
    prev_end = next_start = 0  # to save potential segment limits in case of maximum segment size reached
    possible_ends = []

    for i, speech_prob in enumerate(speech_probs):
        cur_sample = window_size_samples * i

        # If speech returns after a temp_end, record candidate silence if long enough and clear temp_end
        if (speech_prob >= threshold) and temp_end:
            sil_dur = cur_sample - temp_end
            if sil_dur > min_silence_samples_at_max_speech:
                possible_ends.append((temp_end, sil_dur))
            temp_end = 0
            if next_start < prev_end:
                next_start = cur_sample

        # Start of speech
        if (speech_prob >= threshold) and not triggered:
            triggered = True
            current_speech['start'] = cur_sample
            continue

        # Max speech length reached: decide where to cut
        if triggered and (cur_sample - current_speech['start'] > max_speech_samples):
            if use_max_poss_sil_at_max_speech and possible_ends:
                prev_end, dur = max(possible_ends, key=lambda x: x[1])  # use the longest possible silence segment in the current speech chunk
                current_speech['end'] = prev_end
                speeches.append(current_speech)
                current_speech = {}
                next_start = prev_end + dur

                if next_start < prev_end + cur_sample:  # previously reached silence (< neg_thres) and is still not speech (< thres)
                    current_speech['start'] = next_start
                else:
                    triggered = False
                prev_end = next_start = temp_end = 0
                possible_ends = []
            else:
                # Legacy max-speech cut (use_max_poss_sil_at_max_speech=False): prefer last valid silence (prev_end) if available
                if prev_end:
                    current_speech['end'] = prev_end
                    speeches.append(current_speech)
                    current_speech = {}
                    if next_start < prev_end:
                        triggered = False
                    else:
                        current_speech['start'] = next_start
                    prev_end = next_start = temp_end = 0
                    possible_ends = []
                else:
                    # No prev_end -> fallback to cutting at current sample
                    current_speech['end'] = cur_sample
                    speeches.append(current_speech)
                    current_speech = {}
                    prev_end = next_start = temp_end = 0
                    triggered = False
                    possible_ends = []
                    continue

        # Silence detection while in speech
        if (speech_prob < neg_threshold) and triggered:
            if not temp_end:
                temp_end = cur_sample
            sil_dur_now = cur_sample - temp_end

            if not use_max_poss_sil_at_max_speech and sil_dur_now > min_silence_samples_at_max_speech:  # condition to avoid cutting in very short silence
                prev_end = temp_end

            if sil_dur_now < min_silence_samples:
                continue
            else:
                current_speech['end'] = temp_end
                if (current_speech['end'] - current_speech['start']) > min_speech_samples:
                    speeches.append(current_speech)
                current_speech = {}
                prev_end = next_start = temp_end = 0
                triggered = False
                possible_ends = []
                continue

    if current_speech and (audio_length_samples - current_speech['start']) > min_speech_samples:
        current_speech['end'] = audio_length_samples
        speeches.append(current_speech)

    for i, speech in enumerate(speeches):
        if i == 0:
            speech['start'] = int(max(0, speech['start'] - speech_pad_samples))
        if i != len(speeches) - 1:
            silence_duration = speeches[i+1]['start'] - speech['end']
            if silence_duration < 2 * speech_pad_samples:
                speech['end'] += int(silence_duration // 2)
                speeches[i+1]['start'] = int(max(0, speeches[i+1]['start'] - silence_duration // 2))
            else:
                speech['end'] = int(min(audio_length_samples, speech['end'] + speech_pad_samples))
                speeches[i+1]['start'] = int(max(0, speeches[i+1]['start'] - speech_pad_samples))
        else:
            speech['end'] = int(min(audio_length_samples, speech['end'] + speech_pad_samples))

    if return_seconds:
        audio_length_seconds = audio_length_samples / sampling_rate
        for speech_dict in speeches:
            speech_dict['start'] = max(round(speech_dict['start'] / sampling_rate, time_resolution), 0)
            speech_dict['end'] = min(round(speech_dict['end'] / sampling_rate, time_resolution), audio_length_seconds)
    elif step > 1:
        for speech_dict in speeches:
            speech_dict['start'] *= step
            speech_dict['end'] *= step


    return speeches
