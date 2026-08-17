"""Sidebar time entry point — browse footage by the year it was SHOT.

Two things this has to get right, both measured on a real library before it was
built rather than assumed:

1. **Which column.** `creation_date` (EXIF CreateDate / XAVC sidecar), never
   `processed_at`. On a real 62-clip library, 55 of the 56 dated clips were shot in a
   different year than they were ingested, and every single row shared one
   `processed_at` year — a facet built on the ingest date collapses to one bucket.

2. **Two stored shapes.** `ingest.exiftool_extract` writes `str(cdate)` with no
   normalisation, and there are two writers: exiftool yields
   `2025:10:03 13:56:20`, the XAVC NRT sidecar yields ISO `2025-10-03T13:56:20+08:00`.
   Both are YYYY-prefixed, so grouping by year survives naively — but ordering does
   not, because ':' sorts above '-'. Anything past "what year" goes through
   `db.normalise_shot_date`.

The filter also has to hold on all three code paths in `/api/media` (SQL list,
semantic search, degraded SQL text search). A filter honoured only on the first is
the shape of audits H8 and H14 — it silently stops applying the moment the user
types a query, or the moment Ollama is down.
"""
import importlib

import pytest


@pytest.fixture
def db():
    return importlib.import_module("db")


# ---------------------------------------------------------------- normalisation

@pytest.mark.parametrize("raw,expected", [
    ("2025:10:03 13:56:20", "2025-10-03"),          # exiftool, the common case
    ("2025-10-03T13:56:20+08:00", "2025-10-03"),    # XAVC NRT sidecar (ISO + tz)
    ("2025-10-03T13:56:20Z", "2025-10-03"),
    ("2025-10-03 13:56:20", "2025-10-03"),
    ("2025:10:03", "2025-10-03"),
    ("2025-10-03", "2025-10-03"),
    ("2025-10-03T13:56:20.123+08:00", "2025-10-03"),  # sub-second precision
    ("  2025:10:03 13:56:20  ", "2025-10-03"),
])
def test_both_stored_date_shapes_normalise_to_one_iso_day(db, raw, expected):
    assert db.normalise_shot_date(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "0000:00:00 00:00:00", "not a date", "2025", "Sony", "-",
])
def test_unreadable_dates_return_none_rather_than_a_guess(db, raw):
    """A clip whose date can't be read belongs in an explicit unknown bucket — not
    filed under a plausible-looking year, and not silently dropped."""
    assert db.normalise_shot_date(raw) is None
    assert db.shot_year(raw) is None


def test_timezone_is_dropped_not_converted(db):
    """Re-basing to UTC would move footage shot near midnight into the neighbouring
    day. For a shoot-day facet that is the wrong grouping, so the offset is discarded
    rather than applied."""
    assert db.normalise_shot_date("2026-01-01T00:30:00+08:00") == "2026-01-01"
    assert db.normalise_shot_date("2026-01-01T23:30:00-05:00") == "2026-01-01"


def test_year_survives_both_shapes(db):
    assert db.shot_year("2025:10:03 13:56:20") == "2025"
    assert db.shot_year("2025-10-03T13:56:20+08:00") == "2025"


# ---------------------------------------------------------------------- facets

def _seed(db, sample_record, dates):
    for i, d in enumerate(dates):
        rec = sample_record(path="/tmp/shot{0}.mp4".format(i))
        rec["creation_date"] = d
        db.upsert(rec)


def test_facet_buckets_by_shoot_year_newest_first(tmp_db, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record, [
        "2025:10:03 13:56:20", "2025:11:01 09:00:00",
        "2024-06-01T10:00:00+08:00",
        "2022:01:01 00:00:01",
    ])

    facets = db.get_shoot_date_facets()

    assert [b["year"] for b in facets["years"]] == ["2025", "2024", "2022"]
    assert {b["year"]: b["count"] for b in facets["years"]} == \
        {"2025": 2, "2024": 1, "2022": 1}


def test_undated_clips_are_counted_not_dropped(tmp_db, sample_record):
    """The counts must reconcile with the library total — a facet that quietly omits
    rows reads as 'there is nothing there'."""
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025:10:03 13:56:20", None, "", "garbage"])

    facets = db.get_shoot_date_facets()

    assert facets["unknown"] == 3
    assert facets["total"] == 4
    assert sum(b["count"] for b in facets["years"]) + facets["unknown"] == facets["total"]


def test_facet_does_not_group_on_the_ingest_date(tmp_db, sample_record):
    """The measured failure mode: every row shares a processed_at year, so grouping on
    it yields one meaningless bucket."""
    db = importlib.import_module("db")
    for i, shot in enumerate(["2025:01:01 00:00:00", "2024:01:01 00:00:00"]):
        rec = sample_record(path="/tmp/x{0}.mp4".format(i))
        rec["creation_date"] = shot
        rec["processed_at"] = "2026-08-09T00:00:00"  # identical for both
        db.upsert(rec)

    years = [b["year"] for b in db.get_shoot_date_facets()["years"]]
    assert years == ["2025", "2024"], "must reflect shoot year, not ingest year"


# ---------------------------------------------------------------------- filter

def test_filter_clause_selects_one_year_across_both_shapes(tmp_db, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record, [
        "2025:10:03 13:56:20",              # exiftool shape
        "2025-02-01T08:00:00+08:00",        # ISO shape, same year
        "2024:10:03 13:56:20",
        None,
    ])

    rows, total = db.get_media_filtered(shot_year="2025", limit=100)
    assert total == 2, "both stored shapes must match the same year"
    assert all(db.shot_year(r["creation_date"]) == "2025" for r in rows)


def test_unknown_bucket_is_selectable(tmp_db, sample_record):
    """Otherwise undated clips become unreachable from the sidebar as soon as any
    year is picked."""
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025:10:03 13:56:20", None, "", "garbage"])

    rows, total = db.get_media_filtered(shot_year=db.UNKNOWN_SHOT_YEAR, limit=100)
    assert total == 3
    assert all(db.shot_year(r["creation_date"]) is None for r in rows)


def test_no_shot_year_filter_returns_everything(tmp_db, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025:10:03 13:56:20", None])
    _, total = db.get_media_filtered(limit=100)
    assert total == 2


def test_creation_date_is_in_the_light_shape(tmp_db, sample_record):
    """The semantic-search branch filters enriched LIGHT records rather than in SQL,
    so a column absent from LIGHT_COLS is a filter that does nothing on ?q=."""
    db = importlib.import_module("db")
    assert "creation_date" in db.LIGHT_COLS
    _seed(db, sample_record, ["2025:10:03 13:56:20"])
    rows, _ = db.get_media_filtered(limit=1)
    assert "creation_date" in rows[0]


# ------------------------------------------------------------------- HTTP layer

def test_facet_endpoint_shape(fastapi_client, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025:10:03 13:56:20", "2024-06-01T10:00:00+08:00", None])

    r = fastapi_client.get("/api/media/facets/shoot-date")
    assert r.status_code == 200
    body = r.json()
    assert [b["year"] for b in body["years"]] == ["2025", "2024"]
    assert body["unknown"] == 1
    assert body["total"] == 3


def test_facet_route_is_not_shadowed_by_the_media_id_route(fastapi_client, sample_record):
    """`/api/media/{media_id}` is declared in the same router; if it were matched
    first, this path would try to parse 'facets' as an id and 422."""
    r = fastapi_client.get("/api/media/facets/shoot-date")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("query", ["", "?q=clip"])
def test_shot_year_filter_holds_on_every_branch(fastapi_client, sample_record, query):
    """The regression guard that matters. `/api/media` has three code paths — plain
    SQL list, semantic search, and the degraded SQL text search that search falls
    back to. Audits H8 and H14 were both 'filter honoured on one path only'. With no
    embeddings configured in tests, `?q=` exercises the fallback path."""
    db = importlib.import_module("db")
    for i, (name, date) in enumerate([
        ("clip-a.mp4", "2025:10:03 13:56:20"),
        ("clip-b.mp4", "2025-02-01T08:00:00+08:00"),
        ("clip-c.mp4", "2024:10:03 13:56:20"),
        ("clip-d.mp4", None),
    ]):
        rec = sample_record(path="/tmp/{0}".format(name), filename=name)
        rec["creation_date"] = date
        db.upsert(rec)

    r = fastapi_client.get("/api/media{0}{1}shot_year=2025".format(
        query, "&" if query else "?"))
    assert r.status_code == 200, r.text
    got = sorted(item["filename"] for item in r.json()["items"])
    assert got == ["clip-a.mp4", "clip-b.mp4"], \
        "shot_year must apply on this branch too, not silently pass everything"


@pytest.mark.parametrize("query", ["", "?q=clip"])
def test_unknown_bucket_reachable_over_http_on_every_branch(
    fastapi_client, sample_record, query
):
    db = importlib.import_module("db")
    for i, (name, date) in enumerate([
        ("clip-a.mp4", "2025:10:03 13:56:20"),
        ("clip-d.mp4", None),
        ("clip-e.mp4", "garbage"),
    ]):
        rec = sample_record(path="/tmp/{0}".format(name), filename=name)
        rec["creation_date"] = date
        db.upsert(rec)

    r = fastapi_client.get("/api/media{0}{1}shot_year=unknown".format(
        query, "&" if query else "?"))
    assert r.status_code == 200, r.text
    got = sorted(item["filename"] for item in r.json()["items"])
    assert got == ["clip-d.mp4", "clip-e.mp4"]


# ------------------------------------------- day granularity + bucket reconciliation

# Deliberately mixes both real writers with the values that break shape-only reasoning:
# one day reached through two different separators, a date-only value sharing a day
# with a full timestamp, a near-midnight offset, and five flavours of unreadable —
# including one that is date-PREFIXED but not a date.
_ADVERSARIAL_CORPUS = [
    "2025:10:03 13:56:20",          # exiftool
    "2025-10-03T20:10:00+08:00",    # XAVC sidecar — SAME day, other separator
    "2025-10-03T00:30:00+08:00",    # near midnight; tz dropped, so it stays put
    "2025:10:02 08:00:00",
    "2025:10:02",                   # date-only, same day as the row above
    "2024-06-01T10:00:00Z",
    "2022:01:01 00:00:01",
    None,
    "",
    "   ",
    "garbage",
    "2025",                         # year-shaped, not a date
    "2025-10-03XGARBAGE",           # date-prefixed, not a date
    # Shape-perfect, calendar-impossible. One per component, because each is caught
    # by a different range check and a corpus that only carries the zero sentinel
    # cannot tell whether the month guard is doing anything (its day is also 00).
    "0000:00:00 00:00:00",          # exiftool's unset-field sentinel: all zero
    "0000:01:01 00:00:00",          # year only
    "2025:13:05 10:00:00",          # month only
    "2025:10:00 10:00:00",          # day only
]


def test_facet_reports_shoot_days_within_each_year_newest_first(tmp_db, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record, _ADVERSARIAL_CORPUS)

    years = {b["year"]: b for b in db.get_shoot_date_facets()["years"]}

    assert [d["date"] for d in years["2025"]["days"]] == ["2025-10-03", "2025-10-02"]
    assert [d["count"] for d in years["2025"]["days"]] == [3, 2]
    # Days must sum to their year, or the drill-down loses rows on the way in.
    for bucket in years.values():
        assert sum(d["count"] for d in bucket["days"]) == bucket["count"], bucket["year"]


def test_every_facet_bucket_returns_exactly_the_rows_it_promises(tmp_db, sample_record):
    """The property the whole feature rests on, and the reason it is automated here.

    #291 verified it once, by hand, against the real library. A bucket that advertises
    54 and hands back 41 is worse than having no facet: the number is the only thing
    telling the user what is in there. Every year, every day and the unknown bucket
    are checked, and together they must partition the library exactly.
    """
    db = importlib.import_module("db")
    _seed(db, sample_record, _ADVERSARIAL_CORPUS)
    facets = db.get_shoot_date_facets()

    counted = 0
    for bucket in facets["years"]:
        _, year_total = db.get_media_filtered(shot_year=bucket["year"], limit=1000)
        assert year_total == bucket["count"], "year {0}".format(bucket["year"])
        for day in bucket["days"]:
            _, day_total = db.get_media_filtered(shot_date=day["date"], limit=1000)
            assert day_total == day["count"], "day {0}".format(day["date"])
        counted += bucket["count"]

    _, unknown_total = db.get_media_filtered(shot_year=db.UNKNOWN_SHOT_YEAR, limit=1000)
    assert unknown_total == facets["unknown"]
    counted += facets["unknown"]

    assert counted == facets["total"] == len(_ADVERSARIAL_CORPUS)


def test_day_filter_matches_across_both_stored_shapes(tmp_db, sample_record):
    """The year survives a naive prefix compare because both writers start with YYYY.
    The day does not — the separators differ at characters 5 and 8."""
    db = importlib.import_module("db")
    _seed(db, sample_record, [
        "2025:10:03 13:56:20", "2025-10-03T20:10:00+08:00", "2025:10:02 08:00:00",
    ])

    rows, total = db.get_media_filtered(shot_date="2025-10-03", limit=100)
    assert total == 2, "both stored shapes must match the same day"
    assert all(db.normalise_shot_date(r["creation_date"]) == "2025-10-03" for r in rows)


def test_a_date_prefixed_but_unreadable_value_is_unknown_to_both_sides(tmp_db, sample_record):
    """`2025-10-03XGARBAGE` is what separates a shape check from a real one.

    The facet rejects it, so the filters have to as well — admitting it would mean
    clicking 2025-10-03 returns a row the sidebar counted in a different bucket.
    """
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025:10:03 13:56:20", "2025-10-03XGARBAGE"])

    assert db.get_shoot_date_facets()["unknown"] == 1
    _, day_total = db.get_media_filtered(shot_date="2025-10-03", limit=100)
    assert day_total == 1, "a shape-only match must not be admitted by the day filter"
    _, year_total = db.get_media_filtered(shot_year="2025", limit=100)
    assert year_total == 1, "nor by the year filter, which shares the same guard"
    _, unknown_total = db.get_media_filtered(shot_year=db.UNKNOWN_SHOT_YEAR, limit=100)
    assert unknown_total == 1


def test_year_and_day_compose_instead_of_one_overriding_the_other(tmp_db, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025:10:03 13:56:20", "2024:10:03 13:56:20"])

    _, agreeing = db.get_media_filtered(shot_year="2025", shot_date="2025-10-03", limit=100)
    assert agreeing == 1
    # Contradictory input returns nothing rather than quietly answering one half of it.
    _, contradictory = db.get_media_filtered(shot_year="2024", shot_date="2025-10-03", limit=100)
    assert contradictory == 0


@pytest.mark.parametrize("raw,year,day", [
    ("0000:00:00 00:00:00", "0000", "0000-00-00"),  # exiftool's unset-field sentinel
    ("0000:01:01 00:00:00", "0000", "0000-01-01"),  # year out of range on its own
    ("2025:13:05 10:00:00", "2025", "2025-13-05"),  # month out of range on its own
    ("2025:10:00 10:00:00", "2025", "2025-10-00"),  # day out of range on its own
])
def test_shape_valid_but_impossible_dates_are_unknown_to_the_filter_too(
    tmp_db, sample_record, raw, year, day
):
    """A date can be shape-perfect and still not exist.

    `0000:00:00 00:00:00` is the one that matters in practice — it is what exiftool
    writes for an UNSET date field, so it is common, not exotic — and the
    reconciliation test is what caught it: a shape-only guard filed it under year 0000
    while the facet correctly called it unreadable.

    Each component is parametrised separately on purpose. With only the all-zero
    sentinel, removing the month check changes nothing (its day is 00 too), so the
    test would keep passing while the guard rotted.
    """
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025:10:03 13:56:20", raw])

    facets = db.get_shoot_date_facets()
    assert facets["unknown"] == 1
    assert [b["year"] for b in facets["years"]] == ["2025"]

    _, unknown_total = db.get_media_filtered(shot_year=db.UNKNOWN_SHOT_YEAR, limit=100)
    assert unknown_total == 1, "the facet called it unreadable; the filter must agree"
    _, as_year = db.get_media_filtered(shot_year=year, limit=100)
    assert as_year == (1 if year == "2025" else 0), "must not be filed under a real year"
    _, as_day = db.get_media_filtered(shot_date=day, limit=100)
    assert as_day == 0, "must not be reachable as a day either"


def test_impossible_calendar_dates_are_the_known_residual(tmp_db, sample_record):
    """`_DATED_SQL` validates component RANGES, not the calendar.

    So `2025-02-30` — day 30 of a 28-day month — reads as dated to the filter and as
    unknown to the facet. Pinned rather than fixed: no writer produces it (exiftool and
    the XAVC parser both format an already-parsed date, and the unset-field case is the
    zero sentinel covered above), and closing it needs a real calendar, i.e. a stored
    normalised column rather than a predicate over raw text. If this test ever fails
    because the two now agree, the residual is gone and the note on `db._DATED_SQL`
    should go with it.
    """
    db = importlib.import_module("db")
    _seed(db, sample_record, ["2025-02-30T00:00:00"])

    assert db.get_shoot_date_facets()["unknown"] == 1
    _, day_total = db.get_media_filtered(shot_date="2025-02-30", limit=100)
    assert day_total == 1, "known divergence: SQL admits a range the facet rejected"


def _seed_named(db, sample_record, rows):
    for name, date in rows:
        rec = sample_record(path="/tmp/{0}".format(name), filename=name)
        rec["creation_date"] = date
        db.upsert(rec)


_BRANCH_ROWS = [
    ("clip-a.mp4", "2025:10:03 13:56:20"),
    ("clip-b.mp4", "2025-10-03T20:10:00+08:00"),
    ("clip-c.mp4", "2025:10:02 08:00:00"),
    ("clip-d.mp4", None),
]


@pytest.mark.parametrize("query", ["", "?q=clip"])
def test_shot_date_filter_holds_on_every_branch(fastapi_client, sample_record, query):
    """Same guard as the year, one level finer. With no embeddings configured, `?q=`
    exercises the degraded SQL fallback — the path a user hits when Ollama is down."""
    db = importlib.import_module("db")
    _seed_named(db, sample_record, _BRANCH_ROWS)

    r = fastapi_client.get("/api/media{0}{1}shot_date=2025-10-03".format(
        query, "&" if query else "?"))
    assert r.status_code == 200, r.text
    got = sorted(item["filename"] for item in r.json()["items"])
    assert got == ["clip-a.mp4", "clip-b.mp4"], \
        "shot_date must apply on this branch too, not silently pass everything"


def test_shot_date_applies_on_the_semantic_branch(fastapi_client, sample_record, monkeypatch):
    """The branch the `?q=` cases above cannot reach.

    Without embeddings they fall straight through to the degraded SQL path, so the
    Python predicate that filters semantic hits has never actually been exercised by a
    test — it is a third, independently-written copy of the same rule. Faking the
    vector store is what reaches it.
    """
    import vectordb

    db = importlib.import_module("db")
    _seed_named(db, sample_record, _BRANCH_ROWS)
    rows, _ = db.get_media_filtered(limit=100)
    hits = [{"media_id": r["id"], "score": 0.9, "excerpt": ""} for r in rows]
    monkeypatch.setattr(vectordb, "search", lambda q, n_results=10: hits, raising=False)

    r = fastapi_client.get("/api/media?q=anything&shot_date=2025-10-03")
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body.get("search_degraded"), "this case must not fall back to SQL"
    assert sorted(i["filename"] for i in body["items"]) == ["clip-a.mp4", "clip-b.mp4"]


def test_frontend_agrees_on_the_unknown_bucket_string():
    """The sidebar sends this value back as `?shot_year=`, so the two must match.

    A typo on either side reads correctly in isolation: the facet still counts the
    undated clips, the filter still accepts a string, and the row simply clicks
    through to an empty grid. Nothing fails — which is why it is pinned here rather
    than left to whoever notices.
    """
    import pathlib
    import re

    db = importlib.import_module("db")
    src = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "shotYear.js"
    assert src.exists(), src
    m = re.search(
        r"export\s+const\s+UNKNOWN_SHOT_YEAR\s*=\s*['\"]([^'\"]+)['\"]",
        src.read_text(encoding="utf-8"),
    )
    assert m, "UNKNOWN_SHOT_YEAR is no longer declared where this test can read it"
    assert m.group(1) == db.UNKNOWN_SHOT_YEAR
