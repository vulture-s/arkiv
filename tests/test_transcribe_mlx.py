"""`_transcribe_mlx` — the Mac backend, which had no tests and dropped its words.

mlx-whisper has been called with `word_timestamps=True` since this function was
written, and the words it returned were discarded (`words=[]`). Every clip ever
ingested on a Mac — the primary platform — stored `words_json = NULL`, so
`/api/media/{id}/remotion-props` had nothing to give and MCP's opt-in word list was
always empty there.

Shape note, because the two backends disagree and this is where they are reconciled:
mlx puts words on each segment as plain dicts (`{"word", "start", "end",
"probability"}`); faster-whisper returns objects with `.word/.start/.end/
.probability`. Both are normalised to `{"word", "start", "end", "score"}` before
`_postprocess` sees them.
"""
from __future__ import annotations

import sys
import types

import pytest

import transcribe


def _mlx_result(segments, text="測試", language="zh"):
    return {"text": text, "language": language, "segments": segments}


@pytest.fixture
def mlx(monkeypatch):
    """Drive `_transcribe_mlx` with a scripted mlx-whisper result."""

    def _install(result):
        fake = types.ModuleType("mlx_whisper")
        fake.transcribe = lambda *a, **k: result
        monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
        monkeypatch.setattr(transcribe, "LLM_POLISH", False)
        monkeypatch.setattr(transcribe, "FILTER_WORDS", "")
        monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "")
        return result

    return _install


def test_words_are_carried_out_of_the_backend(mlx):
    """The defect: these used to be dropped on the floor."""
    mlx(_mlx_result([{
        "start": 0.0, "end": 2.0, "text": "今天天氣很好",
        "no_speech_prob": 0.01, "avg_logprob": -0.2, "compression_ratio": 1.1,
        "words": [
            {"word": "今天", "start": 0.0, "end": 0.5, "probability": 0.91},
            {"word": "天氣", "start": 0.5, "end": 1.2, "probability": 0.88},
        ],
    }]))

    _text, _lang, _segments, words = transcribe._transcribe_mlx("/fake.wav", "zh")

    assert [w["word"] for w in words] == ["今天", "天氣"]
    assert words[0]["start"] == 0.0 and words[0]["end"] == 0.5


def test_probability_is_normalised_to_score(mlx):
    """faster-whisper calls it `score`; one contract downstream, not two."""
    mlx(_mlx_result([{
        "start": 0.0, "end": 1.0, "text": "x",
        "no_speech_prob": 0.01, "avg_logprob": -0.2, "compression_ratio": 1.1,
        "words": [{"word": "x", "start": 0.0, "end": 1.0, "probability": 0.8123}],
    }]))

    _t, _l, _s, words = transcribe._transcribe_mlx("/fake.wav", "zh")

    assert "probability" not in words[0]
    assert words[0]["score"] == 0.812  # rounded to 3, like the other backend


def test_a_segment_without_words_is_not_an_error(mlx):
    """Older mlx builds, and the test stub, omit the key entirely."""
    mlx(_mlx_result([{
        "start": 0.0, "end": 1.0, "text": "x",
        "no_speech_prob": 0.01, "avg_logprob": -0.2, "compression_ratio": 1.1,
    }]))

    _t, _l, segments, words = transcribe._transcribe_mlx("/fake.wav", "zh")

    assert words == []
    assert len(segments) == 1


def test_words_with_no_timing_are_skipped(mlx):
    """A word without both ends cannot be placed on a timeline; storing it as 0
    would silently pin it to the head of the clip."""
    mlx(_mlx_result([{
        "start": 0.0, "end": 1.0, "text": "ab",
        "no_speech_prob": 0.01, "avg_logprob": -0.2, "compression_ratio": 1.1,
        "words": [
            {"word": "a", "start": 0.0, "end": None, "probability": 0.5},
            {"word": "b", "start": None, "end": 1.0, "probability": 0.5},
            {"word": "c", "start": 0.2, "end": 0.4, "probability": 0.5},
        ],
    }]))

    _t, _l, _s, words = transcribe._transcribe_mlx("/fake.wav", "zh")

    assert [w["word"] for w in words] == ["c"]


def test_mlx_words_are_remapped_onto_the_media_timeline(
    monkeypatch, synth_wav, energy_vad, mlx
):
    """Words ride the same clock as segments, so they need the same translation.

    Without this the Mac path would start writing word timings for the first time —
    on the gapless clock — which is worse than the NULL it replaced.
    """
    import shutil

    monkeypatch.setattr(transcribe, "_USE_MLX", True)
    monkeypatch.setattr(transcribe, "VAD_ENABLED", True)
    path, _audio, _sr, truth = synth_wav([("silence", 3), ("tone", 1), ("silence", 2)])
    assert truth == [(3.0, 4.0)]

    copy = path + ".copy.wav"
    shutil.copyfile(path, copy)
    monkeypatch.setattr(transcribe, "_to_wav", lambda p: copy)

    captured = {}
    real_vad = transcribe._vad_filter

    def _spy(wav, sample_rate=16000):
        out, offset_map = real_vad(wav, sample_rate)
        captured["map"] = offset_map
        t_start, t_end, _orig = offset_map[0]
        mlx(_mlx_result([{
            "start": round(t_start, 3), "end": round(t_end, 3), "text": "一句話",
            "no_speech_prob": 0.01, "avg_logprob": -0.2, "compression_ratio": 1.1,
            "words": [{
                "word": "一句話",
                "start": round(t_start, 3),
                "end": round(t_end, 3),
                "probability": 0.9,
            }],
        }]))
        return out, offset_map

    monkeypatch.setattr(transcribe, "_vad_filter", _spy)
    mlx(_mlx_result([]))  # replaced inside the spy once the layout is known

    _text, _lang, segments, words = transcribe.transcribe("/clip.mp4", language="zh")

    assert len(words) == 1
    assert words[0]["start"] == pytest.approx(3.0, abs=0.2), (
        "word timings must be translated out of gapless-speech time too"
    )
    # And the invariant `_postprocess` depends on: the word still sits in its segment.
    mid = (words[0]["start"] + words[0]["end"]) / 2
    assert segments[0]["start"] <= mid <= segments[0]["end"]
