"""R5-25 (round-5 #51): export routes peeled to routers/export.py.

Thirteenth router peeled. Pins the split: the leaf module owns all six export
routes + _attachment_headers + the three request models, server.py no longer
defines them, routes mounted + auth-guarded (401-not-404). All six are co-located
so export_batch / export_to_file call export_media directly (an internal call, not
an HTTP round-trip). Format/output behaviour is covered by test_export_batch.py +
test_server.py; the serialisers live in export_builders (test_r5_25_export_builders).
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_export_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "export.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_all_six_export_routes_and_helpers():
    import routers.export as re_
    pairs = {
        (r.path, m)
        for r in re_.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/export/metadata-csv", "GET"),
        ("/api/export/metadata-csv-to", "POST"),
        ("/api/media/{media_id}/export/{fmt}", "GET"),
        ("/api/media/{media_id}/export-to", "POST"),
        ("/api/export/batch", "POST"),
        ("/api/export/timeline/{fmt}", "GET"),
    }
    for name in ("export_media", "export_batch", "export_timeline", "export_to_file",
                 "export_metadata_csv", "export_metadata_csv_to", "_attachment_headers",
                 "MetadataCsvExportRequest", "ExportToRequest", "BatchExportRequest"):
        assert hasattr(re_, name)


def test_attachment_headers_is_cjk_safe():
    # RFC 6266: a CJK stem must NOT raise (latin-1 header encoding) — ASCII
    # fallback + percent-encoded filename*. This is why it moved with the routes.
    import routers.export as re_
    h = re_._attachment_headers("測試影片", "srt")
    cd = h["Content-Disposition"]
    cd.encode("latin-1")  # must not raise
    assert 'filename="' in cd and "filename*=UTF-8''" in cd
    assert "%E6" in cd  # percent-encoded UTF-8 bytes of the CJK name


def test_batch_and_to_file_call_export_media_directly():
    # co-location invariant: export_batch / export_to_file reference the module-level
    # export_media (not an HTTP call), so all three must live in the same module.
    src = (_ROOT / "routers" / "export.py").read_text(encoding="utf-8")
    assert "resp = export_media(" in src


def test_server_no_longer_defines_export_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def export_media" not in src
    assert "def _attachment_headers" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/(export|media/\{media_id\}/export)", src)
    assert "include_router(export_router)" in src


def test_export_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/export/metadata-csv").status_code == 401
        assert c.post("/api/export/metadata-csv-to", json={}).status_code == 401
        assert c.get("/api/media/1/export/srt").status_code == 401
        assert c.post("/api/media/1/export-to", json={"fmt": "srt", "dest": "/x"}).status_code == 401
        assert c.post("/api/export/batch", json={"ids": [1], "fmt": "srt"}).status_code == 401
        assert c.get("/api/export/timeline/edl?ids=1").status_code == 401
        assert c.get("/api/export/nope-zz").status_code == 404
