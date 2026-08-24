"""`_vad_filter` — the function that had no tests, in the file that could not run it.

Until this file, `transcribe._vad_filter` was unreachable from the suite for three
independent reasons: the silero fake returned `[]` so control left at the no-speech
guard, `torch` had no `from_numpy`, and `soundfile.read` returned `([], 16000)`.
Every place the function appeared in a test it was monkeypatched to identity
(`test_transcribe_faster_whisper.py:142,157`, `test_zh_convert.py:81`).

So this is a characterisation file first and a regression file second. It pins what
the function does TODAY — including the part that is wrong — because the repo's rule
is that a red test never gets merged. The next commit changes the behaviour and flips
the assertion that names the defect; anything that changes here without that commit
is an accident.

The defect, stated once: VAD concatenates only the speech it keeps and returns the
path to that gapless wav. The `stamps` — the only record of where that speech came
from — go out of scope. Whisper then reports timestamps against gapless audio while
every other clock in arkiv (frames, waveform, `<video>.currentTime`, EDL source TC)
is original-media time.
"""
from __future__ import annotations

import wave

import pytest

import transcribe


def _wav_seconds(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ── the fixtures work at all ──────────────────────────────────────────────────

def test_synth_wav_round_trips_through_the_soundfile_fake(synth_wav):
    """If this fails, nothing else in the file means anything."""
    path, audio, sr, truth = synth_wav([("silence", 1), ("tone", 2), ("silence", 1)])
    assert sr == 16000
    assert _wav_seconds(path) == pytest.approx(4.0, abs=0.01)
    assert truth == [(1.0, 3.0)]


def test_energy_vad_finds_the_speech_we_planted(synth_wav, energy_vad):
    _path, audio, sr, truth = synth_wav([("silence", 3), ("tone", 1), ("silence", 6)])
    stamps = transcribe.get_speech_timestamps(audio, object(), sampling_rate=sr)
    assert len(stamps) == 1
    assert stamps[0]["start"] / sr == pytest.approx(3.0, abs=0.05)
    assert stamps[0]["end"] / sr == pytest.approx(4.0, abs=0.05)


# ── passthrough branches ──────────────────────────────────────────────────────

def test_returns_the_original_path_when_vad_is_disabled(synth_wav, monkeypatch):
    monkeypatch.setattr(transcribe, "VAD_ENABLED", False)
    path, _audio, _sr, _truth = synth_wav([("tone", 1)])
    assert transcribe._vad_filter(path) == path


def test_returns_the_original_path_on_sample_rate_mismatch(synth_wav, monkeypatch):
    """A safety valve: VAD is skipped rather than run against the wrong rate."""
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, _truth = synth_wav([("tone", 1)])
    assert transcribe._vad_filter(path, sample_rate=48000) == path


def test_returns_none_when_there_is_no_speech(synth_wav, energy_vad, monkeypatch):
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, _truth = synth_wav([("silence", 2)])
    assert transcribe._vad_filter(path) is None


# ── the trimming branch, and the defect it hides ──────────────────────────────

def test_trimmed_wav_keeps_only_the_speech(synth_wav, energy_vad, monkeypatch):
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, _truth = synth_wav(
        [("silence", 3), ("tone", 1), ("silence", 4), ("tone", 1), ("silence", 1)]
    )
    out = transcribe._vad_filter(path)
    assert out != path
    # 2 s of speech survives out of a 10 s file. `speech_pad_ms=150` widens each
    # kept region a little, so this is a band rather than an equality.
    assert 2.0 <= _wav_seconds(out) <= 2.7
    assert _wav_seconds(path) == pytest.approx(10.0, abs=0.01)


def test_todays_return_is_a_path_alone__so_the_mapping_is_lost(
    synth_wav, energy_vad, monkeypatch
):
    """THE DEFECT, pinned so the fix has something to flip.

    `_vad_filter` computed exactly the information needed to translate a gapless
    timestamp back to media time — `stamps` — and then returned a string. Nothing
    else in the process ever sees it, and the wav it describes is unlinked shortly
    after (`transcribe.py:258`).

    When this assertion starts failing, that is the fix landing, not a regression:
    the next commit widens the return to `(path, offset_map)`.
    """
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, _truth = synth_wav([("silence", 3), ("tone", 1), ("silence", 2)])
    out = transcribe._vad_filter(path)
    assert isinstance(out, str)


def test_speech_that_starts_late_is_reported_from_zero(
    synth_wav, energy_vad, monkeypatch
):
    """The user-visible consequence, expressed in audio rather than in types.

    The single word in this file is at 5.0 s. After VAD it sits at ~0.0 s in the
    audio whisper actually reads, so whisper says "0.0" and arkiv stores "0.0" —
    a transcript line whose click seeks five seconds early.

    Pinned here as an offset measured from the trimmed file, so the assertion
    survives the fix; the fix's own test asserts the corrected value end to end.
    """
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, sr, truth = synth_wav([("silence", 5), ("tone", 1), ("silence", 1)])
    assert truth == [(5.0, 6.0)]

    out = transcribe._vad_filter(path)
    import soundfile as sf

    trimmed, _ = sf.read(out, dtype="float32")
    # The speech now begins within a padding-width of the start of the file.
    first_loud = next(i for i, v in enumerate(trimmed) if abs(float(v)) > 0.1)
    assert first_loud / sr < 0.2, "speech should sit at the head of the trimmed wav"
