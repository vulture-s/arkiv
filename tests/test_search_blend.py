"""Search-fix regression tests (Direction 2: blend + stable pagination).

Covers the two bugs that made search feel broken:
  • `total` used to be windowed by `offset+limit`, so the first page reported
    e.g. 50 and hid every later page behind a false "that's all".
  • Literal filename/transcript/tag matches (the `text` bucket) were skipped
    whenever the semantic pass already filled the window — a clip that LITERALLY
    contained the query term could be invisible.

These run against a seeded tmp DB with a monkeypatched `vectordb.search` so the
semantic bucket is small and deterministic, forcing the lexical bucket to surface.
"""
import db as db_mod
import vectordb as vdb


def _seed(conn):
    ids = []
    for i in range(1, 13):
        # ids 1-3 match by filename; ids 4-6 match by transcript; 7-12 no match.
        fn = "clip_ZZZTERM_{0}.mp4".format(i) if i <= 3 else "clip_{0}.mp4".format(i)
        tr = "transcript with ZZZTERM text {0}".format(i) if 4 <= i <= 6 else "transcript {0}".format(i)
        cur = conn.execute(
            "INSERT INTO media (path, filename, transcript, ext, lang) VALUES (?,?,?,?,?)",
            ("/p/{0}.mp4".format(i), fn, tr, ".mp4", "zh"),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def test_search_blend_semantic_and_lexical_and_pagination(fastapi_client, monkeypatch):
    with db_mod.get_conn() as conn:
        ids = _seed(conn)

    # Semantic bucket returns ONLY ids 1-3, so lexical (4-6) must still surface.
    def fake_search(q, n_results=10):
        return [
            {"media_id": ids[j], "score": round(0.9 - j * 0.1, 3), "excerpt": "sem {0}".format(j)}
            for j in range(3)
        ]

    monkeypatch.setattr(vdb, "search", fake_search)

    p0 = fastapi_client.get("/api/media", params={"q": "ZZZTERM", "limit": 2, "offset": 0}).json()
    p1 = fastapi_client.get("/api/media", params={"q": "ZZZTERM", "limit": 2, "offset": 2}).json()
    p2 = fastapi_client.get("/api/media", params={"q": "ZZZTERM", "limit": 2, "offset": 4}).json()

    # stable, offset-independent total
    assert p0["total"] == 6
    assert p0["total"] == p1["total"] == p2["total"]
    # pagination slices correctly
    assert [len(p["items"]) for p in (p0, p1, p2)] == [2, 2, 2]
    # both match buckets present + lexical literal matches surfaced
    types = set()
    for p in (p0, p1, p2):
        for it in p["items"]:
            assert "match_type" in it
            types.add(it["match_type"])
    assert "semantic" in types and "text" in types
    text_ids = [it["id"] for p in (p0, p1, p2) for it in p["items"] if it["match_type"] == "text"]
    assert set(text_ids) == set(ids[3:6])


def test_health_exposes_embeddings_coverage(fastapi_client):
    with db_mod.get_conn() as conn:
        _seed(conn)
    r = fastapi_client.get("/api/health")
    assert r.status_code in (200, 503)  # unauthenticated; ollama may be down in CI
    body = r.json()
    assert "embeddings" in body
    emb = body["embeddings"]
    assert {"total_media", "embedded_media", "coverage"} <= set(emb.keys())
    # 12 seeded rows, none embedded yet → 0% coverage
    assert emb["total_media"] == 12
    assert emb["embedded_media"] == 0
    assert emb["coverage"] == 0.0
