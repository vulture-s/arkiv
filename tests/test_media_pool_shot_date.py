"""`/api/media/pool` carries the day the camera rolled, not the day we ingested.

The pool is the flat list an editor scans. Sorting or scanning it by ingest time
is worse than showing no date at all: the ordering looks authoritative and is
wrong. `routers/media.py` records the measurement that settled this for the
shoot-date facet — on a real 62-clip library, 55 of the 56 dated clips were shot
in a different year than they were ingested.
"""


def _pool_by_filename(client):
    r = client.get("/api/media/pool")
    assert r.status_code == 200
    return {it["filename"]: it for it in r.json()["items"]}


def test_media_pool_carries_shot_date(fastapi_client, tmp_db, sample_record):
    import db

    rec = sample_record(filename="sunset.mp4")
    db.upsert(rec)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE media SET shot_date = '2019-05-04' WHERE path = ?", (rec["path"],)
        )

    assert _pool_by_filename(fastapi_client)["sunset.mp4"]["shot_date"] == "2019-05-04"


def test_media_pool_shot_date_is_not_ingest_time(fastapi_client, tmp_db, sample_record):
    """The invariant worth pinning: a 2019 clip ingested today reads 2019.

    Swapping the column to `processed_at` — or falling back to it when the shoot
    date is missing — would still return a plausible ISO date for every row, so
    nothing would look broken. This is the assertion that would go red.
    """
    import db

    rec = sample_record(filename="old-shoot.mp4")
    db.upsert(rec)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE media SET shot_date = '2019-05-04' WHERE path = ?", (rec["path"],)
        )
        ingested = conn.execute(
            "SELECT processed_at FROM media WHERE path = ?", (rec["path"],)
        ).fetchone()["processed_at"]

    got = _pool_by_filename(fastapi_client)["old-shoot.mp4"]["shot_date"]
    assert got == "2019-05-04"
    # and it is genuinely a different value from the ingest stamp, so the
    # assertion above is not passing by coincidence on a same-day fixture
    assert ingested is None or not str(ingested).startswith("2019-05-04")


def test_media_pool_undated_clip_stays_undated(fastapi_client, tmp_db, sample_record):
    """No readable shoot date → None, not a guess.

    `db.normalise_shot_date` returns None rather than inventing a date, and the
    pool must carry that through: an undated clip should look undated to the
    editor, not be filed under a year nobody verified.
    """
    import db

    rec = sample_record(filename="no-exif.mp4")
    db.upsert(rec)
    with db.get_conn() as conn:
        conn.execute("UPDATE media SET shot_date = NULL WHERE path = ?", (rec["path"],))

    item = _pool_by_filename(fastapi_client)["no-exif.mp4"]
    assert "shot_date" in item, "key must be present so callers can distinguish absent from unknown"
    assert item["shot_date"] is None
