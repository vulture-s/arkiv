"""Content-hash move-detection (fable-audit round-5 #8).

A moved/renamed file (unknown path, content hash matches a row whose stored path is
GONE) must re-point the existing row — preserving its ratings/tags/transcript —
instead of re-ingesting the whole library as duplicates after a reorg. A hash match
whose path STILL exists is a genuine second copy and must NOT be re-pointed (the
original is never silently abandoned) — that is the move-detection semantics Hevin
chose over blanket content-dedup.
"""
import importlib


def test_find_moved_row_detects_and_repoint_preserves_metadata(tmp_db, tmp_path, monkeypatch):
    db = importlib.import_module("db")
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)  # resolve_path roots here

    # a row whose stored file no longer exists on disk (the clip was moved away)
    with db.get_conn() as c:
        c.execute(
            "INSERT INTO media (path, filename, ext, file_hash, hash_algo, rating) "
            "VALUES (?,?,?,?,?,?)",
            ("old/clip.mp4", "clip.mp4", ".mp4", "HASH123", "xxh3", "good"),
        )
        mid = c.execute("SELECT id FROM media WHERE file_hash=?", ("HASH123",)).fetchone()["id"]
    db.add_tag(mid, "keeper")

    row = db.find_moved_row("HASH123")
    assert row is not None and row["id"] == mid  # old path gone → move-detected

    # re-point to the new location; only the path changes
    new_abs = tmp_path / "new" / "clip.mp4"
    new_abs.parent.mkdir(parents=True)
    new_abs.write_bytes(b"x")
    db.repoint_media_path(mid, str(new_abs))

    with db.get_conn() as c:
        r = c.execute("SELECT path, rating FROM media WHERE id=?", (mid,)).fetchone()
        tags = [t["name"] for t in c.execute("SELECT name FROM tags WHERE media_id=?", (mid,))]
        n = c.execute("SELECT COUNT(*) AS n FROM media").fetchone()["n"]
    assert r["path"] == "new/clip.mp4"   # stored relative to PROJECT_ROOT
    assert r["rating"] == "good"          # ratings preserved
    assert tags == ["keeper"]             # tags preserved
    assert n == 1                         # re-pointed, NOT duplicated


def test_find_moved_row_ignores_live_duplicate_and_misses(tmp_db, tmp_path, monkeypatch):
    db = importlib.import_module("db")
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    # a row whose file STILL exists on disk = a genuine second copy, not a move
    existing = tmp_path / "here" / "clip.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"x")
    with db.get_conn() as c:
        c.execute("INSERT INTO media (path, filename, ext, file_hash) VALUES (?,?,?,?)",
                  ("here/clip.mp4", "clip.mp4", ".mp4", "DUP"))

    assert db.find_moved_row("DUP") is None    # live path → NOT re-pointed
    assert db.find_moved_row("") is None        # empty hash
    assert db.find_moved_row("no-such-hash") is None
