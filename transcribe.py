from __future__ import annotations

import argparse
import bisect
import json
import os
import subprocess
import tempfile
import threading
import time
import platform
from pathlib import Path

import numpy as np
import soundfile as sf
from silero_vad import load_silero_vad, get_speech_timestamps
import torch

import whisper_guard
import zh_convert

from config import (
    WHISPER_MODEL,
    CUSTOM_VOCABULARY,
    VOCABULARY_FILE,
    FILTER_WORDS,
    WHISPER_GUARD_DEFAULT_MODE,
    WHISPER_GUARD_LAYERS,
    OLLAMA_CHAT_MODEL,
    FFMPEG_PATH,
    DIARIZATION_ENABLED,
    PYANNOTE_TOKEN,
    VAD_THRESHOLD,
)
from llm import chat
NO_SPEECH_THRESHOLD = 0.6
DEFAULT_LANGUAGE = "zh"
WHISPER_LANGUAGE_HINT = None
WHISPER_LLM_MODEL = "qwen2.5:14b"
VAD_ENABLED = True
LLM_POLISH = True

# Polish is the slowest thing in the pipeline: ~3-4 chars/s locally, so a whole
# transcript in one call blows any sane timeout. Chunked, with a ceiling on the
# total — the ceiling matters because polish runs inside the shared ingest slot,
# so an unbounded retry would make the queue worse than the silent failure it
# replaces. Both are overridable for a machine with a faster box behind Ollama.
_LLM_POLISH_CHUNK_CHARS = 300
_LLM_POLISH_TIMEOUT_S = int(os.getenv("ARKIV_LLM_POLISH_TIMEOUT", "300"))
_LLM_POLISH_BUDGET_S = int(os.getenv("ARKIV_LLM_POLISH_BUDGET", "900"))

# ── Platform Detection ───────────────────────────────────────────────────────
_USE_MLX = platform.system() == "Darwin" and platform.machine() == "arm64"


def _non_mac_backend() -> str:
    """Non-Mac transcription backend. Defaults to faster-whisper because
    whisperx 3.8.5 dropped the per-call ASR options and pulls torchcodec, whose
    DLLs fail to load on the CUDA box. Set ARKIV_TRANSCRIBE_BACKEND=whisperx to
    force the legacy path."""
    return os.getenv("ARKIV_TRANSCRIBE_BACKEND", "faster-whisper").strip().lower()

# ── Model Singletons ────────────────────────────────────────────────────────
WHISPER_GUARD_ACTIVE_MODE = WHISPER_GUARD_DEFAULT_MODE
WHISPER_GUARD_ACTIVE_LAYER = WHISPER_GUARD_LAYERS[WHISPER_GUARD_ACTIVE_MODE]
WHISPER_MODEL = WHISPER_GUARD_ACTIVE_LAYER["mlx_whisper"]["path_or_hf_repo"] if _USE_MLX else WHISPER_GUARD_ACTIVE_LAYER["model"]
WHISPER_LANGUAGE_HINT = WHISPER_GUARD_ACTIVE_LAYER["language_hint"]
WHISPER_LLM_MODEL = WHISPER_GUARD_ACTIVE_LAYER["llm_model"] or OLLAMA_CHAT_MODEL
VAD_ENABLED = WHISPER_GUARD_ACTIVE_LAYER["vad_enabled"]
LLM_POLISH = WHISPER_GUARD_ACTIVE_LAYER["llm_polish"]

def _coerce_whisper_guard_mode(raw_mode):
    if raw_mode is None:
        return None
    if isinstance(raw_mode, int):
        return raw_mode if raw_mode in WHISPER_GUARD_LAYERS else None
    raw_mode = str(raw_mode).strip()
    if not raw_mode:
        return None
    try:
        value = int(raw_mode)
    except ValueError:
        path = Path(raw_mode)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if isinstance(payload, dict):
                for key in ("baseline_mode", "mode", "layer"):
                    if key in payload:
                        try:
                            value = int(payload[key])
                            break
                        except (TypeError, ValueError):
                            continue
                else:
                    return None
            else:
                return None
        else:
            return None
    return value if value in WHISPER_GUARD_LAYERS else None

def _resolve_whisper_guard_mode(cli_mode=None):
    env_mode = _coerce_whisper_guard_mode(os.getenv("ARKIV_WHISPER_GUARD_LAYERS"))
    if env_mode is not None:
        return env_mode
    cli_mode = _coerce_whisper_guard_mode(cli_mode)
    if cli_mode is not None:
        return cli_mode
    return WHISPER_GUARD_DEFAULT_MODE

def _apply_whisper_guard_mode(mode):
    global WHISPER_GUARD_ACTIVE_MODE, WHISPER_GUARD_ACTIVE_LAYER
    global WHISPER_MODEL, WHISPER_LANGUAGE_HINT, WHISPER_LLM_MODEL
    global VAD_ENABLED, LLM_POLISH
    WHISPER_GUARD_ACTIVE_MODE = mode
    WHISPER_GUARD_ACTIVE_LAYER = WHISPER_GUARD_LAYERS.get(mode, WHISPER_GUARD_LAYERS[WHISPER_GUARD_DEFAULT_MODE])
    WHISPER_MODEL = WHISPER_GUARD_ACTIVE_LAYER["mlx_whisper"]["path_or_hf_repo"] if _USE_MLX else WHISPER_GUARD_ACTIVE_LAYER["model"]
    WHISPER_LANGUAGE_HINT = WHISPER_GUARD_ACTIVE_LAYER["language_hint"]
    WHISPER_LLM_MODEL = WHISPER_GUARD_ACTIVE_LAYER["llm_model"] or OLLAMA_CHAT_MODEL
    VAD_ENABLED = WHISPER_GUARD_ACTIVE_LAYER["vad_enabled"]
    LLM_POLISH = WHISPER_GUARD_ACTIVE_LAYER["llm_polish"]
    return WHISPER_GUARD_ACTIVE_LAYER

def _current_whisper_guard_backend():
    return "mlx_whisper" if _USE_MLX else "whisperx"

def _current_whisper_guard_settings():
    # Returns the full active layer. Both _transcribe_mlx and _transcribe_whisperx
    # read top-level keys (beam_size, condition_on_previous_text, …) AND the
    # backend sub-dict via layer["whisperx"]/layer["mlx_whisper"], so the full
    # layer is required — indexing by backend here caused KeyError on whisperx.
    return WHISPER_GUARD_ACTIVE_LAYER

def _optional_option(opts, key, value):
    if value is not None:
        opts[key] = value

_whisper_loaded = False
_warm_up_lock = threading.Lock()  # audit L7: guard lazy model load against concurrent callers
_fw_model = None  # faster-whisper model instance
_whisperx_model = None  # WhisperX model instance
_vad_model = None  # Silero VAD model instance


_apply_whisper_guard_mode(_resolve_whisper_guard_mode(None))

def _get_vad_model():
    """Lazy-load Silero VAD model."""
    global _vad_model
    if _vad_model is None:
        _vad_model = load_silero_vad()
    return _vad_model


def _vad_filter(wav_path: str, sample_rate: int = 16000):
    """Run Silero VAD, returning (wav_to_transcribe, offset_map).

    The second element is the whole point. VAD physically removes silence, so the
    audio whisper reads is shorter than the file the user has open and every
    timestamp it reports is in *gapless-speech* time. `offset_map` is what
    translates back:

        [(trimmed_start, trimmed_end, original_start), ...]   # seconds

    one triple per kept chunk, in concatenation order. `None` means the two clocks
    are already the same (VAD off, sample-rate skip) and the caller must not remap.

    Returns `(None, None)` when there is no speech at all.

    Before this returned a bare path, and the stamps — the only record of where the
    kept speech came from — went out of scope. Nothing downstream could recover it:
    the trimmed wav is unlinked moments later, so the mapping had to be built here
    or not at all.
    """
    if not VAD_ENABLED:
        return wav_path, None

    audio, sr = sf.read(wav_path, dtype="float32")
    if sr != sample_rate:
        return wav_path, None  # safety: skip VAD if sample rate mismatch

    tensor = torch.from_numpy(audio)
    stamps = get_speech_timestamps(tensor, _get_vad_model(),
                                   sampling_rate=sample_rate,
                                   min_silence_duration_ms=300,
                                   speech_pad_ms=150,
                                   threshold=VAD_THRESHOLD)
    if not stamps:
        return None, None  # no speech at all

    # Concatenate speech segments, recording where each one came from.
    #
    # The cursor advances by the chunk's ACTUAL length rather than by
    # (end - start) arithmetic on the stamp: `speech_pad_ms` widens stamps and can
    # make neighbours overlap, and a clamped slice is then shorter than the stamp
    # claims. Measuring the slice keeps the map exact by construction.
    chunks, offset_map, cursor = [], [], 0.0
    for s in stamps:
        chunk = audio[s["start"]:s["end"]]
        if not len(chunk):
            continue  # a zero-width stamp would add a triple that swallows later lookups
        dur = len(chunk) / float(sample_rate)
        offset_map.append((cursor, cursor + dur, s["start"] / float(sample_rate)))
        cursor += dur
        chunks.append(chunk)
    if not chunks:
        return None, None
    speech = np.concatenate(chunks)

    _fd, out = tempfile.mkstemp(suffix=".wav"); os.close(_fd)
    try:
        sf.write(out, speech, sample_rate)
    except Exception:
        Path(out).unlink(missing_ok=True)  # audit L4: don't leak the temp wav on write failure
        raise
    kept = len(speech) / max(len(audio), 1)
    print(f"  [VAD] kept {kept:.0%} of audio ({len(stamps)} segments)", flush=True)
    return out, offset_map


def _remap_vad_time(t: float, offset_map, ends) -> float:
    """One gapless-speech second → the second it came from in the source media.

    The trimmed timeline is contiguous by construction (`trimmed_end[i]` ==
    `trimmed_start[i+1]`), so `t` can never land in a gap and the first chunk whose
    end is at or past `t` is the right one. `bisect` rather than a scan because a
    long clip is hundreds of chunks and thousands of words.

    Past the last chunk we extrapolate instead of clamping. Whisper pads to 30 s
    windows and can report an `end` a hair beyond the audio; clamping would stack
    every trailing cue on one instant, which is worse than a cue that runs slightly
    long.
    """
    i = bisect.bisect_left(ends, t)
    if i >= len(offset_map):
        i = len(offset_map) - 1
    trimmed_start, _trimmed_end, original_start = offset_map[i]
    return original_start + (t - trimmed_start)


def _remap_result_times(text, lang, segments, words, offset_map):
    """Rewrite segment and word times from gapless-speech time to media time.

    Returns new dicts rather than mutating: `speaker_id` and any future key rides
    along via `{**s}`, and a pure function is testable without building a whole
    transcription. `offset_map is None` (whisperx, VAD off) is the identity.
    """
    if not offset_map:
        return text, lang, segments, words

    ends = [o[1] for o in offset_map]

    def _fix(item):
        return {
            **item,
            "start": round(_remap_vad_time(float(item.get("start", 0) or 0), offset_map, ends), 3),
            "end": round(_remap_vad_time(float(item.get("end", 0) or 0), offset_map, ends), 3),
        }

    return text, lang, [_fix(s) for s in segments or []], [_fix(w) for w in words or []]


def warm_up():
    """Pre-load Whisper model into memory. Call once before batch processing."""
    global _whisper_loaded, _fw_model, _whisperx_model
    if _whisper_loaded:
        return

    # audit L7: double-checked locking — concurrent retranscribe requests used
    # to both pass the flag check and load the model twice (RAM spike).
    with _warm_up_lock:
        if _whisper_loaded:
            return

        if _USE_MLX:
            import mlx_whisper
            import numpy as np
            _fd, silence = tempfile.mkstemp(suffix=".wav"); os.close(_fd)
            try:
                subprocess.run([
                    FFMPEG_PATH, "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", "1", "-loglevel", "error", silence, "-y"
                ], capture_output=True)
                mlx_whisper.transcribe(silence, path_or_hf_repo=WHISPER_MODEL, language="zh")
            except Exception:
                pass
            finally:
                Path(silence).unlink(missing_ok=True)
        elif _non_mac_backend() == "whisperx":
            import whisperx
            _whisperx_model = whisperx.load_model(
                WHISPER_MODEL,
                "cuda",
                compute_type="float16",
            )
            print("  [whisperx on cuda]", flush=True)
        else:
            from faster_whisper import WhisperModel
            _fw_model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
            print("  [faster-whisper on cuda]", flush=True)

        _whisper_loaded = True
        print("  [whisper model loaded]", flush=True)


def transcribe(media_path: str, language=None) -> tuple:
    """
    Transcribe audio from a media file.
    Returns (transcript_text, language, segments_list, words_list).
    segments_list: [{"start": float, "end": float, "text": str}, ...]
    words_list: [{"word": str, "start": float, "end": float, "score": float}, ...]
    Returns ("", "", [], []) if no speech detected.
    """
    if language is None:
        language = WHISPER_LANGUAGE_HINT or DEFAULT_LANGUAGE
    wav = _to_wav(media_path)
    if not wav:
        return "", "", [], []

    # None means "the backend read the original audio, so its clock is already
    # media time". The whisperx branch below never trims, and so never sets it.
    offset_map = None
    try:
        if _USE_MLX:
            vad_wav, offset_map = _vad_filter(wav)
            if vad_wav is None:
                return "", "", [], []
            try:
                result = _transcribe_mlx(vad_wav, language)
            finally:
                if vad_wav != wav:
                    Path(vad_wav).unlink(missing_ok=True)
        elif _non_mac_backend() == "whisperx":
            result = _transcribe_whisperx(wav, language)
        else:
            vad_wav, offset_map = _vad_filter(wav)
            if vad_wav is None:
                return "", "", [], []
            try:
                result = _transcribe_faster_whisper(vad_wav, language)
            finally:
                if vad_wav != wav:
                    Path(vad_wav).unlink(missing_ok=True)
    finally:
        Path(wav).unlink(missing_ok=True)
    # Put the timestamps back on the media timeline BEFORE anything else sees them.
    # Everything downstream — SRT cue times, /api/media/{id}/segments, the MCP
    # get_transcript contract, click-to-seek — reads these as "seconds from the
    # start of the clip", so gapless-speech time must not escape this function.
    result = _remap_result_times(*result, offset_map=offset_map)
    # Phase 9.8b: whisper emits Simplified for zh — store Taiwan Traditional so the
    # search index / UI / every export are Traditional (write-path; see zh_convert).
    return zh_convert.convert_result(*result)

def _custom_terms() -> list:
    """Merged hotword list: comma-separated ARKIV_CUSTOM_VOCABULARY env first,
    then one-per-line VOCABULARY_FILE (blank lines / '#' comments ignored).
    Order preserved, duplicates dropped — env terms win position."""
    terms = [t.strip() for t in CUSTOM_VOCABULARY.split(",") if t.strip()]
    if VOCABULARY_FILE:
        try:
            with open(VOCABULARY_FILE, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        terms.append(line)
        except OSError:
            pass  # missing / unreadable file is non-fatal — just use env terms
    # Phase 9.6: the per-project correction dictionary is one source for two
    # paths — its pre-flagged `to` terms feed the hotword list here (the same
    # rows also drive post-hoc recorrect). Hot-read per call like vocabulary.txt.
    try:
        import corrections
        terms.extend(corrections.hotword_terms())
    except Exception:
        pass  # dictionary is optional — never block transcription on it
    seen = set()
    out = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _build_initial_prompt() -> str:
    """Build whisper initial_prompt from the merged custom vocabulary (env + file)."""
    terms = _custom_terms()
    return "、".join(terms) if terms else ""


def _transcribe_mlx(wav: str, language: str) -> tuple:
    """Transcribe using mlx-whisper (Apple Silicon)."""
    import mlx_whisper
    initial_prompt = _build_initial_prompt()
    layer = _current_whisper_guard_settings()
    opts = dict(
        path_or_hf_repo=WHISPER_MODEL,
        language=language,
        word_timestamps=True,
        condition_on_previous_text=layer["condition_on_previous_text"],
        no_speech_threshold=NO_SPEECH_THRESHOLD,
        # beam_size removed: mlx-whisper 0.4.3 does not support beam search
    )
    _optional_option(opts, "compression_ratio_threshold", layer["compression_ratio_threshold"])
    _optional_option(opts, "logprob_threshold", layer["logprob_threshold"])
    if initial_prompt:
        opts["initial_prompt"] = initial_prompt
    result = mlx_whisper.transcribe(wav, **opts)
    text = result.get("text", "").strip()
    lang = result.get("language", language)
    raw_segments = result.get("segments", [])
    # mlx-whisper has been asked for word_timestamps=True since this function was
    # written, and the words were thrown away — every clip ingested on a Mac stored
    # words_json = NULL. So `/api/media/{id}/remotion-props` returns nothing useful
    # on the primary platform, and MCP's opt-in word list is always empty there.
    #
    # Shape differs from faster-whisper: mlx returns plain dicts on the segment
    # (`{"word", "start", "end", "probability"}`) where faster-whisper returns
    # objects. Normalised to faster-whisper's output shape here so `_postprocess`
    # and every consumer see one contract — the same reason `probability` is
    # renamed `score`.
    all_words = []
    for seg in raw_segments:
        for word in (seg.get("words") or []):
            start, end = word.get("start"), word.get("end")
            if start is None or end is None:
                continue
            all_words.append({
                "word": (word.get("word") or "").strip(),
                "start": round(float(start), 3),
                "end": round(float(end), 3),
                "score": round(float(word.get("probability") or 0.0), 3),
            })
    return _postprocess(text, lang, raw_segments, language, words=all_words, wav_path=wav)

def _transcribe_faster_whisper(wav: str, language: str) -> tuple:
    """Transcribe using faster-whisper (non-Mac / CUDA).

    faster-whisper's WhisperModel.transcribe() still accepts the whisper-guard
    layer options per call (beam_size / condition_on_previous_text /
    compression_ratio_threshold / log_prob_threshold / initial_prompt /
    word_timestamps), so this is a clean drop-in for the whisperx path without
    pulling torchcodec.
    """
    global _fw_model
    if _fw_model is None:
        warm_up()

    initial_prompt = _build_initial_prompt()
    layer = _current_whisper_guard_settings()
    opts = {
        "language": language,
        "beam_size": layer["beam_size"],
        "condition_on_previous_text": layer["condition_on_previous_text"],
        "word_timestamps": True,
    }
    _optional_option(opts, "compression_ratio_threshold", layer["compression_ratio_threshold"])
    _optional_option(opts, "log_prob_threshold", layer["whisperx"]["log_prob_threshold"])
    if initial_prompt:
        opts["initial_prompt"] = initial_prompt

    segments, info = _fw_model.transcribe(wav, **opts)

    parsed_segments = []
    all_words = []
    for seg in segments:  # generator — iterating it runs the actual inference
        parsed_segments.append({
            "text": (seg.text or "").strip(),
            "start": seg.start,
            "end": seg.end,
            "no_speech_prob": getattr(seg, "no_speech_prob", 0),
            "avg_logprob": getattr(seg, "avg_logprob", 0),
            "compression_ratio": getattr(seg, "compression_ratio", 1),
        })
        for word in (seg.words or []):
            if word.start is not None and word.end is not None:
                all_words.append({
                    "word": (word.word or "").strip(),
                    "start": round(float(word.start), 3),
                    "end": round(float(word.end), 3),
                    "score": round(float(word.probability), 3),
                })

    text = " ".join(s["text"] for s in parsed_segments).strip()
    lang = (info.language if info is not None else None) or language
    return _postprocess(text, lang, parsed_segments, language, words=all_words, wav_path=wav)

def _transcribe_whisperx(wav: str, language: str) -> tuple:
    """Transcribe using WhisperX (CUDA) with forced alignment."""
    global _whisperx_model
    if _whisperx_model is None:
        warm_up()

    import whisperx

    initial_prompt = _build_initial_prompt()
    layer = _current_whisper_guard_settings()
    transcribe_opts = {
        "batch_size": layer["whisperx"]["batch_size"],
        "beam_size": layer["beam_size"],
        "language": language,
        "condition_on_previous_text": layer["condition_on_previous_text"],
    }
    _optional_option(transcribe_opts, "compression_ratio_threshold", layer["compression_ratio_threshold"])
    _optional_option(transcribe_opts, "log_prob_threshold", layer["whisperx"]["log_prob_threshold"])
    if initial_prompt:
        transcribe_opts["initial_prompt"] = initial_prompt

    audio = whisperx.load_audio(wav)
    result = _whisperx_model.transcribe(audio, **transcribe_opts)
    lang = result.get("language", language) or language

    align_model, align_meta = whisperx.load_align_model(
        language_code=lang,
        device="cuda",
    )
    result = whisperx.align(
        result["segments"],
        align_model,
        align_meta,
        audio,
        "cuda",
        return_char_alignments=False,
    )

    segments = []
    all_words = []
    for seg in result.get("segments", []):
        segments.append({
            "text": seg.get("text", "").strip(),
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "no_speech_prob": seg.get("no_speech_prob", 0),
            "avg_logprob": seg.get("avg_logprob", 0),
            "compression_ratio": seg.get("compression_ratio", 1),
        })
        for word in seg.get("words", []):
            if "start" in word and "end" in word:
                all_words.append({
                    "word": word.get("word", "").strip(),
                    "start": round(float(word["start"]), 3),
                    "end": round(float(word["end"]), 3),
                    "score": round(float(word.get("score", 0)), 3),
                })

    text = " ".join(seg["text"] for seg in segments).strip()
    return _postprocess(text, lang, segments, language, words=all_words, wav_path=wav)

def _attach_speaker_ids(timed_segments: list, wav_path: str) -> list:
    """Attach a `speaker_id` to each segment via the speaker-align package (A4).

    Diarizes `wav_path` — which MUST be the same (VAD-filtered) audio the segments
    were timed against, or the labels won't line up — and tags each segment with
    whichever speaker turn overlaps it most. Soft dependency + soft failure: if
    speaker-align/pyannote isn't installed, no token is set, or diarization errors,
    the segments are returned unchanged (transcription must never break because of
    an optional label).
    """
    if not timed_segments or not wav_path:
        return timed_segments
    if not PYANNOTE_TOKEN:
        print("[diarize] skipped: no ARKIV_PYANNOTE_TOKEN set", flush=True)
        return timed_segments
    try:
        from speaker_align import get_diarizer, align_speakers_to_transcript
    except ImportError:
        print("[diarize] skipped: speaker-align not installed "
              "(pip install 'speaker-align[pyannote]')", flush=True)
        return timed_segments
    try:
        diarizer = get_diarizer(auth_token=PYANNOTE_TOKEN)
        result = diarizer.diarize(wav_path)
        aligned = align_speakers_to_transcript(result.segments, timed_segments)
        for seg in aligned:
            # arkiv's segment contract uses `speaker_id`; speaker-align emits `speaker`.
            seg["speaker_id"] = seg.pop("speaker", "")
        return aligned
    except Exception as e:  # noqa: BLE001 — an optional label must never break transcribe
        print("[diarize] skipped (%s): %s" % (type(e).__name__, e), flush=True)
        return timed_segments


def _postprocess(text: str, lang: str, segments: list, language: str,
                 words: list = None, wav_path: str = None) -> tuple:
    """Shared post-processing: anti-hallucination + LLM polish (+ optional A4
    speaker diarization when config.DIARIZATION_ENABLED and a wav_path is given).
    Returns (text, lang, clean_segments, words) where clean_segments has
    start/end/text (+ speaker_id when diarization ran)."""
    if not segments:
        return text, lang, [], words or []

    # Guard 1: ALL segments are silence → no speech
    avg_no_speech = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
    if avg_no_speech > NO_SPEECH_THRESHOLD:
        return "", lang, [], []

    # Guard 2: Per-segment filtering
    good_segments = []
    timed_segments = []
    for s in segments:
        seg_text = s.get("text", "").strip()
        if not seg_text:
            continue
        if s.get("no_speech_prob", 0) > 0.8:
            continue
        # Dynamic logprob threshold: short segments (<1.6s) are more likely hallucinations
        duration = s.get("end", 0) - s.get("start", 0) if "start" in s and "end" in s else None
        logprob_thresh = -1.7 if duration is not None and duration < 1.6 else -1.5
        if s.get("avg_logprob", 0) < logprob_thresh:
            continue
        if s.get("compression_ratio", 1) > 3.0:
            continue
        good_segments.append(seg_text)
        # Preserve timing for SRT/VTT export
        if "start" in s and "end" in s:
            timed_segments.append({
                "start": round(float(s["start"]), 3),
                "end": round(float(s["end"]), 3),
                "text": seg_text,
            })

    if not good_segments:
        return "", lang, [], []

    filtered_text = " ".join(good_segments).strip()

    # Guard 3: Text-level repetition
    if _is_repetitive(filtered_text):
        return "", lang, [], []

    # Guard 4: Character-level repetition
    if _has_char_loops(filtered_text):
        filtered_text = _remove_char_loops(filtered_text)

    # Step 4.5: Filter dictionary — remove configured filler words
    if FILTER_WORDS:
        filter_list = [w.strip() for w in FILTER_WORDS.split(",") if w.strip()]
        for word in filter_list:
            filtered_text = filtered_text.replace(word, "")
        # Also clean timed segments
        for ts in timed_segments:
            for word in filter_list:
                ts["text"] = ts["text"].replace(word, "")
            ts["text"] = ts["text"].strip()
        timed_segments = [ts for ts in timed_segments if ts["text"]]
        # Clean up double spaces
        import re
        filtered_text = re.sub(r'\s{2,}', ' ', filtered_text).strip()

    # Step 5: LLM polish
    if LLM_POLISH and len(filtered_text) > 10:
        filtered_text = _llm_polish_batched(filtered_text, language)

    # Step 6: words_json reconciliation. The per-segment filter above rebuilt
    # timed_segments from the SURVIVING segments, but `words` still carries every
    # word from the raw segment list — including words that belonged to segments
    # dropped as hallucinations (silence / low-logprob / high compression). Keep
    # only words whose midpoint falls inside a kept segment's time range, so
    # words_json can't reintroduce content the text/segment filters already removed
    # (frame-accurate cutting and subtitle rendering read words_json directly).
    if words:
        kept_ranges = [(ts["start"], ts["end"]) for ts in timed_segments]
        eps = 0.05  # tolerance for start/end rounded to 3 dp above vs raw word times

        def _in_kept_segment(w):
            ws = w.get("start")
            if ws is None:
                return False
            we = w.get("end")
            mid = ws if we is None else (ws + we) / 2.0
            return any(lo - eps <= mid <= hi + eps for lo, hi in kept_ranges)

        words = [w for w in words if _in_kept_segment(w)]

    # A4: tag each segment with a speaker_id (optional, gated, soft-fail). Runs on
    # the same VAD-filtered wav the segments were timed against (passed by each
    # backend), so the labels line up with the timecodes.
    if wav_path and DIARIZATION_ENABLED:
        timed_segments = _attach_speaker_ids(timed_segments, wav_path)

    return filtered_text, lang, timed_segments, words or []


# Text-level hallucination filters live in the standalone whisper_guard package
# (Phase 10, published to PyPI as whisper-guard>=0.3). v0.3 refactored the v0.1
# free functions into WhisperGuard methods, so we bind a default-config instance
# here. Behaviour is identical to the old free functions (verified by parity
# test); remove_char_loops now returns (text, count) — we keep the str-returning
# wrapper the callers (_remove_char_loops below) expect.
_wg = whisper_guard.WhisperGuard()
_is_repetitive = _wg.is_repetitive
_has_char_loops = _wg.has_char_loops


def _remove_char_loops(text):
    cleaned, _ = _wg.remove_char_loops(text)
    return cleaned


_ollama_warm = False


def warm_up_ollama():
    global _ollama_warm
    if _ollama_warm:
        return
    try:
        chat("hi", model=OLLAMA_CHAT_MODEL)
        _ollama_warm = True
    except Exception:
        pass


def _llm_polish(text: str, language: str = "zh") -> str:
    MODEL = WHISPER_LLM_MODEL

    lang_name = {"zh": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어"}.get(language, language)

    prompt = f"""你是一個逐字稿校正助手。以下是語音辨識（Whisper）的原始輸出，可能有錯字、同音字錯誤、人名地名錯誤、缺少標點。

請校正以下逐字稿，規則：
1. 修正明顯的同音字錯誤（例如「蕭希」→「小熙」）
2. 補上適當的標點符號（句號、逗號、問號）
3. 不要改變原意、不要增刪內容
4. 保持口語化，不要改成書面語
5. 語言：{lang_name}
6. 只輸出校正後的文字，不要加任何說明

原始逐字稿：
{text}

校正後："""

    try:
        result = chat(prompt, model=MODEL, timeout=_LLM_POLISH_TIMEOUT_S, temperature=0.2)
        polished = result.get("text", "").strip()
        if polished and 0.5 < len(polished) / max(len(text), 1) < 2.0:
            return polished
        print("  [polish] rejected (length ratio out of range), keeping raw text", flush=True)
    except Exception as exc:
        # This used to be a bare `except: pass`, and that silence is the whole
        # reason a 120 s timeout went unnoticed for months: a transcript long
        # enough to blow the budget came back as raw, unpunctuated Whisper output
        # and looked exactly like a transcript the model had declined to improve.
        print(f"  [polish] skipped ({type(exc).__name__}): {exc}", flush=True)
    return text


def _polish_chunks(text: str, max_chars: int = _LLM_POLISH_CHUNK_CHARS) -> list:
    """Greedily pack `text` into ≤max_chars pieces, splitting only on spaces.

    A space is exactly where `_postprocess` joined the kept segments, so a chunk
    boundary can never land inside one — the model always sees whole segments.
    """
    words, chunks, current = text.split(" "), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        chunks.append(current)
    return chunks


def _llm_polish_batched(text: str, language: str = "zh") -> str:
    """Polish a transcript in pieces, under a total time budget.

    Why pieces: qwen2.5:14b runs at roughly 3-4 characters/second locally, so a
    4,000-character transcript needs 20+ minutes in one call. It blew the old
    hardcoded 120 s timeout, the exception was swallowed, and the user silently got
    raw unpunctuated text.

    Why a BUDGET and not just a bigger timeout: raising the timeout alone converts
    a silent 120 s failure into a real 20-70 minute one, held inside the shared
    ingest slot — every other clip in the queue waits behind it. Past the budget the
    remaining chunks are returned raw, which is the same outcome as before but
    bounded and announced.

    The length-ratio guard now applies per chunk, so one bad chunk degrades 300
    characters rather than the whole transcript.
    """
    if not text.strip():
        return text
    chunks = _polish_chunks(text)
    if len(chunks) <= 1:
        return _llm_polish(text, language)

    started, out = time.monotonic(), []
    for i, chunk in enumerate(chunks):
        elapsed = time.monotonic() - started
        if elapsed > _LLM_POLISH_BUDGET_S:
            print(
                f"  [polish] budget reached after {elapsed:.0f}s — "
                f"{len(chunks) - i} of {len(chunks)} chunks kept raw",
                flush=True,
            )
            out.extend(chunks[i:])
            break
        out.append(_llm_polish(chunk, language))
    return " ".join(out)


def _to_wav(media_path: str):
    _fd, out = tempfile.mkstemp(suffix=".wav"); os.close(_fd)
    cmd = [
        FFMPEG_PATH, "-i", media_path,
        "-ac", "1", "-ar", "16000",
        "-map", "a:0",
        "-loglevel", "error",
        out, "-y"
    ]
    # duration-unaware files can hang ffmpeg — bound it so a single bad file can't
    # wedge the whole whisper phase (audit H7).
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        Path(out).unlink(missing_ok=True)
        raise RuntimeError("ffmpeg audio extract timed out (>900s): {0}".format(media_path))
    if r.returncode != 0 or not Path(out).exists() or Path(out).stat().st_size == 0:
        Path(out).unlink(missing_ok=True)  # don't leak the temp wav on failure (H7)
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        err = err[-300:] if err else "(no stderr)"
        # A failed extraction must NOT masquerade as "no speech" (returning empty
        # made ingest record an empty transcript forever and retranscribe overwrite
        # a good one — audit H1/H2). Raise so the caller counts it a real failure.
        raise RuntimeError("ffmpeg audio extract failed rc={0}: {1}".format(r.returncode, err))
    return out


def _whisper_guard_snapshot(mode=None):
    if mode is None:
        mode = WHISPER_GUARD_ACTIVE_MODE
    layer = WHISPER_GUARD_LAYERS.get(mode, WHISPER_GUARD_LAYERS[WHISPER_GUARD_DEFAULT_MODE])
    return {
        "mode": mode,
        "name": layer["name"],
        "model": layer["model"],
        "beam_size": layer["beam_size"],
        "language_hint": layer["language_hint"],
        "vad_enabled": layer["vad_enabled"],
        "condition_on_previous_text": layer["condition_on_previous_text"],
        "compression_ratio_threshold": layer["compression_ratio_threshold"],
        "logprob_threshold": layer["logprob_threshold"],
        "llm_polish": layer["llm_polish"],
        "llm_model": layer["llm_model"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="arkiv Whisper transcription")
    parser.add_argument("audio", nargs="?", help="Audio or video file to transcribe")
    parser.add_argument("--language", default=None, help="Override language hint")
    parser.add_argument("--baseline-mode", type=int, choices=range(5), help="Select Whisper Guard baseline layer (0-4)")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved Whisper Guard layer and exit")
    args = parser.parse_args(argv)

    mode = _resolve_whisper_guard_mode(args.baseline_mode)
    _apply_whisper_guard_mode(mode)

    if args.dry_run or not args.audio:
        print(json.dumps(_whisper_guard_snapshot(mode), indent=2, ensure_ascii=False))
        return 0

    text, lang, segments, words = transcribe(args.audio, language=args.language)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
