"""GET /api/embed/status counts clips whose index entry no longer matches their text.

/api/health's coverage counts any row with an embed_hash as indexed, so a tag
added after embedding (with the reindex failed or skipped) is invisible there.
This route is where that drift shows up.
"""
import pytest


@pytest.fixture
def library(fastapi_client, sample_record):
    import db
    import embed
    for i in (1, 2, 3):
        db.upsert(sample_record(path="/tmp/c{0}.mp4".format(i), filename="c{0}.mp4".format(i)))
    # Clips 1 and 2 were embedded with their current text; clip 3 never was.
    sigs = embed.get_content_signatures()
    for mid in (1, 2):
        db.set_embed_state(mid, sigs[str(mid)][1], "2026-10-09T00:00:00+00:00")
    return fastapi_client


def test_fresh_library_reports_no_stale(library):
    body = library.get("/api/embed/status").json()
    assert body["total_media"] == 3
    assert (body["fresh_media"], body["stale_media"], body["unindexed_media"]) == (2, 0, 1)
    assert body["rebuild_running"] is False


def test_a_tag_added_after_embedding_shows_up_as_stale(library):
    import db
    # Write the tag the way a failed reindex leaves it: tag stored, embed_hash not restamped.
    db.add_tag(1, "海邊", "manual")
    body = library.get("/api/embed/status").json()
    assert (body["fresh_media"], body["stale_media"], body["unindexed_media"]) == (1, 1, 1)


def test_health_coverage_does_not_see_that_drift(library):
    # The reason this route exists: health still calls the stale clip indexed.
    import db
    db.add_tag(1, "海邊", "manual")
    emb = library.get("/api/health").json()["embeddings"]
    assert emb["embedded_media"] == 2


def test_requires_a_read_token(server_module):
    from starlette.testclient import TestClient
    # A non-loopback client with no token must be refused.
    with TestClient(server_module.app, base_url="http://10.0.0.9") as client:
        r = client.get("/api/embed/status")
    assert r.status_code in (401, 403)
