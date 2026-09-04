"""Deleting a clip clears it out of every 精選集 that held it.

A bin references clips by `(registry name, media_id)`. Deleting one used to leave
that reference behind in every bin, permanently: `delete_media_full` removes the
row, so the id is gone, and restoring from the recycle bin RE-INGESTS the file —
which mints a new id. The old entry can never resolve again. It sits at
`ROW_MISSING` forever and still counts toward the bin's size.

That the reference-integrity gate shows it as broken rather than silently wrong is
why this was a papercut rather than data loss — and also why nobody had to fix it,
which is how it survived.

🔴 The name has to be the REGISTRY name, not the directory basename. A library
registered as 「婚禮案素材庫」 can live in a folder called `wedding`; matching on the
basename silently matches nothing and the whole cleanup becomes a no-op that looks
like it ran.
"""
import importlib

import pytest

bins = importlib.import_module("bins")
projects = importlib.import_module("projects")


@pytest.fixture(autouse=True)
def isolated_bins(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "bins.json"))
    yield


def _bin_with(name, items):
    b = bins.create_bin(name)
    bins.add_items(b.id, items)
    return bins.get_bin(b.id)


def _item(project, mid, filename="clip.mp4"):
    return {"project_name": project, "media_id": mid, "filename": filename}


# ── remove_media_everywhere ──────────────────────────────────────────────────
def test_removes_the_clip_from_every_bin_that_held_it():
    a = _bin_with("A", [_item("庫一", 7), _item("庫一", 8)])
    b = _bin_with("B", [_item("庫一", 7)])
    c = _bin_with("C", [_item("庫一", 9)])

    touched = bins.remove_media_everywhere("庫一", 7)

    assert touched == 2, "two bins held it; the third must not be counted"
    assert [i.media_id for i in bins.get_bin(a.id).items] == ["8"]
    assert bins.get_bin(b.id).items == []
    assert [i.media_id for i in bins.get_bin(c.id).items] == ["9"]


def test_a_clip_in_no_bin_touches_nothing():
    a = _bin_with("A", [_item("庫一", 7)])
    before = bins.get_bin(a.id).updated_at

    assert bins.remove_media_everywhere("庫一", 999) == 0
    assert bins.get_bin(a.id).updated_at == before, "an untouched bin must not be re-stamped"


def test_no_match_does_not_rewrite_the_file(monkeypatch):
    """Every delete calls this, and most clips are in no bin at all. Rewriting a
    shared JSON on each one is a pointless write against a file other requests
    are reading — invisible in the data, which is why it needs asserting on the
    call rather than on the contents."""
    _bin_with("A", [_item("庫一", 7)])
    writes = {"n": 0}
    real = bins.save_bins
    monkeypatch.setattr(bins, "save_bins",
                        lambda data: (writes.__setitem__("n", writes["n"] + 1), real(data))[1])

    bins.remove_media_everywhere("庫一", 999)
    assert writes["n"] == 0

    bins.remove_media_everywhere("庫一", 7)
    assert writes["n"] == 1, "a real removal still has to persist"


def test_the_same_media_id_in_another_project_is_left_alone():
    """media_id is per-project auto-increment, so id 7 exists in every library.
    Removing it from one must not reach into the others.

    Written straight to the bins file rather than through `add_items`: a bin
    spanning two projects is a Pro feature and `add_items` refuses it without the
    add-on. The removal path has no such gate, and a Pro user's cross-project bin
    still has to be swept correctly — so the state is constructed rather than
    built through an API that would not let a free install reach it.
    """
    bins.save_bins({
        "version": bins.BINS_VERSION,
        "bins": [{
            "id": "b1", "name": "A",
            "created_at": None, "updated_at": None,
            "items": [_item("庫一", 7), _item("庫二", 7)],
        }],
    })

    assert bins.remove_media_everywhere("庫一", 7) == 1

    remaining = [(i.project_name, i.media_id) for i in bins.get_bin("b1").items]
    assert remaining == [("庫二", "7")]


def test_media_id_type_does_not_matter():
    """The API takes ints, the JSON stores strings."""
    a = _bin_with("A", [_item("庫一", 7)])
    assert bins.remove_media_everywhere("庫一", "7") == 1
    assert bins.get_bin(a.id).items == []


def test_removal_survives_a_reload():
    a = _bin_with("A", [_item("庫一", 7)])
    bins.remove_media_everywhere("庫一", 7)
    # fresh read straight off disk, not the in-memory objects
    assert bins.get_bin(a.id).items == []


# ── the registry name ────────────────────────────────────────────────────────
def test_current_registry_name_when_unregistered(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "reg.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    assert projects.current_registry_name() is None


def test_current_registry_name_is_the_registry_name_not_the_basename(
    tmp_path, monkeypatch
):
    import config

    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "reg.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    projects.add_project("我的素材庫", str(config.PROJECT_ROOT))

    got = projects.current_registry_name()
    assert got == "我的素材庫"
    assert got != config.PROJECT_ROOT.name, "the whole point is that they differ"


def test_analytics_still_exposes_its_helper():
    """R5-25 route-ownership asserts this attribute lives on the analytics
    module. The implementation moved; the name must not."""
    import routers.analytics as ra

    assert hasattr(ra, "_current_project_registry_name")
    assert ra._current_project_registry_name() == projects.current_registry_name()


# ── wired into the delete ────────────────────────────────────────────────────
def test_delete_clears_the_clip_out_of_bins(monkeypatch, tmp_path):
    import media_delete

    a = _bin_with("A", [_item("庫一", 42), _item("庫一", 43)])
    monkeypatch.setattr(projects, "current_registry_name", lambda: "庫一")

    seen = {}
    real = bins.remove_media_everywhere

    def spy(project, mid):
        seen["args"] = (project, mid)
        return real(project, mid)

    monkeypatch.setattr(bins, "remove_media_everywhere", spy)
    monkeypatch.setattr(media_delete.db, "get_conn", _conn_returning_row(tmp_path))
    monkeypatch.setattr(media_delete.db, "delete_media", lambda mid: [])
    monkeypatch.setattr(media_delete.db, "trash_media", lambda *a, **k: None)

    media_delete.delete_media_full(42, allow_file_delete=False)

    assert seen["args"] == ("庫一", 42)
    assert [i.media_id for i in bins.get_bin(a.id).items] == ["43"]


def test_delete_still_succeeds_when_bins_are_unusable(monkeypatch, tmp_path):
    """The row, files and vectors are already gone by this point. Failing the
    delete because a JSON file could not be rewritten would report failure for
    work that actually happened."""
    import media_delete

    def boom(*_a, **_k):
        raise OSError("bins file is read-only")

    monkeypatch.setattr(projects, "current_registry_name", lambda: "庫一")
    monkeypatch.setattr(bins, "remove_media_everywhere", boom)
    monkeypatch.setattr(media_delete.db, "get_conn", _conn_returning_row(tmp_path))
    monkeypatch.setattr(media_delete.db, "delete_media", lambda mid: [])
    monkeypatch.setattr(media_delete.db, "trash_media", lambda *a, **k: None)

    assert media_delete.delete_media_full(42, allow_file_delete=False)["ok"] is True


def test_delete_skips_the_cleanup_when_the_project_is_unregistered(monkeypatch, tmp_path):
    """An unregistered project cannot own bin items in the first place, so there
    is nothing to look for — and calling with None would match the literal
    string 'None'."""
    import media_delete

    a = _bin_with("A", [_item("None", 42)])
    monkeypatch.setattr(projects, "current_registry_name", lambda: None)
    monkeypatch.setattr(media_delete.db, "get_conn", _conn_returning_row(tmp_path))
    monkeypatch.setattr(media_delete.db, "delete_media", lambda mid: [])
    monkeypatch.setattr(media_delete.db, "trash_media", lambda *a, **k: None)

    media_delete.delete_media_full(42, allow_file_delete=False)

    assert len(bins.get_bin(a.id).items) == 1, "a 'None'-named project must not be swept"


def _conn_returning_row(tmp_path):
    """Minimal db.get_conn stand-in: one media row, no real database."""
    import contextlib

    class _Row(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    class _Conn:
        def execute(self, sql, params=None):
            class _C:
                def fetchone(self_inner):
                    return _Row(id=42, path=str(tmp_path / "gone.mp4"), filename="gone.mp4")
            return _C()

    @contextlib.contextmanager
    def fake():
        yield _Conn()

    return fake
