"""P4 (2026-10-17): the SPA's Content-Security-Policy.

The Tauri window loads `WebviewUrl::External("http://127.0.0.1:<port>")`, so
Tauri's `app.security.csp` (only attached by Tauri's asset protocol to embedded
assets) never reaches the page. The policy that actually governs the desktop app
and the browser UI is the header the backend sends with index.html — pinned here.
Render-verified against Playwright WebKit (0 violations across all SPA routes);
loosening or tightening a directive should come with a re-run of that check.
"""
import json
from pathlib import Path

import webguard

SPA_INDEX = '<!doctype html><html><body><div id="app"></div></body></html>'

EXPECTED = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ipc: http://ipc.localhost ws://127.0.0.1:8611 wss://127.0.0.1:8611; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _directives(policy):
    return {d.split()[0]: d.split()[1:] for d in (x.strip() for x in policy.split(";")) if d}


def test_spa_csp_exact_policy():
    assert webguard.spa_csp("127.0.0.1:8611") == EXPECTED


def test_spa_csp_never_allows_eval_or_inline_script():
    d = _directives(webguard.spa_csp("127.0.0.1:8501"))
    assert d["script-src"] == ["'self'"]
    assert "*" not in " ".join(sum(d.values(), []))


def test_spa_csp_rejects_injected_host():
    # A Host carrying ';' (or spaces) must not be able to append a directive.
    for bad in ("evil; script-src *", "a b", "x" * 300, "h\r\nX: y"):
        pol = webguard.spa_csp(bad)
        assert "script-src *" not in pol
        assert _directives(pol)["connect-src"] == ["'self'", "ipc:", "http://ipc.localhost"]


def test_root_route_sends_csp_header(fastapi_client, server_module, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(SPA_INDEX, encoding="utf-8")
    monkeypatch.setattr(server_module, "FRONTEND_DIST", dist)
    r = fastapi_client.get("/", headers={"host": "127.0.0.1:8611"})
    assert r.status_code == 200
    assert 'id="app"' in r.text
    assert r.headers["content-security-policy"] == EXPECTED


def test_tauri_conf_csp_no_longer_wildcard():
    # Dormant today (External URL window), but must not regress to `default-src *`
    # in case the shell ever switches to WebviewUrl::App.
    conf = json.loads((Path(__file__).resolve().parent.parent / "src-tauri" / "tauri.conf.json").read_text())
    d = _directives(conf["app"]["security"]["csp"])
    assert d["default-src"] == ["'self'"]
    assert d["script-src"] == ["'self'"]
    assert "'unsafe-eval'" not in conf["app"]["security"]["csp"]
