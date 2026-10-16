"""Cross-process frames freshness — "server 重啟才讀得到 frames".

Scenario from the roadmap's known-issue table: the server is running; another
PROCESS (CLI ``ingest.py --regenerate-thumbnails`` / a frames rebuild / the
ingest child spawned by watch.py) rewrites the frames table; the server's
``/api/media/{id}/scenes`` and MCP ``get_scenes`` must see the new rows without a
restart.

The writer runs in a real subprocess (like tests/test_chroma_staleness.py) so no
in-process cache or shared connection can make the test pass by accident. The
reader reads once BEFORE the write, so any read-side cache would have something
to hold onto.
"""
import importlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _rewrite_frames_in_subprocess(db_path, media_id, frames):
    """Delete + re-insert frames for media_id from a separate interpreter, the
    way a CLI regenerate / ingest child would (db.delete_frames + upsert_frame)."""
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, {repo!r})
        import db
        db.set_db_path({db_path!r})
        with db.get_conn() as conn:
            db.delete_frames({mid}, _conn=conn)
            for idx, ts, desc in {frames!r}:
                db.upsert_frame({mid}, idx, ts, description=desc, _conn=conn)
    """).format(repo=str(REPO), db_path=str(db_path), mid=media_id, frames=frames)
    env = dict(os.environ)
    subprocess.run([sys.executable, "-c", script], check=True, env=env,
                   cwd=str(REPO), timeout=60)


def _seed(db, sample_record):
    db.upsert(sample_record(path="/tmp/fresh_a.mp4", duration_s=30.0))
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path=?",
                           ("/tmp/fresh_a.mp4",)).fetchone()["id"]
    db.upsert_frame(mid, 0, 0.0, description="old-0")
    db.upsert_frame(mid, 1, 10.0, description="old-1")
    return mid


def _descs(scenes_body):
    return [s["description"] for s in scenes_body["scenes"]]


def test_http_scenes_sees_frames_rewritten_by_another_process(fastapi_client, tmp_db, sample_record):
    db = importlib.import_module("db")
    mid = _seed(db, sample_record)

    before = fastapi_client.get("/api/media/{0}/scenes".format(mid))
    assert before.status_code == 200
    assert _descs(before.json()) == ["old-0", "old-1"]

    _rewrite_frames_in_subprocess(tmp_db, mid,
                                  [(0, 0.0, "new-0"), (1, 4.0, "new-1"), (2, 20.0, "new-2")])

    after = fastapi_client.get("/api/media/{0}/scenes".format(mid))
    assert after.status_code == 200
    body = after.json()
    assert _descs(body) == ["new-0", "new-1", "new-2"]
    assert body["total"] == 3
    assert [s["start_s"] for s in body["scenes"]] == [0.0, 4.0, 20.0]


def test_mcp_get_scenes_sees_frames_rewritten_by_another_process(tmp_db, sample_record):
    db = importlib.import_module("db")
    mcp_server = importlib.import_module("mcp_server")
    mid = _seed(db, sample_record)

    assert _descs(mcp_server.get_scenes_impl(mid)) == ["old-0", "old-1"]

    _rewrite_frames_in_subprocess(tmp_db, mid, [(0, 0.0, "new-0")])

    body = mcp_server.get_scenes_impl(mid)
    assert _descs(body) == ["new-0"]
    assert body["total"] == 1


def test_get_frames_from_a_long_lived_reader_sees_new_rows(tmp_db, sample_record):
    """db.get_frames opens a fresh connection per call — pin that, because a
    cached module-level / thread-local connection holding a read txn open would
    pin a WAL snapshot and reproduce exactly the restart-to-see symptom."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record)
    assert [f["description"] for f in db.get_frames(mid)] == ["old-0", "old-1"]
    _rewrite_frames_in_subprocess(tmp_db, mid, [(0, 0.0, "x")])
    assert [f["description"] for f in db.get_frames(mid)] == ["x"]


# ── the staleness that DOES exist: same-name keyframe JPEGs ──────────────────
# frames.py writes keyframes as THUMBNAILS_DIR/{stem}_frame{i}.jpg, so a rebuild
# (`ingest.py --regenerate-thumbnails`, re-running vision) overwrites the SAME
# file under the SAME name — and /api/media/{id}/scenes hands back the SAME
# keyframe_url. The JSON is fresh (tests above); the image is not: the route sent
# no Cache-Control, so the webview applied heuristic freshness (a fraction of the
# file's age since Last-Modified — hours to days for an old library) and kept
# painting the pre-rebuild frame without asking. Only a fresh webview, i.e. an app
# restart, dropped it.

def _thumb(server_module, name, data):
    p = server_module.thumbs_dir / name
    p.write_bytes(data)
    return p


def test_thumbnail_requires_revalidation(fastapi_client, server_module):
    p = _thumb(server_module, "fresh_probe_frame0.jpg", b"old-bytes")
    try:
        r = fastapi_client.get("/thumbnails/fresh_probe_frame0.jpg")
        assert r.status_code == 200
        cc = r.headers.get("cache-control", "")
        assert "no-cache" in cc, cc
        assert r.headers.get("etag")
    finally:
        p.unlink()


def test_thumbnail_revalidation_is_cheap_until_the_file_changes(fastapi_client, server_module):
    """no-cache must not turn every scene-strip render into a full re-download
    (the #432 shape: a freshness fix that makes the normal path pay every time).
    Unchanged file + matching ETag → 304 with no body; overwritten file → 200 with
    the new bytes."""
    p = _thumb(server_module, "fresh_probe_frame1.jpg", b"old-bytes")
    try:
        first = fastapi_client.get("/thumbnails/fresh_probe_frame1.jpg")
        etag = first.headers["etag"]

        again = fastapi_client.get("/thumbnails/fresh_probe_frame1.jpg",
                                   headers={"If-None-Match": etag})
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers.get("etag") == etag
        assert "no-cache" in again.headers.get("cache-control", "")

        st = p.stat()
        p.write_bytes(b"new-bytes-after-rebuild")
        os.utime(str(p), (st.st_atime + 5, st.st_mtime + 5))

        rebuilt = fastapi_client.get("/thumbnails/fresh_probe_frame1.jpg",
                                     headers={"If-None-Match": etag})
        assert rebuilt.status_code == 200
        assert rebuilt.content == b"new-bytes-after-rebuild"
        assert rebuilt.headers["etag"] != etag
    finally:
        p.unlink()
