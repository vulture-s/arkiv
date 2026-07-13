"""Query-scale hardening (fable-audit round-5 #20/#21).

#21: a broad structured/semantic query can match thousands of ids; the id→record
fetch must chunk its IN() so it never exceeds SQLite's max variable count.
#20: the common gallery/search columns must be indexed.
"""
import importlib


def test_light_records_chunks_large_id_list(server_module):
    """#21: >500 ids are fetched across chunks, preserving input order with no drops
    or duplicates (pre-fix a single unbounded IN() risked 'too many SQL variables')."""
    db = importlib.import_module("db")
    import server

    N = 1200  # spans multiple 500-id chunks
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO media (path, filename, ext) VALUES (?,?,?)",
            [("scale/p{0}.mp4".format(i), "f{0}.mp4".format(i), ".mp4") for i in range(N)],
        )
        ids = [r["id"] for r in c.execute("SELECT id FROM media ORDER BY id").fetchall()]
    assert len(ids) == N

    ordered = list(reversed(ids))  # arbitrary order → must be preserved
    recs = server._get_light_records_by_ids(ordered)
    assert [r["id"] for r in recs] == ordered


def test_query_indexes_created(tmp_db):
    """#20: init_db creates the media/tags query indexes."""
    db = importlib.import_module("db")
    with db.get_conn() as c:
        names = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    for name in ("idx_media_processed_at", "idx_media_lang", "idx_media_rating",
                 "idx_media_filename", "idx_tags_name_media"):
        assert name in names, "missing index {0}".format(name)
