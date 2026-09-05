"""A deleted clip's id gets handed to the next one, and the bin never noticed.

`media.id` is `INTEGER PRIMARY KEY` — no AUTOINCREMENT — so SQLite is free to
reuse the rowid of a deleted row. Delete clip 7, ingest anything else, and the
new clip IS clip 7. Every 精選集 entry still pointing at 7 then resolves cleanly,
status `ok`, to footage the user never put there. 精選集 is also what feeds the
cross-project copy, so the wrong clip does not just get shown: it gets copied.

`remove_media_everywhere` closes the window for deletes made from now on. It
cannot help the two populations that already exist — entries added before it
shipped (every install today) and entries whose cleanup failed — and for those
the only defence is noticing the swap. That is what these tests pin.

Retiring this: if `media.id` ever gains AUTOINCREMENT, the premise test below
fails, and it is the one that says the rest is still load-bearing.
"""
import importlib
import sqlite3

import pytest

bins = importlib.import_module("bins")


def _project(tmp_path, name, rows, *, make_files=True):
    """A registrable project whose media table is spelled exactly as production
    spells it — `INTEGER PRIMARY KEY`, no AUTOINCREMENT. `rows` is a list of
    (id_or_None, path, filename); None lets SQLite assign, which is the whole
    point of the reuse test."""
    root = tmp_path / name
    arkiv = root / ".arkiv"
    arkiv.mkdir(parents=True, exist_ok=True)
    (arkiv / "chroma_db").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(arkiv / "project.db"))
    conn.execute(
        "CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT, filename TEXT, "
        "duration_s REAL, rating TEXT, lang TEXT, ext TEXT, transcript TEXT)"
    )
    for mid, path, filename in rows:
        conn.execute("INSERT INTO media (id, path, filename) VALUES (?, ?, ?)",
                     (mid, path, filename))
        if make_files:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"stub-media")
    conn.commit()
    conn.close()
    return root


def _register(tmp_path, monkeypatch, name, root):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    projects = importlib.import_module("projects")
    projects.add_project(name, str(root))
    return projects


@pytest.fixture(autouse=True)
def isolated_bins(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "bins.json"))
    yield


def _swap(root, old_id, new_path, new_filename):
    """Delete a row and ingest another, exactly as a delete + ingest would.
    Returns the id SQLite assigned to the newcomer."""
    conn = sqlite3.connect(str(root / ".arkiv" / "project.db"))
    conn.execute("DELETE FROM media WHERE id = ?", (old_id,))
    cur = conn.execute("INSERT INTO media (path, filename) VALUES (?, ?)",
                       (new_path, new_filename))
    assigned = cur.lastrowid
    conn.commit()
    conn.close()
    target = root / new_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"different-media")
    return assigned


# ── the premise ──────────────────────────────────────────────────────────────
def test_sqlite_hands_a_deleted_id_to_the_next_clip(tmp_path):
    """Not a test of arkiv — a test of the assumption everything below rests on.
    If this ever fails, the schema gained AUTOINCREMENT and the filename check
    below is dead weight that can be removed.

    Also pins WHICH id gets reused, which is the part that is easy to get
    backwards: without AUTOINCREMENT SQLite assigns `max(rowid) + 1`, so a hole
    in the middle is never filled — only the HIGHEST id comes back. That reads
    like it narrows the blast radius, and it does the opposite. The highest id is
    the most recently ingested clip, which is exactly the one someone just
    added to a bin, looked at again, and deleted."""
    root = _project(tmp_path, "lib",
                    [(1, "clips/黑沙灘.mov", "黑沙灘.mov"),
                     (2, "clips/海邊.mov", "海邊.mov")])

    assert _swap(root, 2, "clips/婚禮.mp4", "婚禮.mp4") == 2, \
        "the highest id was handed straight to the next clip"

    root2 = _project(tmp_path, "lib2",
                     [(1, "clips/黑沙灘.mov", "黑沙灘.mov"),
                      (2, "clips/海邊.mov", "海邊.mov")])
    assert _swap(root2, 1, "clips/婚禮.mp4", "婚禮.mp4") == 3, \
        "a hole below the maximum is left alone"


# ── the gate ─────────────────────────────────────────────────────────────────
def test_a_reused_id_is_not_reported_ok(tmp_path, monkeypatch):
    """The failure this whole file exists for. Before the filename check this
    returned STATUS_OK and the bin showed the wrong clip without a mark."""
    root = _project(tmp_path, "lib", [(1, "clips/黑沙灘.mov", "黑沙灘.mov")])
    _register(tmp_path, monkeypatch, "lib", root)
    _swap(root, 1, "clips/婚禮.mp4", "婚禮.mp4")

    assert bins.bin_item_status("lib", "1", "黑沙灘.mov") == bins.STATUS_ID_REUSED


def test_the_clip_that_is_still_itself_stays_ok(tmp_path, monkeypatch):
    """The other half: no false positive on the overwhelmingly common case."""
    root = _project(tmp_path, "lib", [(1, "clips/黑沙灘.mov", "黑沙灘.mov")])
    _register(tmp_path, monkeypatch, "lib", root)

    assert bins.bin_item_status("lib", "1", "黑沙灘.mov") == bins.STATUS_OK


def test_an_item_that_never_stored_a_filename_is_trusted(tmp_path, monkeypatch):
    """A documented compromise, not an oversight. Items added by a tuple caller,
    or before the field was populated, have nothing to compare — and reporting
    every one of them as swapped would be worse than missing the real ones."""
    root = _project(tmp_path, "lib", [(1, "clips/黑沙灘.mov", "黑沙灘.mov")])
    _register(tmp_path, monkeypatch, "lib", root)
    _swap(root, 1, "clips/婚禮.mp4", "婚禮.mp4")

    assert bins.bin_item_status("lib", "1", "") == bins.STATUS_OK
    assert bins.bin_item_status("lib", "1") == bins.STATUS_OK, "default is trust"


def test_whitespace_is_not_a_difference_on_either_side(tmp_path, monkeypatch):
    """Both sides get stripped, and both directions need pinning.

    The asymmetric case is the realistic one: the stored name travels through the
    browser and JSON, the row name comes straight out of SQLite, and anything in
    between that trims for display would leave the two differing by whitespace
    alone. That is a false 'this is a different clip' on a clip that never
    moved — the failure mode that makes a warning worth ignoring."""
    root = _project(tmp_path, "lib", [(1, "clips/黑沙灘.mov", "黑沙灘.mov")])
    _register(tmp_path, monkeypatch, "lib", root)
    assert bins.bin_item_status("lib", "1", "  黑沙灘.mov  ") == bins.STATUS_OK

    padded = _project(tmp_path, "lib2", [(1, "clips/黑沙灘.mov", "  黑沙灘.mov  ")])
    _register(tmp_path, monkeypatch, "lib2", padded)
    assert bins.bin_item_status("lib2", "1", "黑沙灘.mov") == bins.STATUS_OK


def test_a_missing_row_still_reads_as_missing_not_as_a_swap(tmp_path, monkeypatch):
    """Order matters: row_missing is checked first, and it is the more accurate
    answer. A swap needs a row to swap TO."""
    root = _project(tmp_path, "lib", [(1, "clips/黑沙灘.mov", "黑沙灘.mov")])
    _register(tmp_path, monkeypatch, "lib", root)

    assert bins.bin_item_status("lib", "999", "黑沙灘.mov") == bins.STATUS_ROW_MISSING


# ── the batch path has to agree ──────────────────────────────────────────────
def test_the_batched_probe_catches_it_too(tmp_path, monkeypatch):
    """The UI reads statuses through the batch. A check that only lives in the
    per-item path protects nothing a user would ever see."""
    root = _project(tmp_path, "lib",
                    [(1, "clips/黑沙灘.mov", "黑沙灘.mov"),
                     (2, "clips/海邊.mov", "海邊.mov")])
    _register(tmp_path, monkeypatch, "lib", root)
    # id 2, not 1 — see the premise test: only the highest id is ever reused.
    _swap(root, 2, "clips/婚禮.mp4", "婚禮.mp4")

    b = bins.create_bin("A")
    bins.add_items(b.id, [
        {"project_name": "lib", "media_id": 1, "filename": "黑沙灘.mov"},
        {"project_name": "lib", "media_id": 2, "filename": "海邊.mov"},
    ])
    got = bins.bin_item_statuses(bins.get_bin(b.id).items)

    assert got[("lib", "2")] == bins.STATUS_ID_REUSED
    assert got[("lib", "1")] == bins.STATUS_OK, "the untouched neighbour must not be flagged"


def test_tuple_callers_still_work(tmp_path, monkeypatch):
    """The batch accepts bare (name, id) tuples, which carry no filename. They
    must keep working rather than blowing up on the missing attribute."""
    root = _project(tmp_path, "lib", [(1, "clips/黑沙灘.mov", "黑沙灘.mov")])
    _register(tmp_path, monkeypatch, "lib", root)

    assert bins.bin_item_statuses([("lib", 1)]) == {("lib", "1"): bins.STATUS_OK}


# ── the copy is the expensive failure ────────────────────────────────────────
def test_resolve_source_withholds_the_path_for_a_reused_id(tmp_path, monkeypatch):
    """`resolve_source` is what the cross-project copy asks for the file to
    read. A reused id getting an absolute path back here means the wrong footage
    is physically copied into another project and ingested there — the one
    outcome in this whole bug that cannot be undone by fixing a JSON file."""
    root = _project(tmp_path, "lib", [(1, "clips/黑沙灘.mov", "黑沙灘.mov")])
    _register(tmp_path, monkeypatch, "lib", root)
    _swap(root, 1, "clips/婚禮.mp4", "婚禮.mp4")

    info = bins.resolve_source("lib", 1, "黑沙灘.mov")

    assert info["status"] == bins.STATUS_ID_REUSED
    assert info["absolute_path"] == "", "the copy gate reads this; it must stay empty"
