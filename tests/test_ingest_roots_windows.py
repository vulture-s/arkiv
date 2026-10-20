"""Ingest allowlist on Windows + NAS (2026-10-20 Windows first-use run).

The bug: in the Windows desktop app, "＋ INGEST" on a NAS folder
(`\\\\100.64.154.6\\home\\Photos\\iCloud\\PrimarySync`) returned 403, and the only
way out was an env var nobody on Windows can set. Three gaps, each pinned here:

  * the default list said `~/Movies` (macOS) and never `~/Videos` (Windows/Linux);
  * a Mac gets `/Volumes/*` (its NAS mounts) for free; Windows got no counterpart
    for a mapped network drive (Z:) — and the final guard would have dropped
    `Z:\\` anyway, because a drive root is an anchor;
  * a UNC folder has no drive letter at all, so it needs a user-editable setting.

What must NOT change (Codex Round-2 J1): a collaborator on the tailnet still
cannot get a whole local disk walked — `/` and `C:\\` stay refused.

Windows path semantics are exercised with PureWindowsPath so these run on every
host; the real ctypes path is covered by the fake kernel32 below and was run on
real Windows (see PR).
"""
import importlib
import tempfile
from pathlib import Path, PureWindowsPath, PurePosixPath

import pytest


@pytest.fixture
def webguard(monkeypatch):
    monkeypatch.delenv("ARKIV_INGEST_ROOTS", raising=False)
    return importlib.import_module("webguard")


class _FakeKernel32:
    """C: = DRIVE_FIXED(3), D: = DRIVE_CDROM(5), Z: = DRIVE_REMOTE(4)."""
    TYPES = {"C": 3, "D": 5, "Z": 4}

    def GetLogicalDrives(self):
        mask = 0
        for letter in self.TYPES:
            mask |= 1 << (ord(letter) - ord("A"))
        return mask

    def GetDriveTypeW(self, root):
        return self.TYPES.get(root[0], 1)


# ── Videos ───────────────────────────────────────────────────────────────────

def test_videos_is_a_default_ingest_root(webguard):
    roots = webguard._allowed_ingest_roots()
    assert (Path.home() / "Videos").resolve() in roots
    assert (Path.home() / "Movies").resolve() in roots, "Mac name must stay"


def test_videos_is_a_default_export_root(monkeypatch):
    monkeypatch.delenv("ARKIV_EXPORT_ROOTS", raising=False)
    import webguard
    roots = webguard._allowed_export_roots()
    assert (Path.home() / "Videos").resolve() in roots
    assert (Path.home() / "Movies").resolve() in roots


# ── Windows mapped network drives ────────────────────────────────────────────

def test_only_drive_remote_counts_as_a_network_drive(webguard, monkeypatch):
    monkeypatch.setattr(webguard, "_win_kernel32", lambda: _FakeKernel32())
    assert webguard._windows_network_drives() == ["Z:"]


def test_no_network_drives_off_windows_or_on_failure(webguard, monkeypatch):
    monkeypatch.setattr(webguard, "_win_kernel32", lambda: None)
    assert webguard._windows_network_drives() == []

    class _Broken:
        def GetLogicalDrives(self):
            raise OSError("boom")
    monkeypatch.setattr(webguard, "_win_kernel32", lambda: _Broken())
    assert webguard._windows_network_drives() == []


def test_network_drive_root_allowed_local_drive_root_refused(webguard):
    remote = ["Z:"]
    assert webguard._is_bare_local_root(PureWindowsPath("C:\\"), remote) is True
    assert webguard._is_bare_local_root(PureWindowsPath("D:\\"), remote) is True
    assert webguard._is_bare_local_root(PureWindowsPath("Z:\\"), remote) is False
    assert webguard._is_bare_local_root(PureWindowsPath("z:\\"), remote) is False
    # without the sweep saying so, Z:\ is just another drive root
    assert webguard._is_bare_local_root(PureWindowsPath("Z:\\"), []) is True


def test_unc_share_root_allowed_but_admin_and_device_roots_refused(webguard):
    f = webguard._is_bare_local_root
    assert f(PureWindowsPath("\\\\100.64.154.6\\home\\"), []) is False
    assert f(PureWindowsPath("\\\\nas\\home\\Photos"), []) is False   # not a root
    assert f(PureWindowsPath("\\\\localhost\\C$\\"), []) is True       # whole drive
    assert f(PureWindowsPath("\\\\?\\C:\\"), []) is True
    assert f(PurePosixPath("/"), []) is True
    assert f(PurePosixPath("/Volumes/home"), []) is False


def test_network_drive_reaches_allowed_roots_and_c_does_not(webguard, monkeypatch):
    """End-to-end through _allowed_ingest_roots with the fake kernel32. The drive
    roots are fed as PureWindowsPath so the assertion means the same thing on a
    POSIX CI host as on Windows."""
    monkeypatch.setattr(webguard, "_win_kernel32", lambda: _FakeKernel32())
    added = []

    def fake_drive_root(drive):
        added.append(drive)
        return PureWindowsPath(drive + "\\")

    monkeypatch.setattr(webguard, "_drive_root", fake_drive_root)
    roots = webguard._allowed_ingest_roots()
    assert added == ["Z:"], "only the DRIVE_REMOTE letter is offered"
    assert PureWindowsPath("Z:\\") in roots
    assert PureWindowsPath("C:\\") not in roots


# ── ingest.extra_roots (UNC) ─────────────────────────────────────────────────

def test_extra_roots_split_on_semicolon_and_newline(webguard):
    raw = '\\\\nas\\home\\Photos; D:\\Footage\n"E:\\Quoted"\n\n;\\\\nas\\home\\Photos'
    assert webguard._split_extra_roots(raw) == [
        "\\\\nas\\home\\Photos", "D:\\Footage", "E:\\Quoted"]


def test_extra_root_must_be_absolute_and_not_a_local_disk(webguard):
    assert webguard._extra_root_problem("relative/dir")
    assert webguard._extra_root_problem("/")
    assert webguard._extra_root_problem(str(Path(tempfile.gettempdir()) / "x")) == ""


def test_extra_roots_setting_is_added_to_the_allowlist(tmp_db, webguard, tmp_path, monkeypatch):
    settings = importlib.reload(importlib.import_module("settings"))
    nas = tmp_path / "nas-share"
    (nas / "PrimarySync").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    settings.put({"ingest.extra_roots": str(nas)}, scope=settings.current_scope())

    webguard._assert_ingest_path_safe(nas / "PrimarySync")      # allowed
    with pytest.raises(Exception) as ei:
        webguard._assert_ingest_path_safe(outside)
    assert getattr(ei.value, "status_code", None) == 403


def test_extra_roots_rejects_a_disk_root_at_write_time(tmp_db, tmp_path):
    settings = importlib.reload(importlib.import_module("settings"))
    settings.put({"ingest.extra_roots": str(tmp_path)})      # a real folder: fine
    with pytest.raises(settings.SettingError):
        settings.put({"ingest.extra_roots": "/"})
    with pytest.raises(settings.SettingError):
        settings.put({"ingest.extra_roots": "relative/folder"})


def test_a_disk_root_smuggled_into_the_db_is_still_ignored(tmp_db, webguard):
    """The 422 is UX; the boundary is the read side."""
    db = importlib.import_module("db")
    settings = importlib.reload(importlib.import_module("settings"))
    with db.get_conn() as conn:
        conn.execute("INSERT INTO settings(scope, key, value, updated_at) "
                     "VALUES(?, 'ingest.extra_roots', '/', datetime('now'))",
                     (settings.GLOBAL_SCOPE,))
        conn.commit()
    assert all(str(r) != "/" for r in webguard._allowed_ingest_roots())


def test_env_replace_semantics_unchanged_and_setting_still_adds(tmp_db, webguard, tmp_path, monkeypatch):
    """ARKIV_INGEST_ROOTS still REPLACES the defaults (the Docker deploy depends on
    it); the setting is merged on top, never dropping an env root."""
    settings = importlib.reload(importlib.import_module("settings"))
    only = tmp_path / "only"
    only.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    monkeypatch.setenv("ARKIV_INGEST_ROOTS", str(only))
    assert webguard._allowed_ingest_roots() == [only.resolve()]
    assert (Path.home() / "Desktop").resolve() not in webguard._allowed_ingest_roots()

    settings.put({"ingest.extra_roots": str(extra)}, scope=settings.current_scope())
    assert webguard._allowed_ingest_roots() == [only.resolve(), extra.resolve()]


# ── 403 wording ──────────────────────────────────────────────────────────────

def test_ingest_403_tells_the_user_what_to_do(fastapi_client, monkeypatch, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("ARKIV_INGEST_ROOTS", str(sandbox))
    blocked = tmp_path / "PrimarySync"
    blocked.mkdir()
    resp = fastapi_client.post("/api/ingest/scan", json={"path": str(blocked)})
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "不在 arkiv 允許匯入的位置" in detail
    assert "網路磁碟機" in detail and "額外允許的資料夾" in detail
    assert str(blocked) in detail
    assert str(sandbox) not in detail, "must not list the resolved allowed roots"


def test_extra_roots_is_writable_through_the_settings_api(fastapi_client, tmp_path):
    ok = fastapi_client.put("/api/settings", json={
        "scope": "global", "values": {"ingest.extra_roots": str(tmp_path)}})
    assert ok.status_code == 200, ok.text
    bad = fastapi_client.put("/api/settings", json={
        "scope": "global", "values": {"ingest.extra_roots": "/"}})
    assert bad.status_code == 422
