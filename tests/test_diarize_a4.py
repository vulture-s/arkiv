"""A4 — speaker diarization: attach speaker_id to transcript segments.

The alignment logic itself lives in (and is tested by) the speaker-align package;
these tests cover arkiv's integration: the _attach_speaker_ids adapter (token gate,
soft-fail, speaker→speaker_id rename) and the _postprocess hook (off by default,
runs only when enabled + a wav_path is supplied).

speaker-align is an optional (M2-local, not-yet-on-PyPI) dependency, so the tests
that exercise real alignment self-skip when it isn't installed — same idiom as the
opencc / mcp / psycopg tests.
"""
import json

import pytest

import config
import transcribe

try:
    from speaker_align import SpeakerSegment, DiarizationResult
    _HAVE_SPEAKER_ALIGN = True
except ImportError:  # pragma: no cover - env without the optional dep
    _HAVE_SPEAKER_ALIGN = False

_needs_sa = pytest.mark.skipif(
    not _HAVE_SPEAKER_ALIGN, reason="speaker-align not installed")


class _FakeDiarizer:
    def __init__(self, turns):
        self._turns = turns

    def diarize(self, wav_path):
        return DiarizationResult(segments=self._turns, num_speakers=2)


class _BoomDiarizer:
    def diarize(self, wav_path):
        raise RuntimeError("pyannote exploded")


@_needs_sa
def test_attach_renames_speaker_to_speaker_id(monkeypatch):
    import speaker_align
    turns = [SpeakerSegment("SPEAKER_00", 0.0, 5.0),
             SpeakerSegment("SPEAKER_01", 5.0, 10.0)]
    monkeypatch.setattr(speaker_align, "get_diarizer", lambda **kw: _FakeDiarizer(turns))
    monkeypatch.setattr(transcribe, "PYANNOTE_TOKEN", "tok")

    segs = [{"start": 0.0, "end": 3.0, "text": "早安"},
            {"start": 6.0, "end": 9.0, "text": "午安"}]
    out = transcribe._attach_speaker_ids(segs, "/tmp/fake.wav")

    assert out[0]["speaker_id"] == "SPEAKER_00"
    assert out[1]["speaker_id"] == "SPEAKER_01"
    # speaker-align's own key must not leak through arkiv's contract.
    assert "speaker" not in out[0]
    # original text/timing preserved.
    assert out[0]["text"] == "早安" and out[0]["start"] == 0.0


@_needs_sa
def test_attach_passes_configured_token(monkeypatch):
    import speaker_align
    seen = {}

    def _spy(**kw):
        seen["auth_token"] = kw.get("auth_token")
        return _FakeDiarizer([SpeakerSegment("SPEAKER_00", 0.0, 10.0)])

    monkeypatch.setattr(speaker_align, "get_diarizer", _spy)
    monkeypatch.setattr(transcribe, "PYANNOTE_TOKEN", "hf-secret")
    transcribe._attach_speaker_ids([{"start": 0.0, "end": 3.0, "text": "x"}], "/tmp/f.wav")
    assert seen["auth_token"] == "hf-secret"


def test_attach_no_token_is_soft(monkeypatch):
    monkeypatch.setattr(transcribe, "PYANNOTE_TOKEN", "")
    segs = [{"start": 0.0, "end": 3.0, "text": "x"}]
    out = transcribe._attach_speaker_ids(segs, "/tmp/f.wav")
    assert out == segs  # unchanged; no speaker_id, no raise


@_needs_sa
def test_attach_diarize_error_is_soft(monkeypatch):
    import speaker_align
    monkeypatch.setattr(speaker_align, "get_diarizer", lambda **kw: _BoomDiarizer())
    monkeypatch.setattr(transcribe, "PYANNOTE_TOKEN", "tok")
    segs = [{"start": 0.0, "end": 3.0, "text": "x"}]
    out = transcribe._attach_speaker_ids(segs, "/tmp/f.wav")
    assert out == segs  # a diarizer blowing up must not break transcription


def test_attach_empty_or_no_wav_noops():
    assert transcribe._attach_speaker_ids([], "/tmp/f.wav") == []
    segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
    assert transcribe._attach_speaker_ids(segs, "") == segs


# --- _postprocess hook wiring ---

_RAW = [{"text": "第一句話很清楚", "start": 0.0, "end": 3.0,
         "no_speech_prob": 0.0, "avg_logprob": -0.2, "compression_ratio": 1.2}]


def test_postprocess_no_speaker_id_when_disabled(monkeypatch):
    monkeypatch.setattr(transcribe, "DIARIZATION_ENABLED", False)
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    _, _, segs, _ = transcribe._postprocess("第一句話很清楚", "zh", list(_RAW),
                                            "zh", words=[], wav_path="/tmp/f.wav")
    assert segs and "speaker_id" not in segs[0]


def test_postprocess_calls_attach_when_enabled(monkeypatch):
    monkeypatch.setattr(transcribe, "DIARIZATION_ENABLED", True)
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)

    def _fake_attach(timed, wav):
        return [dict(s, speaker_id="SPEAKER_00") for s in timed]

    monkeypatch.setattr(transcribe, "_attach_speaker_ids", _fake_attach)
    _, _, segs, _ = transcribe._postprocess("第一句話很清楚", "zh", list(_RAW),
                                            "zh", words=[], wav_path="/tmp/f.wav")
    assert segs[0]["speaker_id"] == "SPEAKER_00"


def test_postprocess_no_attach_without_wav(monkeypatch):
    # Enabled but no wav_path (e.g. an old caller) → hook must not fire.
    monkeypatch.setattr(transcribe, "DIARIZATION_ENABLED", True)
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    called = {"n": 0}
    monkeypatch.setattr(transcribe, "_attach_speaker_ids",
                        lambda t, w: called.__setitem__("n", called["n"] + 1) or t)
    transcribe._postprocess("第一句話很清楚", "zh", list(_RAW), "zh", words=[])
    assert called["n"] == 0
