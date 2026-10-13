"""LAN variant of the DaVinci Resolve plugin (arkiv_resolve_lan.py).

Pins two behaviours the localhost build does NOT have:
- default API host is localhost (override with ARKIV_HOST / ARKIV_API for a remote server)
- every request carries an `Authorization: Bearer <token>` header, because the
  server only trusts loopback as admin without a token.

The plugin imports cleanly without DaVinciResolveScript (that import is nested
inside get_resolve()), so we can load it by path and assert the wired Request.
"""
import importlib.util
import pathlib
import urllib.request

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parent.parent / "resolve_plugin" / "arkiv_resolve_lan.py"


def _load(monkeypatch):
    for k in ("ARKIV_API", "ARKIV_HOST", "ARKIV_PORT"):
        monkeypatch.delenv(k, raising=False)
    # The LAN plugin requires ARKIV_TOKEN at import time (it raises otherwise);
    # the tests only check that a non-empty Bearer token is attached, so a dummy
    # value is fine here.
    monkeypatch.setenv("ARKIV_TOKEN", "test-token-not-a-secret")
    spec = importlib.util.spec_from_file_location("arkiv_resolve_lan_test", str(PLUGIN))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch):
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured["req"] = req
        return _FakeResp(b'{"items":[]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_lan_default_host_is_localhost(monkeypatch):
    from urllib.parse import urlparse

    mod = _load(monkeypatch)
    assert urlparse(mod.ARKIV_API).hostname == "localhost"


def test_search_media_sends_token_header_to_lan(monkeypatch):
    from urllib.parse import urlparse

    mod = _load(monkeypatch)
    captured = _capture(monkeypatch)
    mod.search_media("snow")
    req = captured["req"]
    assert urlparse(req.full_url).hostname == "localhost"
    assert req.get_header("Authorization", "").startswith("Bearer ")
    assert req.get_header("Authorization", "") != "Bearer "


def test_list_media_sends_token_header_to_lan(monkeypatch):
    from urllib.parse import urlparse

    mod = _load(monkeypatch)
    captured = _capture(monkeypatch)
    mod.list_media(limit=10)
    req = captured["req"]
    assert urlparse(req.full_url).hostname == "localhost"
    assert req.get_header("Authorization", "").startswith("Bearer ")


def test_download_media_requests_stream_with_token(monkeypatch, tmp_path):
    """LAN import must pull bytes from /api/stream/{id} (server-side path
    resolution) rather than handing Resolve the unreachable media-in/... path."""
    import urllib.request as ureq

    mod = _load(monkeypatch)
    monkeypatch.setattr(mod, "_DOWNLOAD_DIR", str(tmp_path))

    captured = {}

    class _FakeResp:
        # Chunked like a real HTTPResponse: the download now streams with
        # read(n) instead of one whole-file read() (audit N1).
        def __init__(self, data):
            self._d = data

        def read(self, n=-1):
            d, self._d = self._d, b""
            return d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, *a, **k):
        captured["req"] = req
        return _FakeResp(b"\x00\x01video-bytes")

    monkeypatch.setattr(ureq, "urlopen", fake_urlopen)

    dest = mod._download_media(42, "clip.mp4")
    assert dest is not None
    assert captured["req"].full_url.endswith("/api/stream/42")
    assert captured["req"].get_header("Authorization", "").startswith("Bearer ")
    with open(dest, "rb") as f:
        assert f.read() == b"\x00\x01video-bytes"


def test_import_to_resolve_groups_lan_files_under_arkiv_bin(monkeypatch):
    """With bin_name set (LAN download), clips land in an 'Arkiv' bin, never a
    meaningless 'media-in' bin derived from the server's relative path."""
    mod = _load(monkeypatch)
    monkeypatch.setattr(mod, "download_metadata_csv", lambda *a, **k: None)

    class _Bin:
        def __init__(self, name):
            self.name = name

        def GetName(self):
            return self.name

        def GetSubFolderList(self):
            return []

    class _Clip:
        def __init__(self, p):
            self._p = p

        def GetName(self):
            import os
            return os.path.basename(self._p)

    class _MediaPool:
        def __init__(self):
            self.bins = []

        def GetRootFolder(self):
            return _Bin("Master")

        def GetSubFolderList(self):
            return []

        def SetCurrentFolder(self, b):
            pass

        def AddSubFolder(self, parent, name):
            b = _Bin(name)
            self.bins.append(b)
            return b

        def ImportMedia(self, paths):
            return [_Clip(p) for p in paths]

    class _Project:
        def GetMediaPool(self):
            return _MediaPool()

    class _PM:
        def GetCurrentProject(self):
            return _Project()

    class _Resolve:
        def GetProjectManager(self):
            return _PM()

    mp = _MediaPool()
    monkeypatch.setattr(_Project, "GetMediaPool", lambda self: mp)

    ok = mod.import_to_resolve(
        _Resolve(), ["/tmp/arkiv_import/42_clip.mp4"], bin_name="Arkiv"
    )
    assert ok is True
    assert any(b.GetName() == "Arkiv" for b in mp.bins)
    assert not any(b.GetName() == "media-in" for b in mp.bins)

