"""R5-25 (round-5 #51): web-security boundary guards extracted to webguard.py.

Second leaf module of the APIRouter split. Holds the write-boundary + same-site
guards that ~every mutating route calls: the export-destination allowlist, the
offload-destination denylist, the ingest-path allowlist, and the same-site
(CSRF) guard — plus the `_ALLOWED_ORIGINS` allowlist those consult.

`_ALLOWED_ORIGINS` is the subtle part: it is used at server import time by the
CORSMiddleware AND by webguard._assert_same_site. It must be ONE object owned by
the leaf module — if server.py kept its own copy while webguard imported another,
the middleware and the same-site guard could silently diverge, and worse, the
router split would re-introduce the router→server→router import cycle. These
tests pin single-ownership + identity re-export + the leak/CSRF behaviour so the
move is provably semantics-preserving.

Behavioural coverage of the guards through the live app (403 bodies, offload
denylist, same-site rejection) already lives in test_hardening_round4.py; here we
pin the module boundary and a few direct-call invariants.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_NAMES = (
    "_ALLOWED_ORIGINS",
    "_ALLOWED_EXPORT_EXTS",
    "_allowed_export_roots",
    "_assert_export_dest_safe",
    "_OFFLOAD_DENY_SUBSTR",
    "_OFFLOAD_DENY_ROOTS",
    "_assert_offload_dst_safe",
    "_allowed_ingest_roots",
    "_assert_ingest_path_safe",
    "_assert_same_site",
)


# ── module boundary ──────────────────────────────────────────────────────────
def test_webguard_is_a_leaf_module():
    src = (_ROOT / "webguard.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_server_reexports_webguard_by_identity():
    import server
    import webguard
    for name in _NAMES:
        assert getattr(server, name) is getattr(webguard, name), (
            "server.{0} must BE webguard.{0} (a re-export)".format(name)
        )


def test_allowed_origins_single_ownership():
    # The whole point of moving _ALLOWED_ORIGINS to the leaf: exactly one object,
    # shared by the CORS middleware and the same-site guard. A regression that
    # re-defines it in server.py (or hardcodes a different list into the
    # middleware) would break this and let the two boundaries drift apart.
    import server
    import webguard
    assert server._ALLOWED_ORIGINS is webguard._ALLOWED_ORIGINS


def test_cors_middleware_uses_the_webguard_allowlist():
    import server
    import webguard
    cors = None
    for mw in server.app.user_middleware:
        kw = getattr(mw, "kwargs", {}) or {}
        if "allow_origins" in kw:
            cors = kw["allow_origins"]
            break
    assert cors is not None, "CORSMiddleware allow_origins not found"
    assert cors is webguard._ALLOWED_ORIGINS, (
        "CORS middleware must be configured with the shared webguard allowlist, "
        "not a private copy"
    )


def test_server_defines_no_private_allowed_origins():
    # Guard against a future edit re-introducing a module-level `_ALLOWED_ORIGINS = [`
    # literal in server.py, which would shadow the re-export and re-create the
    # dual-source drift this refactor removed.
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert not re.search(r"^_ALLOWED_ORIGINS\s*=\s*\[", src, re.M), (
        "server.py must import _ALLOWED_ORIGINS from webguard, not define its own"
    )


# ── guard behaviour preserved by the move (direct calls) ─────────────────────
def test_export_dest_rejects_bad_extension_and_outside_root(tmp_path, monkeypatch):
    import webguard
    monkeypatch.setenv("ARKIV_EXPORT_ROOTS", str(tmp_path))
    # bad extension → 403 before the root check
    with pytest.raises(HTTPException) as e1:
        webguard._assert_export_dest_safe(tmp_path / "evil.plist")
    assert e1.value.status_code == 403
    # good extension but outside the approved root → 403
    with pytest.raises(HTTPException) as e2:
        webguard._assert_export_dest_safe(pathlib.Path("/etc/x.csv"))
    assert e2.value.status_code == 403
    # good extension inside the approved root → allowed
    webguard._assert_export_dest_safe(tmp_path / "ok.csv")


@pytest.mark.parametrize("bad", ["/etc", "~/.ssh", "/System/Library"])
def test_offload_dst_denies_system_dirs(bad):
    import webguard
    with pytest.raises(HTTPException) as e:
        webguard._assert_offload_dst_safe(bad)
    assert e.value.status_code == 403


def test_offload_dst_allows_backup_target(tmp_path):
    import webguard
    webguard._assert_offload_dst_safe(str(tmp_path))  # must not raise


def test_ingest_roots_default_never_include_filesystem_root(monkeypatch):
    import webguard
    # J1: the auto-discovered default roots (incl. the /Volumes/* sweep that could
    # otherwise surface a mount resolving to '/') must never contain a bare
    # drive/fs root — else the bound is a no-op. (The operator-supplied
    # ARKIV_INGEST_ROOTS override is trusted and intentionally not filtered.)
    monkeypatch.delenv("ARKIV_INGEST_ROOTS", raising=False)
    roots = webguard._allowed_ingest_roots()
    assert roots, "default roots should be non-empty"
    assert all(str(r) != r.anchor for r in roots)


def test_same_site_rejects_cross_site_and_passes_non_browser():
    import webguard

    class _Req:
        def __init__(self, headers):
            self.headers = headers

    # cross-site Sec-Fetch-Site → reject
    with pytest.raises(HTTPException):
        webguard._assert_same_site(_Req({"sec-fetch-site": "cross-site"}))
    # foreign Origin → reject
    with pytest.raises(HTTPException):
        webguard._assert_same_site(_Req({"origin": "https://evil.example", "host": "localhost:8501"}))
    # no Origin / Sec-Fetch-Site (curl) → pass
    webguard._assert_same_site(_Req({}))
    # known allowed origin → pass
    webguard._assert_same_site(_Req({"origin": webguard._ALLOWED_ORIGINS[0]}))
