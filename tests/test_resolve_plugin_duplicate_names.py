"""Resolve plugins vs. duplicate filenames (audit 2026-10-09 N1).

Camera cards restart numbering, so a library routinely holds several
`C0001.MP4`. Both plugins keyed their result rows by filename, so ticking the
first `C0001.MP4` imported whichever one was written to the dict last; rating
colours/tags were matched by `clip_name in path` (substring), and timeline
markers by `startswith(stem)` (C0001 → C00012). The LAN build also renamed every
download to `{id}_{filename}`, so the metadata CSV (matched on File Name) never
applied, and read whole clips into memory.

The Fusion UI is faked just enough to drive create_ui's real handlers.
"""
import importlib.util
import io
import os
import pathlib
import types
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "resolve_plugin"


def _load(monkeypatch, name):
    for k in ("ARKIV_API", "ARKIV_HOST", "ARKIV_PORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ARKIV_TOKEN", "test-token-not-a-secret")
    spec = importlib.util.spec_from_file_location(name + "_dup_test", str(ROOT / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── fake Fusion UIManager ─────────────────────────────────────────────────────
class _Idx(dict):
    def __missing__(self, k):
        return ""


class _Item:
    def __init__(self):
        self.Text = _Idx()


class _Tree:
    def __init__(self):
        self.rows = []
        self.ColumnWidth = {}
        self.ColumnCount = 0
        self.selected = {}

    def NewItem(self):
        return _Item()

    def SetHeaderItem(self, hdr):
        self.hdr = hdr

    def Clear(self):
        self.rows = []

    def AddTopLevelItem(self, row):
        self.rows.append(row)

    def SelectedItems(self):
        return self.selected


class _Widget:
    def __init__(self):
        self.Text = ""


class _On:
    def __getattr__(self, name):
        ns = types.SimpleNamespace()
        object.__setattr__(self, name, ns)
        return ns


class _Win:
    def __init__(self):
        self.tree = _Tree()
        self.widgets = {}
        self.On = _On()

    def Find(self, wid):
        if wid == "ResultTree":
            return self.tree
        return self.widgets.setdefault(wid, _Widget())

    def Show(self):
        pass

    def Hide(self):
        pass


class _UI:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _Disp:
    def __init__(self):
        self.win = _Win()

    def AddWindow(self, *a, **k):
        return self.win

    def RunLoop(self):
        pass

    def ExitLoop(self):
        pass


# ── fake Resolve media pool ───────────────────────────────────────────────────
class _MPI:
    def __init__(self, path):
        self.path = path
        self.color = None
        self.meta = {}

    def GetName(self):
        return os.path.basename(self.path)

    def GetClipProperty(self, key):
        return self.path if key == "File Path" else ""

    def SetClipColor(self, c):
        self.color = c

    def SetMetadata(self, k, v):
        self.meta[k] = v


class _Folder:
    def GetSubFolderList(self):
        return []

    def GetName(self):
        return "Master"


class _Pool:
    def __init__(self):
        self.imported = []

    def GetRootFolder(self):
        return _Folder()

    def SetCurrentFolder(self, f):
        return True

    def AddSubFolder(self, root, name):
        return _Folder()

    def ImportMedia(self, paths):
        items = [_MPI(p) for p in paths]
        self.imported.extend(items)
        return items


class _Resolve:
    def __init__(self, ui):
        self.pool = _Pool()
        self._ui = ui

    def Fusion(self):
        return types.SimpleNamespace(UIManager=self._ui)

    def GetProjectManager(self):
        pool = self.pool
        project = types.SimpleNamespace(GetMediaPool=lambda: pool)
        return types.SimpleNamespace(GetCurrentProject=lambda: project)


ITEMS = [
    {"id": 11, "filename": "C0001.MP4", "path": "/vol/cardA/C0001.MP4", "rating": "good",
     "tags": [{"name": "day1"}], "duration_s": 3, "lang": "zh"},
    {"id": 22, "filename": "C0001.MP4", "path": "/vol/cardB/C0001.MP4", "rating": "ng",
     "tags": [{"name": "day2"}], "duration_s": 4, "lang": "zh"},
]


def _drive(monkeypatch, mod):
    disp = _Disp()
    ui = _UI()
    monkeypatch.setattr(mod, "bmd", types.SimpleNamespace(UIDispatcher=lambda _ui: disp), raising=False)
    monkeypatch.setattr(mod, "list_media", lambda *a, **k: [dict(i) for i in ITEMS])
    monkeypatch.setattr(mod, "download_metadata_csv", lambda *a, **k: None)
    resolve = _Resolve(ui)
    mod.create_ui(resolve)
    return disp.win, resolve


def test_local_plugin_imports_the_row_that_was_ticked(monkeypatch):
    mod = _load(monkeypatch, "arkiv_resolve")
    win, resolve = _drive(monkeypatch, mod)
    rows = win.tree.rows
    assert [r.Text[0] for r in rows] == ["C0001.MP4", "C0001.MP4"]
    win.tree.selected = {"r0": rows[0]}  # tick ONLY the first (card A)
    win.On.ImportBtn.Clicked(None)
    assert [m.path for m in resolve.pool.imported] == ["/vol/cardA/C0001.MP4"]
    assert resolve.pool.imported[0].color == "Green"  # card A's own rating, not card B's NG


def test_local_plugin_two_same_named_rows_import_both_with_own_ratings(monkeypatch):
    mod = _load(monkeypatch, "arkiv_resolve")
    win, resolve = _drive(monkeypatch, mod)
    win.tree.selected = {"r0": win.tree.rows[0], "r1": win.tree.rows[1]}
    win.On.ImportBtn.Clicked(None)
    got = {m.path: (m.color, m.meta.get("Keywords")) for m in resolve.pool.imported}
    assert got == {"/vol/cardA/C0001.MP4": ("Green", "day1"),
                   "/vol/cardB/C0001.MP4": ("Orange", "day2")}


class _Stream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_lan_download_keeps_original_name_per_id_and_streams(monkeypatch, tmp_path):
    mod = _load(monkeypatch, "arkiv_resolve_lan")
    monkeypatch.setattr(mod, "_DOWNLOAD_DIR", str(tmp_path))
    payload = {11: b"card A bytes", 22: b"card B bytes"}

    def fake_urlopen(req, *a, **k):
        mid = int(req.full_url.rsplit("/", 1)[1])
        return _Stream(payload[mid])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    a = mod._download_media(11, "C0001.MP4")
    b = mod._download_media(22, "C0001.MP4")
    assert a != b
    # the metadata CSV matches on File Name → the local file must keep it
    assert os.path.basename(a) == os.path.basename(b) == "C0001.MP4"
    assert open(a, "rb").read() == b"card A bytes"
    assert open(b, "rb").read() == b"card B bytes"
    assert not [p for p in os.listdir(os.path.dirname(a)) if p.endswith(".partial")]


def test_lan_failed_download_leaves_no_file(monkeypatch, tmp_path):
    mod = _load(monkeypatch, "arkiv_resolve_lan")
    monkeypatch.setattr(mod, "_DOWNLOAD_DIR", str(tmp_path))

    class _Broken(_Stream):
        def read(self, *a):
            raise OSError("connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Broken(b""))
    assert mod._download_media(33, "C0009.MP4") is None
    leftovers = [f for _, _, fs in os.walk(str(tmp_path)) for f in fs]
    assert leftovers == []


def test_marker_fallback_does_not_match_a_longer_clip_name(monkeypatch):
    mod = _load(monkeypatch, "arkiv_resolve")
    added = []

    class _TI:
        def __init__(self, name):
            self.name = name

        def GetName(self):
            return self.name

        def AddMarker(self, *a):
            added.append((self.name, a[0]))
            return True

    items = [_TI("C00012.MP4")]
    timeline = types.SimpleNamespace(
        GetSetting=lambda k: "25",
        GetTrackCount=lambda kind: 1,
        GetItemListInTrack=lambda kind, t: items,
    )
    project = types.SimpleNamespace(GetCurrentTimeline=lambda: timeline)
    resolve = types.SimpleNamespace(
        GetProjectManager=lambda: types.SimpleNamespace(GetCurrentProject=lambda: project))
    monkeypatch.setattr(mod, "get_media_detail",
                        lambda mid: {"frames": [{"timestamp_s": 1.0, "description": "x"}]})
    n = mod.add_markers_to_timeline(resolve, [{"id": 1, "filename": "C0001.MOV"}])
    assert n == 0 and added == []
