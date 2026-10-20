"""Search hits carry where in the clip they matched (``match_start_s``).

The vector index has no time information — chunks are cut by character count —
so the time is recovered from ``segments_json`` at query time. The stored
transcript and the segments do NOT always agree verbatim (punctuation restoration,
opencc, re-correction rewrite one but not the other), so transcript_time anchors on
normalized windows and returns None rather than guess.
"""
import json

import db as db_mod
import transcript_time as tt
import vectordb as vdb

SEGS = [
    {"start": 0.0, "end": 4.0, "text": "今天我們來聊聊黑膠唱盤"},
    {"start": 4.0, "end": 9.5, "text": "首先是唱頭的選擇"},
    {"start": 9.5, "end": 15.0, "text": "然後要注意循軌力的調整"},
    {"start": 15.0, "end": 21.0, "text": "最後是防滑力的設定"},
]
# punctuation restored + one word re-corrected ("選擇" → "挑選"): not verbatim
TRANSCRIPT = "今天我們來聊聊黑膠唱盤。首先是唱頭的挑選，然後要注意循軌力的調整。最後是防滑力的設定。"


# ── transcript_time ──────────────────────────────────────────────────────────
def test_locates_each_segment_despite_punctuation_and_rewording():
    for needle, want in (("黑膠唱盤", 0.0), ("唱頭", 4.0), ("循軌力", 9.5), ("防滑力", 15.0)):
        assert tt.locate_text(TRANSCRIPT, json.dumps(SEGS), needle) == want, needle


def test_position_near_the_end_still_located():
    # only anchors BEFORE the target exist this close to the end
    assert tt.locate(TRANSCRIPT, SEGS, len(TRANSCRIPT) - 2) == 15.0


def test_excerpt_is_matched_ignoring_spacing_and_punctuation():
    # chunk_text re-joins CJK sentences with no separator
    excerpt = "然後要注意循軌力的調整最後是防滑力"
    assert tt.locate_text(TRANSCRIPT, SEGS, excerpt) == 9.5


def test_returns_none_instead_of_guessing():
    assert tt.locate_text(TRANSCRIPT, SEGS, "完全不存在的句子") is None
    assert tt.locate_text(TRANSCRIPT, None, "唱頭") is None
    assert tt.locate_text(TRANSCRIPT, "not json", "唱頭") is None
    assert tt.locate_text("", SEGS, "唱頭") is None
    unrelated = [{"start": 0.0, "text": "aaaaaaaaaaaa"}, {"start": 5.0, "text": "bbbbbbbbbbbb"}]
    assert tt.locate(TRANSCRIPT, unrelated, 10) is None


def test_single_segment_needs_no_alignment():
    assert tt.locate("3 2 1 走", [{"start": 1.25, "end": 3.0, "text": "321走"}], 0) == 1.25


def test_latin_text():
    segs = [{"start": 0.0, "text": "so if it's okay with you"},
            {"start": 3.0, "text": "I'll start with the tonearm"},
            {"start": 7.0, "text": "and then the cartridge alignment"}]
    t = "So, if it's okay with you, I'll start with the tonearm. And then the cartridge alignment."
    assert tt.locate_text(t, segs, "cartridge") == 7.0
    assert tt.locate_text(t, segs, "TONEARM") == 3.0


# ── /api/media search wiring ─────────────────────────────────────────────────
def _seed(conn):
    rows = [
        ("/p/a.mp4", "a.mp4", TRANSCRIPT, json.dumps(SEGS)),   # semantic transcript hit
        ("/p/b.mp4", "b.mp4", TRANSCRIPT, json.dumps(SEGS)),   # semantic TAGS hit → no time
        ("/p/c.mp4", "c.mp4", TRANSCRIPT, json.dumps(SEGS)),   # keyword hit
        ("/p/d.mp4", "d.mp4", "循軌力 only text", None),        # keyword hit, no segments
    ]
    ids = []
    for path, fn, tr, sj in rows:
        cur = conn.execute(
            "INSERT INTO media (path, filename, transcript, segments_json, ext, lang) "
            "VALUES (?,?,?,?,?,?)", (path, fn, tr, sj, ".mp4", "zh"))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def test_search_results_carry_match_start(fastapi_client, monkeypatch):
    with db_mod.get_conn() as conn:
        a, b, c, d = _seed(conn)

    def fake_search(q, n_results=10):
        return [
            {"media_id": a, "score": 0.9, "excerpt": "然後要注意循軌力的調整", "chunk_type": "transcript"},
            # a tag doc can share words with the transcript ("循軌力" is a manual tag
            # here) — it still has no place in the timeline
            {"media_id": b, "score": 0.8, "excerpt": "循軌力的調整", "chunk_type": "tags"},
        ]
    monkeypatch.setattr(vdb, "search", fake_search)

    items = fastapi_client.get("/api/media", params={"q": "循軌力", "limit": 10}).json()["items"]
    by_id = {it["id"]: it for it in items}
    assert by_id[a]["match_start_s"] == 9.5          # where the semantic chunk begins
    assert "match_start_s" not in by_id[b]            # tag hit: no place in the timeline
    assert by_id[c]["match_type"] == "text" and by_id[c]["match_start_s"] == 9.5
    assert "match_start_s" not in by_id[d]            # no segments: unknown, not 0


def test_time_lookup_failure_never_breaks_search(fastapi_client, monkeypatch):
    with db_mod.get_conn() as conn:
        a, *_ = _seed(conn)
    monkeypatch.setattr(vdb, "search", lambda q, n_results=10: [
        {"media_id": a, "score": 0.9, "excerpt": "循軌力", "chunk_type": "transcript"}])

    def boom(*_a, **_k):
        raise RuntimeError("bad segments")
    monkeypatch.setattr(tt, "locate_text", boom)
    r = fastapi_client.get("/api/media", params={"q": "循軌力"})
    assert r.status_code == 200
    assert all("match_start_s" not in it for it in r.json()["items"])
