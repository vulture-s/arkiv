"""Cross-library 精選集 (bins): persistence CRUD + per-item reference-integrity
status. The status probe is the RED LINE — a source library going offline / a
clip deleted / a file removed from disk must surface, never silently 'ok'."""
import importlib
import sqlite3
from pathlib import Path


def _fresh_bins(tmp_path, monkeypatch):
    # _default_bins_path() reads ARKIV_BINS_PATH live on every call, so setting the
    # env is enough — do NOT reload the module (reloading rebinds ProjectMeta and
    # contaminates federation's isinstance checks in other tests).
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "bins.json"))
    return importlib.import_module("bins")


def _make_project(tmp_path, name, with_db=True, with_chroma=True, media_path="clips/a.mp4", make_file=True):
    """A registrable project: <root>/.arkiv/{project.db,chroma_db}. Media row 1
    points at media_path (relative → resolves under root); optionally create the
    real file so file_missing can be exercised by omitting it."""
    root = tmp_path / name
    arkiv = root / ".arkiv"
    arkiv.mkdir(parents=True, exist_ok=True)
    if with_chroma:
        (arkiv / "chroma_db").mkdir(exist_ok=True)
    if with_db:
        conn = sqlite3.connect(str(arkiv / "project.db"))
        conn.execute(
            "CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT, filename TEXT, "
            "duration_s REAL, rating TEXT, lang TEXT, ext TEXT, transcript TEXT)"
        )
        conn.execute(
            "INSERT INTO media (id, path, filename, duration_s, rating, lang, ext, transcript) "
            "VALUES (1, ?, '黑沙灘.mov', 7.9, 'good', 'zh', '.mov', 'iceland')",
            (media_path,),
        )
        conn.commit()
        conn.close()
    if make_file and media_path:
        target = root / media_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"stub-media")
    return root


# --- CRUD ---

def test_bin_crud_round_trip(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    b = bins.create_bin("單車紀錄片選片")
    assert b.name == "單車紀錄片選片" and b.id
    assert [x.name for x in bins.list_bins()] == ["單車紀錄片選片"]

    renamed = bins.rename_bin(b.id, "改名了")
    assert renamed.name == "改名了"
    assert bins.get_bin(b.id).name == "改名了"

    bins.delete_bin(b.id)
    assert bins.list_bins() == []


def test_add_items_dedupes_and_preserves_order(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    b = bins.create_bin("selection")
    bins.add_items(b.id, [
        {"project_name": "projA", "media_id": "1", "filename": "a.mov"},
        {"project_name": "projB", "media_id": "5", "filename": "b.mov"},
        {"project_name": "projA", "media_id": "1", "filename": "dup-ignored"},  # dup key
    ])
    got = bins.get_bin(b.id)
    assert [(i.project_name, i.media_id) for i in got.items] == [("projA", "1"), ("projB", "5")]
    # dedup keeps the FIRST filename
    assert got.items[0].filename == "a.mov"


def test_remove_item(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    b = bins.create_bin("s")
    bins.add_items(b.id, [{"project_name": "p", "media_id": "1"}, {"project_name": "p", "media_id": "2"}])
    bins.remove_item(b.id, "p", "1")
    assert [i.media_id for i in bins.get_bin(b.id).items] == ["2"]


def test_get_missing_bin_raises(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    try:
        bins.get_bin("nope")
        assert False, "should raise"
    except bins.BinsError as exc:
        assert "not found" in str(exc)


def test_save_bins_uses_unique_tmp_and_cleans_up(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    bins.save_bins({"version": 1, "bins": []})
    assert not (tmp_path / "bins.json.tmp").exists()
    assert (tmp_path / "bins.json").exists()


def test_corrupt_bins_json_raises_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "bins.json"))
    (tmp_path / "bins.json").write_text("{ not valid", encoding="utf-8")
    bins = importlib.import_module("bins")
    try:
        bins.list_bins()
        assert False, "should raise"
    except bins.BinsError as exc:
        assert "corrupt" in str(exc)


# --- reference-integrity status (the RED LINE) ---

def _register(tmp_path, monkeypatch, name, root):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    projects = importlib.import_module("projects")  # env read live; no reload
    projects.add_project(name, str(root))
    return projects


def test_status_ok_for_reachable_clip(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    root = _make_project(tmp_path, "libA", make_file=True)
    _register(tmp_path, monkeypatch, "libA", root)
    assert bins.bin_item_status("libA", "1") == bins.STATUS_OK


def test_status_project_unregistered(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    assert bins.bin_item_status("ghost", "1") == bins.STATUS_PROJECT_UNREGISTERED


def test_status_row_missing(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    root = _make_project(tmp_path, "libB", make_file=True)
    _register(tmp_path, monkeypatch, "libB", root)
    assert bins.bin_item_status("libB", "999") == bins.STATUS_ROW_MISSING


def test_status_file_missing(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    # row exists but the referenced file is never created on disk
    root = _make_project(tmp_path, "libC", make_file=False)
    _register(tmp_path, monkeypatch, "libC", root)
    assert bins.bin_item_status("libC", "1") == bins.STATUS_FILE_MISSING


def test_status_db_missing_passes_through_health(tmp_path, monkeypatch):
    bins = _fresh_bins(tmp_path, monkeypatch)
    # project dir + chroma but NO project.db → health returns db_missing
    root = _make_project(tmp_path, "libD", with_db=False, with_chroma=True, make_file=False)
    _register(tmp_path, monkeypatch, "libD", root)
    assert bins.bin_item_status("libD", "1") == "db_missing"


# --- API endpoints ---

def test_bins_api_crud_and_no_path_leak(fastapi_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "api-bins.json"))
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "api-registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)

    # create
    r = fastapi_client.post("/api/bins", json={"name": "跨庫選片"})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]

    # list
    r = fastapi_client.get("/api/bins")
    assert r.status_code == 200
    assert [b["name"] for b in r.json()["bins"]] == ["跨庫選片"]

    # add items — filename carries an absolute path; the detail view MUST basename it
    r = fastapi_client.post(
        "/api/bins/{0}/items".format(bid),
        json={"items": [{"project_name": "ghostlib", "media_id": "7",
                          "filename": "/Users/secret/vault/黑沙灘.mov"}]},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["item_count"] == 1
    item = detail["items"][0]
    # reachability surfaced (project not registered here)
    assert item["status"] == "project_unregistered"
    # Phase 16.2: no absolute path leaks — filename basenamed, no absolute_path field
    assert item["filename"] == "黑沙灘.mov"
    assert "absolute_path" not in item
    assert "/Users/secret" not in r.text

    # remove item
    r = fastapi_client.request(
        "DELETE", "/api/bins/{0}/items".format(bid),
        json={"project_name": "ghostlib", "media_id": "7"},
    )
    assert r.status_code == 200
    assert r.json()["item_count"] == 0

    # delete bin
    assert fastapi_client.delete("/api/bins/{0}".format(bid)).status_code == 200
    assert fastapi_client.get("/api/bins").json()["total"] == 0


def test_bins_api_get_missing_404(fastapi_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "api-bins2.json"))
    assert fastapi_client.get("/api/bins/nope").status_code == 404
