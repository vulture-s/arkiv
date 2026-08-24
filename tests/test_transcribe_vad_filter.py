"""`_vad_filter` and the remap that puts whisper's clock back on the media timeline.

VAD physically removes silence before whisper ever sees the audio, so whisper
reports timestamps against a *gapless* file while every other clock in arkiv —
frames, waveform, `<video>.currentTime`, EDL source TC — is original-media time.
Until the commit that added `offset_map`, nothing bridged the two: `_vad_filter`
computed exactly the information needed and then returned a bare path.

The user-visible symptom, reported by an external contributor: clicking a
transcript line seeks early, by the total silence removed before that line, so the
error grows through the clip.

The load-bearing test here is `test_every_segment_window_contains_audio_energy`.
It encodes no arithmetic — it slices the ORIGINAL samples at each returned
timestamp and asserts something is audible there — so it survives refactors of the
mapping and would have failed on day one.
"""
from __future__ import annotations

import wave

import pytest

import transcribe


class _Seg:
    """Minimal faster-whisper segment. Times are in whatever clock the backend saw."""

    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end
        self.no_speech_prob = 0.01
        self.avg_logprob = -0.2
        self.compression_ratio = 1.2
        self.words = []


class _Info:
    def __init__(self, language="zh"):
        self.language = language


class _Model:
    def __init__(self, segments, info):
        self._segments, self._info = segments, info

    def transcribe(self, *args, **kwargs):
        return self._segments, self._info


def _wav_seconds(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ── the fixtures work at all ──────────────────────────────────────────────────

def test_synth_wav_round_trips_through_the_soundfile_fake(synth_wav):
    """If this fails, nothing else in the file means anything."""
    path, _audio, sr, truth = synth_wav([("silence", 1), ("tone", 2), ("silence", 1)])
    assert sr == 16000
    assert _wav_seconds(path) == pytest.approx(4.0, abs=0.01)
    assert truth == [(1.0, 3.0)]


def test_energy_vad_finds_the_speech_we_planted(synth_wav, energy_vad):
    _path, audio, sr, _truth = synth_wav([("silence", 3), ("tone", 1), ("silence", 6)])
    stamps = transcribe.get_speech_timestamps(audio, object(), sampling_rate=sr)
    assert len(stamps) == 1
    assert stamps[0]["start"] / sr == pytest.approx(3.0, abs=0.05)
    assert stamps[0]["end"] / sr == pytest.approx(4.0, abs=0.05)


# ── passthrough branches: the two clocks are already the same ─────────────────

def test_vad_disabled_returns_the_original_path_and_no_map(synth_wav, monkeypatch):
    monkeypatch.setattr(transcribe, "VAD_ENABLED", False)
    path, _audio, _sr, _truth = synth_wav([("tone", 1)])
    assert transcribe._vad_filter(path) == (path, None)


def test_sample_rate_mismatch_returns_the_original_path_and_no_map(synth_wav, monkeypatch):
    """A safety valve: VAD is skipped rather than run against the wrong rate.

    The `None` matters as much as the path — a caller that remapped here would be
    translating a clock that was never compressed.
    """
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, _truth = synth_wav([("tone", 1)])
    assert transcribe._vad_filter(path, sample_rate=48000) == (path, None)


def test_no_speech_returns_none_none(synth_wav, energy_vad, monkeypatch):
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, _truth = synth_wav([("silence", 2)])
    assert transcribe._vad_filter(path) == (None, None)


# ── the trimming branch ───────────────────────────────────────────────────────

def test_trimmed_wav_keeps_only_the_speech(synth_wav, energy_vad, monkeypatch):
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, _truth = synth_wav(
        [("silence", 3), ("tone", 1), ("silence", 4), ("tone", 1), ("silence", 1)]
    )
    out, offset_map = transcribe._vad_filter(path)
    assert out != path
    assert offset_map is not None
    # 2 s of speech out of a 10 s file; `speech_pad_ms=150` widens each kept region
    # a little, so this is a band rather than an equality.
    assert 2.0 <= _wav_seconds(out) <= 2.7
    assert _wav_seconds(path) == pytest.approx(10.0, abs=0.01)


def test_offset_map_records_where_each_kept_chunk_came_from(
    synth_wav, energy_vad, monkeypatch
):
    """The triple that makes the translation possible.

    Two 1-second words at 3 s and 8 s become a ~2-second gapless file, and the map
    says so: chunk 0 sits at the head of the trimmed file and came from ~3 s;
    chunk 1 follows it and came from ~8 s.
    """
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, truth = synth_wav(
        [("silence", 3), ("tone", 1), ("silence", 4), ("tone", 1), ("silence", 1)]
    )
    assert truth == [(3.0, 4.0), (8.0, 9.0)]

    _out, offset_map = transcribe._vad_filter(path)
    assert len(offset_map) == 2

    (t0_start, t0_end, o0), (t1_start, t1_end, o1) = offset_map
    assert t0_start == 0.0
    assert o0 == pytest.approx(3.0, abs=0.2)
    assert o1 == pytest.approx(8.0, abs=0.2)
    # The trimmed timeline is contiguous by construction — no gaps for a lookup
    # to fall into.
    assert t1_start == pytest.approx(t0_end, abs=1e-9)
    assert t1_end > t1_start


def test_zero_width_stamp_is_dropped(synth_wav, monkeypatch):
    """A degenerate stamp would add a triple whose window swallows later lookups."""
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, sr, _truth = synth_wav([("silence", 1), ("tone", 1)])
    monkeypatch.setattr(
        transcribe,
        "get_speech_timestamps",
        lambda *a, **k: [{"start": 100, "end": 100}, {"start": sr, "end": 2 * sr}],
    )
    _out, offset_map = transcribe._vad_filter(path)
    assert len(offset_map) == 1
    assert offset_map[0][2] == pytest.approx(1.0, abs=1e-6)


def test_a_stamp_running_past_the_audio_does_not_desync_the_map(synth_wav, monkeypatch):
    """Why the cursor measures the slice instead of trusting the stamp.

    `speech_pad_ms` widens every stamp, and a stamp near the end can be widened
    past the last sample. numpy silently returns a SHORTER slice; stamp arithmetic
    (`end - start`) does not know that. Trusting the stamp would put every later
    triple's `trimmed_start` beyond where its audio actually sits, and the error
    would accumulate down the file.

    Silero clamps today, which is why this needs an explicit unclamped stamp: the
    defence is against the next VAD, not the current one.
    """
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, audio, sr, _truth = synth_wav([("tone", 1), ("silence", 1), ("tone", 1)])
    over = len(audio) + sr  # one second past the end of the file
    monkeypatch.setattr(
        transcribe,
        "get_speech_timestamps",
        lambda *a, **k: [{"start": 0, "end": sr}, {"start": 2 * sr, "end": over}],
    )

    _out, offset_map = transcribe._vad_filter(path)
    assert len(offset_map) == 2

    # Chunk 1 is really 1 s (3 s of audio minus its 2 s start), not the 2 s the
    # stamp claims — so chunk 1 occupies [1.0, 2.0) of the trimmed timeline.
    assert offset_map[1][0] == pytest.approx(1.0, abs=1e-6)
    assert offset_map[1][1] == pytest.approx(2.0, abs=1e-6)
    assert offset_map[1][2] == pytest.approx(2.0, abs=1e-6)
    # And the map must still describe a contiguous timeline.
    assert offset_map[1][0] == pytest.approx(offset_map[0][1], abs=1e-9)


# ── _remap_vad_time: the arithmetic, pinned ───────────────────────────────────

_MAP = [(0.0, 1.0, 3.0), (1.0, 2.0, 8.0)]
_ENDS = [m[1] for m in _MAP]


@pytest.mark.parametrize(
    "trimmed, original",
    [
        (0.0, 3.0),    # head of the first kept chunk
        (0.5, 3.5),    # inside it
        (1.0, 4.0),    # the boundary belongs to the EARLIER chunk
        (1.001, 8.001),  # a hair later belongs to the next one
        (2.0, 9.0),    # tail of the last chunk
    ],
)
def test_remap_translates_gapless_seconds_to_media_seconds(trimmed, original):
    assert transcribe._remap_vad_time(trimmed, _MAP, _ENDS) == pytest.approx(original)


def test_remap_extrapolates_past_the_last_chunk_rather_than_clamping():
    """Whisper pads to 30 s windows and can report an end past the audio.

    Clamping would stack every trailing cue on one instant; running slightly long
    is the better failure.
    """
    assert transcribe._remap_vad_time(2.5, _MAP, _ENDS) == pytest.approx(9.5)


def test_remap_result_times_is_identity_without_a_map():
    """whisperx reads the original audio; VAD-off reads it too. Neither may shift."""
    segs = [{"start": 1.0, "end": 2.0, "text": "x"}]
    words = [{"start": 1.0, "end": 1.5, "word": "x"}]
    out = transcribe._remap_result_times("x", "zh", segs, words, None)
    assert out == ("x", "zh", segs, words)


def test_remap_result_times_preserves_other_keys():
    """`speaker_id` is attached before the remap runs and must survive it."""
    segs = [{"start": 0.0, "end": 1.0, "text": "x", "speaker_id": "SPEAKER_01"}]
    _t, _l, out_segs, _w = transcribe._remap_result_times("x", "zh", segs, [], _MAP)
    assert out_segs[0]["speaker_id"] == "SPEAKER_01"
    assert out_segs[0]["start"] == pytest.approx(3.0)


def test_words_still_sit_inside_their_segment_after_remapping():
    """`_postprocess` filters words by midpoint containment inside segments.

    That invariant silently depends on both being on the same clock; remapping them
    with the same map is what keeps it true. Never asserted before this file.
    """
    segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
    words = [{"start": 0.2, "end": 0.4, "word": "x"}]
    _t, _l, out_segs, out_words = transcribe._remap_result_times("x", "zh", segs, words, _MAP)
    mid = (out_words[0]["start"] + out_words[0]["end"]) / 2
    assert out_segs[0]["start"] <= mid <= out_segs[0]["end"]


# ── end to end: the assertion the suite never had ─────────────────────────────

def _run_end_to_end(monkeypatch, synth_wav, energy_vad, script, texts):
    """Drive `transcribe()` over synthetic audio with a fake backend.

    The fake backend's segment times are DERIVED from the offset map rather than
    hard-coded, because `speech_pad_ms=150` widens every kept chunk — guessing
    "chunk 1 starts at 1.0" is wrong by the padding, and a test that guesses would
    fail against a correct implementation. One segment is placed just inside the
    head of each kept chunk, which is exactly where whisper would put a word that
    begins as soon as the speech does.
    """
    monkeypatch.setattr(transcribe, "_USE_MLX", False)
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    monkeypatch.setattr(transcribe, "FILTER_WORDS", "")
    monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "")
    path, audio, sr, truth = synth_wav(script)

    import shutil

    copy = path + ".copy.wav"  # transcribe() unlinks what _to_wav hands it
    shutil.copyfile(path, copy)
    monkeypatch.setattr(transcribe, "_to_wav", lambda p: copy)

    captured = {}
    real_vad = transcribe._vad_filter

    def _spy(wav, sample_rate=16000):
        out, offset_map = real_vad(wav, sample_rate)
        captured["map"] = offset_map
        # Now that the chunk layout is known, stock the backend with times inside it.
        segs = [
            _Seg(text, round(t_start + 0.05, 3), round(t_end - 0.05, 3))
            for text, (t_start, t_end, _orig) in zip(texts, offset_map or [])
        ]
        transcribe._fw_model = _Model(segs, _Info("zh"))
        return out, offset_map

    monkeypatch.setattr(transcribe, "_vad_filter", _spy)
    monkeypatch.setattr(transcribe, "_fw_model", _Model([], _Info("zh")))

    _text, _lang, segments, _words = transcribe.transcribe("/clip.mp4", language="zh")
    return segments, audio, sr, truth


def test_segment_start_is_the_real_position_in_the_source_media(
    monkeypatch, synth_wav, energy_vad
):
    """The bug, expressed as the number a user would check.

    Two words, at 3 s and 8 s of a 10 s clip. After VAD they sit back-to-back, so
    whisper honestly reports 0-1 s and 1-2 s. Before the remap arkiv stored those
    verbatim and a click on the second line seeked to 1 s — six seconds early.
    """
    segments, _audio, _sr, truth = _run_end_to_end(
        monkeypatch,
        synth_wav,
        energy_vad,
        [("silence", 3), ("tone", 1), ("silence", 4), ("tone", 1), ("silence", 1)],
        ["第一段", "第二段"],
    )
    assert truth == [(3.0, 4.0), (8.0, 9.0)]
    assert len(segments) == 2
    assert segments[0]["start"] == pytest.approx(3.0, abs=0.2)
    assert segments[0]["end"] == pytest.approx(4.0, abs=0.2)
    assert segments[1]["start"] == pytest.approx(8.0, abs=0.2)
    assert segments[1]["end"] == pytest.approx(9.0, abs=0.2)


def test_every_segment_window_contains_audio_energy(
    monkeypatch, synth_wav, energy_vad
):
    """The guard that catches the NEXT one.

    No arithmetic, no expected constants: slice the ORIGINAL samples at each
    timestamp arkiv reports and require something audible there. It survives any
    refactor of the mapping, and catches sign errors, off-by-one-chunk errors, and
    a future change to `speech_pad_ms`.

    Before the fix, segment two pointed at 1.0-2.0 s — pure silence.
    """
    import numpy as np

    segments, audio, sr, _truth = _run_end_to_end(
        monkeypatch,
        synth_wav,
        energy_vad,
        [("silence", 3), ("tone", 1), ("silence", 4), ("tone", 1), ("silence", 1)],
        ["第一段", "第二段"],
    )
    for seg in segments:
        window = audio[int(seg["start"] * sr):int(seg["end"] * sr)]
        assert len(window), "segment {0} points past the end of the audio".format(seg)
        peak = float(np.max(np.abs(window)))
        assert peak > 0.1, "segment {0} points at silence (peak {1:.3f})".format(seg, peak)
