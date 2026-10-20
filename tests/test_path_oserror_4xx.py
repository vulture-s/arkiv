"""A user-typed path the OS refuses to let us LOOK at must not become a 500.

2026-10-20 Windows first-run: adding `\\\\100.64.154.6\\home\\...` (an SMB share
Windows had not logged into) raised `OSError: [WinError 1326]` out of
`Path.exists()` — which only swallows the not-found family — and the user saw
"arkiv API 500 on /api/projects". These tests make `exists` / `is_dir` raise for
one marked path, the way Windows does, and pin that every API taking such a path
answers 4xx with a next step (or, for the health probe, marks that one project).
"""
import errno
import pathlib

import pytest

_MARK = "unreachable-share"
_REAL_EXISTS = pathlib.Path.exists


def _win1326():
    # What Windows raises for an SMB share with no session: errno EINVAL-ish,
    # winerror 1326, strerror in the local language. On POSIX the winerror
    # argument is ignored, so build the same shape by hand.
    exc = OSError(errno.EACCES, "使用者名稱或密碼不正確。")
    exc.winerror = 1326
    return exc


@pytest.fixture(params=["winerror1326", "permission"])
def refuse_marked_path(request, monkeypatch):
    real_exists = pathlib.Path.exists
    real_is_dir = pathlib.Path.is_dir

    def _boom():
        if request.param == "permission":
            return PermissionError(errno.EACCES, "Permission denied")
        return _win1326()

    def exists(self, *a, **kw):
        if _MARK in str(self):
            raise _boom()
        return real_exists(self, *a, **kw)

    def is_dir(self, *a, **kw):
        if _MARK in str(self):
            raise _boom()
        return real_is_dir(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "exists", exists)
    monkeypatch.setattr(pathlib.Path, "is_dir", is_dir)
    return request.param


def _assert_actionable(resp):
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "無法存取這個路徑" in detail
    assert "登入" in detail


def test_path_access_message_keeps_the_system_reason():
    import health

    msg = health.path_access_message(_win1326(), "加入")
    assert "使用者名稱或密碼不正確" in msg
    assert "登入" in msg and msg.endswith("再回來加入。")
    # An OSError built without strerror still yields a readable reason.
    assert "boom" in health.path_access_message(OSError("boom"))


def test_add_project_unreachable_path_is_400_not_500(
    fastapi_client, tmp_path, monkeypatch, refuse_marked_path
):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "projects.json"))
    resp = fastapi_client.post(
        "/api/projects", json={"name": "icloud", "path": str(tmp_path / _MARK)}
    )
    _assert_actionable(resp)


def test_add_project_missing_path_still_plain_not_found(fastapi_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "projects.json"))
    resp = fastapi_client.post(
        "/api/projects", json={"name": "gone", "path": str(tmp_path / "nope")}
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"]


def test_health_and_sync_mark_one_project_instead_of_500(
    fastapi_client, tmp_path, monkeypatch, refuse_marked_path
):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "projects.json"))
    import projects

    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / _MARK
    # Register both while the path is still reachable, then let it go dark —
    # the share logged out after the project was added.
    bad.mkdir()
    with monkeypatch.context() as m:
        m.setattr(pathlib.Path, "exists", _REAL_EXISTS)
        projects.add_project("bad", str(bad))
    projects.add_project("good", str(good))

    resp = fastapi_client.get("/api/projects/health")
    assert resp.status_code == 200, resp.text
    by_name = {p["name"]: p["status"] for p in resp.json()["projects"]}
    assert by_name["bad"] == "inaccessible"
    assert by_name["good"] != "inaccessible"

    resp = fastapi_client.post("/api/projects/sync")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 2


def test_ingest_scan_unreachable_path_is_400(fastapi_client, tmp_path, refuse_marked_path):
    resp = fastapi_client.post("/api/ingest/scan", json={"path": str(tmp_path / _MARK)})
    _assert_actionable(resp)


def test_offload_preview_unreachable_src_is_400(fastapi_client, tmp_path, refuse_marked_path):
    resp = fastapi_client.post("/api/offload/preview", json={"src": str(tmp_path / _MARK)})
    _assert_actionable(resp)


def test_export_csv_unreachable_dest_is_400(fastapi_client, tmp_path, refuse_marked_path):
    resp = fastapi_client.post(
        "/api/export/metadata-csv-to", json={"dest": str(tmp_path / _MARK)}
    )
    _assert_actionable(resp)
