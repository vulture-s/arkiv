"""Rows ingested without exiftool stay dateless forever.

`exiftool_extract` returns `{}` when the binary is missing, so a whole ingest run
writes `creation_date` and every camera column as NULL — one banner line is the
only signal. Installing exiftool afterwards fixes nothing: `db._backfill_shot_date`
derives `shot_date` FROM `creation_date`, so NULL stays NULL, and nothing goes back
to the file to read the EXIF again.

What the user sees: those clips are stuck in the `unknown` bucket of the shoot-date
facet, permanently, and the DIT columns are empty.

Two rules this pass must not break:

* **Never overwrite.** A value already in the column — including one a human
  typed — outranks anything re-read from the file.
* **Recompute the derived column.** Writing `creation_date` alone leaves
  `shot_date` NULL and the facet still saying `unknown`, i.e. the symptom
  survives the repair.
"""
from __future__ import annotations

import importlib

import pytest

import db


@pytest.fixture
def script(tmp_db):
    mod = importlib.import_module("backfill_creation_date")
    return importlib.reload(mod)


@pytest.fixture
def exif(monkeypatch):
    """Stand in for exiftool. Pass a dict, or {} for "no exiftool / no EXIF"."""
    import ingest

    def _install(payload):
        monkeypatch.setattr(ingest, "exiftool_extract", lambda p, fps=None: payload)
    return _install


def _seed(sample_record, tmp_path, name="A001.mp4", **over):
    src = tmp_path / name
    src.write_bytes(b"\x00")
    rec = dict(path=str(src), filename=name, ext=".mp4")
    rec.update(over)
    rec.setdefault("creation_date", None)
    db.upsert(sample_record(**rec))
    return src


def _row(mid=1):
    with db.get_conn() as conn:
        return dict(conn.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone())


def test_a_dateless_row_gets_its_date_back(script, exif, sample_record, tmp_path):
    _seed(sample_record, tmp_path)
    exif({"creation_date": "2026:03:14 09:30:00", "camera_make": "Sony"})

    script.main([])

    row = _row()
    assert row["creation_date"] == "2026:03:14 09:30:00"
    assert row["camera_make"] == "Sony"


def test_the_derived_shot_date_is_recomputed(script, exif, sample_record, tmp_path):
    """Without this the clip is still in the `unknown` facet bucket — the symptom
    the whole script exists to clear."""
    _seed(sample_record, tmp_path)
    exif({"creation_date": "2026:03:14 09:30:00"})

    script.main([])

    assert _row()["shot_date"] == "2026-03-14"


def test_an_existing_value_is_never_overwritten(script, exif, sample_record, tmp_path):
    """It might have been typed by a human. A repair pass that overwrites is a
    repair pass that destroys work."""
    _seed(sample_record, tmp_path, camera_make="手動填的")
    exif({"creation_date": "2026:03:14 09:30:00", "camera_make": "Sony"})

    script.main([])

    assert _row()["camera_make"] == "手動填的"


def test_rows_that_already_have_a_date_are_not_even_considered(
    script, exif, sample_record, tmp_path
):
    _seed(sample_record, tmp_path, creation_date="2026:01:01 00:00:00")
    with db.get_conn() as conn:
        assert script.rows_needing_backfill(conn) == []


def test_dry_run_changes_nothing(script, exif, sample_record, tmp_path, capsys):
    _seed(sample_record, tmp_path)
    exif({"creation_date": "2026:03:14 09:30:00"})

    script.main(["--dry-run"])

    assert _row()["creation_date"] is None
    assert "would write 1" in capsys.readouterr().out


def test_an_unreachable_file_is_skipped_not_failed(
    script, exif, sample_record, tmp_path, capsys
):
    """The NAS being unplugged is a "run it again later", not an error."""
    src = _seed(sample_record, tmp_path)
    src.unlink()
    exif({"creation_date": "2026:03:14 09:30:00"})

    assert script.main([]) == 0
    assert "1 unreachable" in capsys.readouterr().out


def test_a_file_with_genuinely_no_exif_is_reported_separately(
    script, exif, sample_record, tmp_path, capsys
):
    """"exiftool is missing" and "this file has no metadata" look identical from
    here — both return {} — but the count tells the user which run to repeat."""
    _seed(sample_record, tmp_path)
    exif({})

    script.main([])

    assert "1 without EXIF" in capsys.readouterr().out
    assert _row()["creation_date"] is None


def test_an_unparseable_date_still_writes_the_raw_value(
    script, exif, sample_record, tmp_path
):
    """`creation_date` is stored raw on purpose (cameras disagree about format);
    only the derived column is allowed to be NULL when it can't be read."""
    _seed(sample_record, tmp_path)
    exif({"creation_date": "not a date"})

    script.main([])

    row = _row()
    assert row["creation_date"] == "not a date"
    assert row["shot_date"] is None


def test_limit_stops_early(script, exif, sample_record, tmp_path):
    for i in range(3):
        _seed(sample_record, tmp_path, name="A00{0}.mp4".format(i))
    exif({"creation_date": "2026:03:14 09:30:00"})

    script.main(["--limit", "1"])

    dated = [r for r in (_row(i) for i in (1, 2, 3)) if r["creation_date"]]
    assert len(dated) == 1


def test_editorial_fields_are_out_of_scope(script):
    """Only file-derived fields are re-read. Anything a human sets — tags, rating,
    in/out, camera_id — must never be touched by a repair pass."""
    for human in ("tags", "rating", "in_point", "out_point", "camera_id", "angle",
                  "transcript"):
        assert human not in script._EXIF_FIELDS
