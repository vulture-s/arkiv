"""A second ASR engine, reached over the shape everyone already speaks.

The thing we want locally — QwenASR ships an `/audio/transcriptions` + `/health`
server — talks the same protocol as Groq, Cloudflare Workers AI, and every
whisper.cpp server. So this is one adapter for all of them, and which engine is in
use is a URL.

The awkward part is that they disagree about the response body. Three shapes turn
up and all three have to work, because the whole point of a second pass is that it
comes from a *different* engine.
"""
from __future__ import annotations

import json

import pytest
import requests

import asr_api


class _Resp:
    def __init__(self, body, content_type="application/json", status=200):
        self.text = body
        self.headers = {"Content-Type": content_type}
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError("{0}".format(self._status))


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF0000WAVE")
    return str(p)


def _post(monkeypatch, resp, captured=None):
    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        if captured is not None:
            captured.update({"url": url, "headers": headers or {}, "data": data or {},
                             "files": files or {}, "timeout": timeout})
        return resp
    monkeypatch.setattr(requests, "post", fake_post)


# ── the three response shapes ────────────────────────────────────────────────

def test_verbose_json_segments_are_used(monkeypatch, wav):
    _post(monkeypatch, _Resp(json.dumps({
        "text": "第一句 第二句",
        "segments": [{"start": 0.0, "end": 2.0, "text": "第一句"},
                     {"start": 2.0, "end": 4.0, "text": "第二句"}],
    }, ensure_ascii=False)))

    text, lang, segs, words = asr_api.transcribe(wav, "zh", base_url="http://x")

    assert [s["text"] for s in segs] == ["第一句", "第二句"]
    assert segs[1]["start"] == 2.0
    assert text == "第一句 第二句"


def test_srt_is_parsed_into_segments(monkeypatch, wav):
    """QwenASR's own default. The timings are there — just in subtitle format."""
    _post(monkeypatch, _Resp(
        "1\n00:00:01,000 --> 00:00:03,500\n第一句話\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n第二句話\n", "text/plain"))

    _text, _lang, segs, _w = asr_api.transcribe(wav, "zh", base_url="http://x")

    assert [(s["start"], s["end"]) for s in segs] == [(1.0, 3.5), (4.0, 6.0)]
    assert segs[0]["text"] == "第一句話"


def test_a_text_only_answer_yields_no_segments(monkeypatch, wav):
    """**No invented timings.** A server that returns only text has told us nothing
    about when anything was said, and a fabricated start/end is indistinguishable
    downstream from a measured one — which is the entire bug class this project
    just spent a wave removing."""
    _post(monkeypatch, _Resp("就只有一串文字沒有時間", "text/plain"))

    text, _lang, segs, _w = asr_api.transcribe(wav, "zh", base_url="http://x")

    assert text == "就只有一串文字沒有時間"
    assert segs == []


def test_words_are_always_empty(monkeypatch, wav):
    """None of these endpoints return word timings, and splitting a segment evenly
    to manufacture them is the same invented-timestamp mistake one level down."""
    _post(monkeypatch, _Resp(json.dumps({
        "text": "abc", "segments": [{"start": 0, "end": 3, "text": "abc"}]})))

    _t, _l, _s, words = asr_api.transcribe(wav, "zh", base_url="http://x")

    assert words == []


# ── SRT parsing edge cases ───────────────────────────────────────────────────

def test_both_millisecond_separators_are_accepted():
    """SRT says comma, WebVTT says dot, and real servers emit both."""
    comma = asr_api.parse_srt("1\n00:00:01,500 --> 00:00:02,000\n甲\n")
    dot = asr_api.parse_srt("1\n00:00:01.500 --> 00:00:02.000\n甲\n")
    assert comma == dot
    assert comma[0]["start"] == 1.5


def test_a_multi_line_cue_becomes_one_segment():
    segs = asr_api.parse_srt("1\n00:00:00,000 --> 00:00:02,000\n第一行\n第二行\n")
    assert segs[0]["text"] == "第一行 第二行"


def test_a_cue_with_no_text_is_dropped():
    segs = asr_api.parse_srt("1\n00:00:00,000 --> 00:00:02,000\n\n\n2\n"
                             "00:00:02,000 --> 00:00:03,000\n有字\n")
    assert [s["text"] for s in segs] == ["有字"]


def test_garbage_is_not_a_segment():
    assert asr_api.parse_srt("這不是 SRT，只是一段話") == []
    assert asr_api.parse_srt("") == []


def test_short_millisecond_fields_are_padded_not_misread():
    """`00:00:01,5` means 500 ms, not 5 ms. Reading it as 5 would put the cue half
    a second early — small, silent, and exactly the kind of drift we just fixed."""
    assert asr_api.parse_srt("1\n00:00:01,5 --> 00:00:02,0\n甲\n")[0]["start"] == 1.5


# ── the request ──────────────────────────────────────────────────────────────

def test_it_asks_for_timings(monkeypatch, wav):
    """`verbose_json` is what gets segments out of a compliant server. A server that
    doesn't know the format ignores it and answers in its own, which the parser
    handles — so asking costs nothing and not asking costs the timings."""
    cap = {}
    _post(monkeypatch, _Resp('{"text":"x"}'), cap)

    asr_api.transcribe(wav, "zh", base_url="http://x")

    assert cap["data"]["response_format"] == "verbose_json"


def test_the_language_hint_is_passed_through(monkeypatch, wav):
    cap = {}
    _post(monkeypatch, _Resp('{"text":"x"}'), cap)
    asr_api.transcribe(wav, "zh", base_url="http://x")
    assert cap["data"]["language"] == "zh"


def test_no_language_hint_means_no_field(monkeypatch, wav):
    """Sending an empty language is not the same as omitting it — some servers read
    `""` as a request to transcribe in no language at all."""
    cap = {}
    _post(monkeypatch, _Resp('{"text":"x"}'), cap)
    asr_api.transcribe(wav, None, base_url="http://x")
    assert "language" not in cap["data"]


def test_the_api_key_becomes_a_bearer_header(monkeypatch, wav):
    cap = {}
    _post(monkeypatch, _Resp('{"text":"x"}'), cap)
    asr_api.transcribe(wav, "zh", base_url="http://x", api_key="secret")
    assert cap["headers"]["Authorization"] == "Bearer secret"


def test_no_key_sends_no_auth_header(monkeypatch, wav):
    """A local QwenASR with auth off would reject `Bearer ` on some builds."""
    cap = {}
    _post(monkeypatch, _Resp('{"text":"x"}'), cap)
    asr_api.transcribe(wav, "zh", base_url="http://x", api_key="")
    assert "Authorization" not in cap["headers"]


def test_a_trailing_slash_in_the_base_url_does_not_double_up(monkeypatch, wav):
    cap = {}
    _post(monkeypatch, _Resp('{"text":"x"}'), cap)
    asr_api.transcribe(wav, "zh", base_url="http://x:8000/")
    assert cap["url"] == "http://x:8000/audio/transcriptions"


def test_an_unconfigured_endpoint_is_a_clear_error(monkeypatch, wav):
    """Silently returning nothing would look exactly like 'this clip has no speech'
    — the failure this project has now been bitten by twice."""
    monkeypatch.setattr(asr_api, "ASR_API_BASE", "")
    with pytest.raises(RuntimeError, match="ARKIV_ASR_API_BASE"):
        asr_api.transcribe(wav, "zh")


def test_an_http_error_propagates(monkeypatch, wav):
    _post(monkeypatch, _Resp("nope", "text/plain", status=500))
    with pytest.raises(requests.HTTPError):
        asr_api.transcribe(wav, "zh", base_url="http://x")


def test_configured_reflects_the_env(monkeypatch):
    monkeypatch.setattr(asr_api, "ASR_API_BASE", "")
    assert asr_api.configured() is False
    monkeypatch.setattr(asr_api, "ASR_API_BASE", "http://x")
    assert asr_api.configured() is True


# ── a 200 is not a promise that the body is readable ─────────────────────────

def test_a_subtitle_body_that_will_not_parse_is_an_error_not_a_transcript():
    """The worst outcome this module can have. A failed SRT parse used to fall
    through and return the raw body — index numbers and `-->` lines and all — as
    the spoken words. Nothing about that looks wrong downstream."""
    body = "1\n00:00:0X,000 --> 呃\n你好\n"

    with pytest.raises(ValueError, match="subtitle"):
        asr_api._segments_from_payload(body, "text/plain")


def test_a_single_digit_hour_is_still_a_timestamp():
    """`0:00:01,000` is what several emitters write, and the hour field was
    `\\d{2}` — so the whole file failed to parse and came back as transcript text."""
    segs = asr_api.parse_srt("1\n0:00:01,000 --> 0:00:02,000\n甲\n")

    assert segs == [{"start": 1.0, "end": 2.0, "text": "甲"}]


def test_the_timing_line_is_searched_for_not_assumed():
    """A cue index is optional, so the timing is not always the second line."""
    assert asr_api.parse_srt("00:00:01,000 --> 00:00:02,000\n甲\n")[0]["text"] == "甲"


def test_plain_text_is_still_plain_text():
    """The subtitle guard must not swallow the shape it sits next to."""
    assert asr_api._segments_from_payload("就是一段話", "text/plain") == ("就是一段話", [])


# ── malformed JSON that still arrives with a 200 ─────────────────────────────

def test_a_segment_that_is_not_an_object_does_not_take_down_the_caller():
    text, segs = asr_api._segments_from_payload(
        json.dumps({"text": "完整逐字稿", "segments": ["not a dict"]}), "application/json")

    assert text == "完整逐字稿" and segs == []


def test_a_segments_field_that_is_not_a_list_does_not_take_down_the_caller():
    """Written with a NUMBER, not a dict. The first version used `{"a": 1}` and
    could not fail: iterating a dict yields its keys, which are strings, which the
    per-segment guard already drops — so the list check it claimed to pin could be
    deleted with the test still green. A non-iterable is what that check is for."""
    text, segs = asr_api._segments_from_payload(
        json.dumps({"text": "甲", "segments": 5}), "application/json")

    assert text == "甲" and segs == []
    # and the shapes that merely iterate to nothing still come back clean
    assert asr_api._segments_from_payload(
        json.dumps({"text": "乙", "segments": {"a": 1}}), "application/json") == ("乙", [])


def test_a_non_string_transcript_falls_back_to_the_segments():
    """`{"text": 5}` used to raise on `.strip()`. The words are in the segments
    either way, so there is no reason for the caller to see an exception."""
    text, segs = asr_api._segments_from_payload(
        json.dumps({"text": 5, "segments": [{"start": 0, "end": 1, "text": "甲"}]}),
        "application/json")

    assert text == "甲" and len(segs) == 1


def test_an_unusable_timing_costs_the_placement_not_the_words():
    """`float("abc")` used to raise. Dropping the line instead would lose speech,
    and keeping it at 0.0 would be the invented-timestamp bug — so the segment
    leaves the timed list while its words stay in the transcript."""
    text, segs = asr_api._segments_from_payload(json.dumps({"segments": [
        {"start": "abc", "end": 1, "text": "甲"},
        {"start": 1, "end": 2, "text": "乙"},
    ]}), "application/json")

    assert text == "甲 乙"
    assert [s["text"] for s in segs] == ["乙"]


def test_a_null_start_is_not_the_head_of_the_clip():
    """`s.get("start") or 0.0` read a missing timing as "zero seconds in" — a
    silent, confident, wrong placement."""
    _text, segs = asr_api._segments_from_payload(
        json.dumps({"segments": [{"start": None, "end": 1, "text": "甲"}]}),
        "application/json")

    assert segs == []


def test_a_boolean_is_not_a_timestamp():
    """`isinstance(True, int)` is True, so a bare numeric check lets `start: true`
    through as 1.0 second."""
    _text, segs = asr_api._segments_from_payload(
        json.dumps({"segments": [{"start": True, "end": 2, "text": "甲"}]}),
        "application/json")

    assert segs == []
