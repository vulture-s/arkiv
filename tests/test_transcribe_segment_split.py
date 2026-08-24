"""Segments wider than a caption line get split on the line boundaries.

Whisper times whatever fits its 30-second window, so a whole sentence of dialogue
routinely arrives as ONE segment with ONE timestamp. Everything downstream inherits
that granularity: clicking any part of the line in the Inspector seeks to the start
of the sentence, an SRT cue holds more text than fits the time given, and a
frame-exact IN point can only ever be the sentence's first frame.

`subtitle.wrap()` already knows where a line may break. This reuses it and shares
the segment's span between the lines it produces, in proportion to their width.

The proportional share is an estimate — it assumes an even speaking rate inside the
segment. That is worth saying plainly: it does not make the timing exact, it makes
the error one caption line wide instead of one sentence wide.
"""
from __future__ import annotations

import pytest

import subtitle
import transcribe


def _seg(text, start, end, **extra):
    seg = {"text": text, "start": start, "end": end}
    seg.update(extra)
    return seg


LONG = "這是一段很長的旁白，長到一行字幕根本放不下，所以它必須被拆開才有意義。"


def test_a_sentence_wider_than_a_line_becomes_several_segments():
    out = transcribe._split_long_segments([_seg(LONG, 0.0, 12.0)])

    assert len(out) == len(subtitle.wrap(LONG, max_units=transcribe._MAX_SEGMENT_UNITS))
    assert len(out) > 1


def test_the_split_loses_no_text():
    out = transcribe._split_long_segments([_seg(LONG, 0.0, 12.0)])
    assert "".join(s["text"] for s in out) == LONG


def test_the_outer_span_is_unchanged():
    """Load-bearing: the words_json reconciliation in `_postprocess` keeps a word
    only if its midpoint lands inside some kept segment. Shrinking or shifting the
    outer span here would silently drop words at the edges."""
    out = transcribe._split_long_segments([_seg(LONG, 3.5, 12.25)])

    assert out[0]["start"] == 3.5
    assert out[-1]["end"] == 12.25


def test_pieces_are_contiguous_and_ordered():
    out = transcribe._split_long_segments([_seg(LONG, 0.0, 12.0)])

    for a, b in zip(out, out[1:]):
        assert a["end"] == pytest.approx(b["start"], abs=1e-3)
        assert a["start"] < a["end"]


def test_span_follows_width_not_piece_count():
    """Equal division would put every click at the geometric midpoint of the
    sentence. Lines differ in width; a wider line has more speech in it."""
    text = "短。" + "這是一段明顯長很多的句子，需要更多時間念完，所以它應該分到更長的區間。"
    out = transcribe._split_long_segments([_seg(text, 0.0, 20.0)])
    assert len(out) > 2

    durations = [s["end"] - s["start"] for s in out]
    widths = [subtitle.display_units(s["text"]) for s in out]
    ratios = [d / w for d, w in zip(durations, widths)]

    # Every piece gets the same seconds-per-unit — that IS proportional division.
    assert max(ratios) == pytest.approx(min(ratios), rel=0.02)
    # And the check that would pass under equal division too, kept explicit:
    assert durations[0] < durations[-1]


def test_speaker_id_and_unknown_keys_survive():
    """Diarization runs BEFORE this, so the label is already on the segment. Any
    future per-segment key (translation, confidence) rides along the same way."""
    out = transcribe._split_long_segments(
        [_seg(LONG, 0.0, 12.0, speaker_id="SPEAKER_01", translation="x")]
    )

    assert len(out) > 1
    assert all(s["speaker_id"] == "SPEAKER_01" for s in out)
    assert all(s["translation"] == "x" for s in out)


def test_a_segment_that_already_fits_is_returned_untouched():
    seg = _seg("短短一句話。", 1.0, 2.5, speaker_id="A")
    out = transcribe._split_long_segments([seg])
    assert out == [seg]


def test_a_too_fast_segment_is_left_alone():
    """A wide segment that lasts half a second would yield pieces too short to read
    as cues, and at that scale the proportional estimate is mostly rounding error.
    Leaving it whole is the honest outcome; the export path still wraps the line."""
    seg = _seg(LONG, 0.0, 0.5)
    assert transcribe._split_long_segments([seg]) == [seg]


def test_a_zero_length_segment_is_left_alone():
    """Dividing by a zero span would hand every piece the same instant."""
    seg = _seg(LONG, 4.0, 4.0)
    assert transcribe._split_long_segments([seg]) == [seg]


def test_segments_without_timing_pass_through():
    seg = {"text": LONG}
    assert transcribe._split_long_segments([seg]) == [seg]


def test_empty_text_passes_through():
    seg = _seg("   ", 0.0, 5.0)
    assert transcribe._split_long_segments([seg]) == [seg]


def test_splitting_runs_after_diarization(monkeypatch):
    """Order matters and is easy to break by moving one line. pyannote must see the
    original sentence-length windows — half-second windows make one speaker's
    sentence flicker between labels."""
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    monkeypatch.setattr(transcribe, "FILTER_WORDS", "")
    monkeypatch.setattr(transcribe, "DIARIZATION_ENABLED", True)

    seen = {}

    def fake_diarize(timed_segments, wav_path):
        seen["count"] = len(timed_segments)
        return [{**s, "speaker_id": "SPEAKER_00"} for s in timed_segments]

    monkeypatch.setattr(transcribe, "_attach_speaker_ids", fake_diarize)

    segments = [{"text": LONG, "start": 0.0, "end": 12.0,
                 "no_speech_prob": 0.1, "avg_logprob": -0.2, "compression_ratio": 1.1}]
    _text, _lang, out, _words = transcribe._postprocess(
        LONG, "zh", segments, "zh", wav_path="/fake.wav"
    )

    assert seen["count"] == 1, "diarization saw the pieces, not the sentence"
    assert len(out) > 1 and all(s["speaker_id"] == "SPEAKER_00" for s in out)
