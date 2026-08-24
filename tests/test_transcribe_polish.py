"""LLM polish — the step that was failing silently on every long transcript.

`_llm_polish` sent the whole transcript in one `chat()` call, and `chat()` hard-coded
a 120 s timeout. qwen2.5:14b writes roughly 3-4 characters/second locally, so a
4,000-character transcript needs 20+ minutes: the request timed out, a bare
`except: pass` swallowed the exception, and the caller got back raw unpunctuated
Whisper output — indistinguishable from a transcript the model had chosen not to
change. Nothing was logged, so it looked like polish "just wasn't very good on long
clips".

The fix has two halves and BOTH matter:

* chunking + a real timeout, so long transcripts can finish at all;
* a total **budget**, because a bigger timeout alone would trade a silent 120 s
  failure for a genuine 20-70 minute one — held inside the shared ingest slot,
  with every other clip in the queue waiting behind it. Past the budget the
  remaining chunks come back raw, which is the old outcome, but bounded and said
  out loud.
"""
from __future__ import annotations

import types

import pytest

import transcribe


@pytest.fixture
def polish_calls(monkeypatch):
    """Record every `chat()` the polish path makes; echo the prompt's transcript back.

    The fake returns the input text unchanged, which passes the length-ratio guard,
    so a test only has to look at what was *asked* unless it wants to script a failure.
    """
    calls = []

    def fake_chat(prompt, model=None, timeout=None, temperature=None, **kw):
        body = prompt.split("原始逐字稿：\n", 1)[1].rsplit("\n\n校正後：", 1)[0]
        calls.append({"text": body, "timeout": timeout, "temperature": temperature,
                      "model": model})
        return {"text": body}

    monkeypatch.setattr(transcribe, "chat", fake_chat)
    return calls


def _segments(count, size=40):
    """`count` space-free pieces, the shape `_postprocess` joins with a single space."""
    return ["字{0}".format(i) * (size // 2) for i in range(count)]


# ── chunking ─────────────────────────────────────────────────────────────────

def test_chunks_never_split_a_segment():
    """The load-bearing property. A boundary inside a segment would hand the model
    half a sentence and get back a confidently punctuated half-sentence."""
    segs = _segments(30)
    chunks = transcribe._polish_chunks(" ".join(segs))

    assert len(chunks) > 1, "test is vacuous unless the text actually splits"
    for chunk in chunks:
        for piece in chunk.split(" "):
            assert piece in segs, "chunk boundary landed inside a segment: {0!r}".format(piece)


def test_chunking_loses_nothing():
    segs = _segments(30)
    chunks = transcribe._polish_chunks(" ".join(segs))
    assert " ".join(chunks) == " ".join(segs)


def test_chunks_respect_the_size_limit():
    chunks = transcribe._polish_chunks(" ".join(_segments(30)), max_chars=120)
    assert all(len(c) <= 120 for c in chunks)


def test_a_single_oversized_segment_is_not_chopped():
    """One 900-character segment has no space to break on. Sending it whole is
    correct — chopping mid-sentence to satisfy the limit would be worse."""
    giant = "話" * 900
    assert transcribe._polish_chunks(giant, max_chars=300) == [giant]


def test_short_text_stays_one_call(polish_calls):
    transcribe._llm_polish_batched("短短一句", "zh")
    assert len(polish_calls) == 1


# ── timeout + temperature reach the model ────────────────────────────────────

def test_polish_asks_for_the_long_timeout(polish_calls):
    """120 s is right for interactive RAG and wrong for this. If polish stops
    passing its own timeout it silently inherits the fast-fail default again."""
    transcribe._llm_polish("一句話", "zh")

    assert polish_calls[0]["timeout"] == transcribe._LLM_POLISH_TIMEOUT_S
    assert polish_calls[0]["timeout"] > 120


def test_polish_pins_a_low_temperature(polish_calls):
    """Correcting homophones is not a creative task."""
    transcribe._llm_polish("一句話", "zh")
    assert polish_calls[0]["temperature"] == 0.2


# ── failure is per chunk, and it is announced ────────────────────────────────

def test_one_failing_chunk_degrades_only_itself(monkeypatch, capsys):
    segs = _segments(30)
    text = " ".join(segs)
    chunks = transcribe._polish_chunks(text)
    assert len(chunks) >= 3

    def flaky(prompt, model=None, timeout=None, temperature=None, **kw):
        body = prompt.split("原始逐字稿：\n", 1)[1].rsplit("\n\n校正後：", 1)[0]
        if body == chunks[1]:
            raise TimeoutError("read timed out")
        return {"text": body + "。"}

    monkeypatch.setattr(transcribe, "chat", flaky)
    out = transcribe._llm_polish_batched(text, "zh")

    # The failed chunk came back raw; its neighbours are still polished.
    assert chunks[0] + "。" in out
    assert chunks[2] + "。" in out
    assert chunks[1] + "。" not in out and chunks[1] in out
    assert "TimeoutError" in capsys.readouterr().out, "a swallowed failure is the original bug"


def test_a_wildly_wrong_length_is_rejected_per_chunk(monkeypatch, capsys):
    """The model occasionally answers with a summary, or with its own commentary.
    Before chunking, one such answer discarded the entire transcript."""
    monkeypatch.setattr(transcribe, "chat",
                        lambda prompt, **kw: {"text": "好"})  # far shorter than the input

    assert transcribe._llm_polish("一" * 200, "zh") == "一" * 200
    assert "rejected" in capsys.readouterr().out


# ── the budget ───────────────────────────────────────────────────────────────

def _fake_clock(monkeypatch, ticks):
    """Replace transcribe's `time` module reference — not the real one, which pytest
    itself uses — with a clock that advances by `ticks` seconds per reading."""
    state = {"t": 0.0}

    def monotonic():
        now = state["t"]
        state["t"] += ticks
        return now

    monkeypatch.setattr(transcribe, "time", types.SimpleNamespace(monotonic=monotonic))


def test_budget_stops_polishing_and_returns_the_rest_raw(monkeypatch, polish_calls, capsys):
    """Without this, the "fix" would hold the shared ingest slot for as long as the
    model wanted — worse for the queue than the silent failure it replaced."""
    segs = _segments(30)
    text = " ".join(segs)
    chunks = transcribe._polish_chunks(text)
    assert len(chunks) >= 4

    # The clock is read once before the loop and once per chunk: 0 s, then 60,
    # 120, 180 — so the third chunk is the first one over a 150 s budget.
    monkeypatch.setattr(transcribe, "_LLM_POLISH_BUDGET_S", 150)
    _fake_clock(monkeypatch, ticks=60)

    out = transcribe._llm_polish_batched(text, "zh")

    assert len(polish_calls) == 2, "budget must stop the loop, not merely warn"
    assert out == text, "unpolished chunks are returned verbatim, never dropped"
    assert "budget reached" in capsys.readouterr().out


def test_budget_does_not_fire_when_there_is_time(monkeypatch, polish_calls):
    segs = _segments(30)
    text = " ".join(segs)
    chunks = transcribe._polish_chunks(text)

    monkeypatch.setattr(transcribe, "_LLM_POLISH_BUDGET_S", 10_000)
    _fake_clock(monkeypatch, ticks=1)

    transcribe._llm_polish_batched(text, "zh")
    assert len(polish_calls) == len(chunks)


def test_empty_text_makes_no_calls(polish_calls):
    assert transcribe._llm_polish_batched("   ", "zh") == "   "
    assert polish_calls == []
