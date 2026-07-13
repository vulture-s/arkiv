"""Payload-diet perf fixes (fable-audit round-5 #26/#27/#31)."""
import importlib


def test_media_detail_drops_words_json_keeps_segments(fastapi_client):
    """#26 (codex-verified): the per-click media-detail response drops words_json
    (multi-MB, no frontend consumer — word data is served via /remotion-props) but
    KEEPS segments_json, which the inspector's transcript seek uses."""
    db = importlib.import_module("db")
    with db.get_conn() as c:
        c.execute(
            "INSERT INTO media (path, filename, ext, transcript, segments_json, words_json) "
            "VALUES (?,?,?,?,?,?)",
            ("m.mp4", "m.mp4", ".mp4", "hi",
             '[{"start":0,"end":1,"text":"hi"}]', '[{"word":"hi","start":0}]'),
        )
    r = fastapi_client.get("/api/media/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "words_json" not in body       # dropped from the per-click response
    assert "segments_json" in body         # kept — inspector seek depends on it


def test_get_stats_single_pass_aggregate(server_module):
    """#27: get_stats folds five full-table scans into one; the output is unchanged."""
    db = importlib.import_module("db")
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO media (path, filename, ext, transcript, thumbnail_path, "
            "duration_s, size_mb, lang) VALUES (?,?,?,?,?,?,?,?)",
            [
                ("a.mp4", "a", ".mp4", "t", "th.jpg", 10.0, 5.0, "zh"),
                ("b.mp4", "b", ".mp4", None, None, 20.0, 3.0, "en"),
                ("c.mp4", "c", ".mp4", "t2", "th2.jpg", 5.0, 1.0, "zh"),
            ],
        )
    s = db.get_stats()
    assert s["total"] == 3
    assert s["with_transcript"] == 2       # a, c (COUNT(transcript) = non-NULL)
    assert s["with_thumb"] == 2            # a, c
    assert s["total_duration_s"] == 35.0
    assert s["total_size_mb"] == 9.0
    assert s["langs"] == {"zh": 2, "en": 1}


def test_get_indexed_media_ids_from_chunk_prefixes():
    """#31: reconcile derives media_ids from chunk-id prefixes (ids-only fetch) instead
    of pulling every chunk's metadata."""
    embed = importlib.import_module("embed")

    class FakeCol:
        def get(self, include=None, limit=None):
            assert include == []           # ids-only — no heavy metadata pull
            return {"ids": ["12_t0", "12_t1", "12_f0", "7_t0"]}

    assert embed.get_indexed_media_ids(FakeCol()) == {"12", "7"}
