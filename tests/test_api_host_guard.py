"""Audit 2026-10-09 (E3): HTTP API web-boundary hardening.

  * HIGH  DNS rebinding — loopback trust looked only at the peer IP, and the
          same-site guard accepted any Origin whose authority equalled Host. A page
          that rebinds its own name to 127.0.0.1 satisfies both, and got all 12
          scopes (bulk-delete + trash purge + mint an admin token).
  * MED   same-site (CSRF) check was hand-attached to 13/52 write routes; multipart
          upload / no-body POSTs elsewhere are CORS simple requests.
  * MED   uvicorn's default proxy_headers=True let a loopback peer forge its source
          IP with X-Forwarded-For and pass a token's IP allowlist.
  * MED   the WebSocket `?token=` was written to the log unredacted (uvicorn.error),
          and /api/logs/tail served it back.
"""
import importlib
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


ROOT = Path(__file__).resolve().parents[1]
EVIL = "rebind.evil.example:8501"


def _local(server_module, host="127.0.0.1:8501"):
    return TestClient(server_module.app, client=("127.0.0.1", 40000),
                      base_url="http://{0}".format(host))


# ── HIGH: DNS rebinding ─────────────────────────────────────────────────────
def test_rebound_host_gets_no_loopback_trust(server_module):
    c = _local(server_module, host=EVIL)
    hdr = {"Origin": "http://" + EVIL}
    assert c.get("/api/media", headers=hdr).status_code == 401
    assert c.post("/api/media/bulk-delete", json={"ids": [1]}, headers=hdr).status_code in (401, 403)
    assert c.post("/api/admin/trash/purge", json={"ttl_days": 0}, headers=hdr).status_code in (401, 403)
    r = c.post("/api/admin/tokens", json={"name": "rebind", "scopes": ["admin"]}, headers=hdr)
    assert r.status_code in (401, 403)
    assert "raw_token" not in r.text


@pytest.mark.parametrize("host", [
    "127.0.0.1:8501", "localhost:8501", "[::1]:8501", "tauri.localhost",
    "100.101.102.103:8501",   # tailnet IP via the L4 forwarder (arkiv-ops)
    "192.168.1.50:8501",      # IP literals can't be rebound
])
def test_legit_loopback_hosts_keep_trust(server_module, host):
    # explicit Host header: starlette's TestClient can't parse an IPv6 base_url
    assert _local(server_module).get("/api/media", headers={"host": host}).status_code == 200


def test_extra_hostnames_are_opt_in(server_module, monkeypatch):
    c = _local(server_module, host="arkiv.local:8501")
    assert c.get("/api/media").status_code == 401
    monkeypatch.setenv("ARKIV_ALLOWED_HOSTS", "arkiv.local")
    assert c.get("/api/media").status_code == 200


def test_rebound_host_cannot_open_ingest_ws(server_module):
    c = _local(server_module, host=EVIL)
    with pytest.raises(WebSocketDisconnect):
        # TestClient's ws handshake ignores base_url for Host → set it explicitly
        with c.websocket_connect("/ws/ingest", headers={"origin": "http://" + EVIL, "host": EVIL}):
            pass
    # the genuine local handshake still works
    with _local(server_module).websocket_connect("/ws/ingest", headers={"host": "127.0.0.1:8501"}) as ws:
        assert ws is not None


# ── MED: same-site guard on every write ─────────────────────────────────────
XSITE = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}


@pytest.mark.parametrize("method,path,kw", [
    ("post", "/api/admin/trash/purge", {}),
    ("post", "/api/projects/sync", {}),
    ("post", "/api/media/999/reingest", {}),
    ("post", "/api/media/999/retry-vision", {}),
    ("post", "/api/admin/trash/restore/999", {}),
    ("post", "/api/ingest/upload", {"files": {"files": ("evil.mp4", b"x", "video/mp4")}}),
])
def test_cross_site_writes_rejected_everywhere(server_module, method, path, kw):
    r = getattr(_local(server_module), method)(path, headers=XSITE, **kw)
    assert r.status_code == 403, (path, r.status_code, r.text[:200])


def test_cross_site_origin_alone_rejected(server_module):
    r = _local(server_module).post("/api/projects/sync", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_and_non_browser_writes_pass_the_guard(server_module):
    c = _local(server_module)
    # SPA served from the same origin
    r = c.post("/api/projects/sync", headers={"Origin": "http://127.0.0.1:8501",
                                               "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200
    # curl / scripts send neither header
    assert c.post("/api/projects/sync").status_code == 200
    # Vite dev server origin stays allowed
    assert c.post("/api/projects/sync", headers={"Origin": "http://localhost:5173"}).status_code == 200


def test_reads_are_not_blocked_by_the_write_guard(server_module):
    assert _local(server_module).get("/api/media", headers=XSITE).status_code == 200


# ── MED: X-Forwarded-For can't launder a token's IP allowlist ───────────────
def _token(scopes, allowed_ips):
    import admin
    return admin.create_token(name="xff", scopes=scopes, allowed_ips=allowed_ips)["raw_token"]


def test_forwarded_header_does_not_satisfy_ip_allowlist(server_module, monkeypatch):
    monkeypatch.delenv("ARKIV_TRUST_PROXY_HEADERS", raising=False)
    raw = _token(["videos_read"], ["10.0.0.0/8"])
    # What uvicorn's default ProxyHeadersMiddleware produces from a loopback peer
    # sending `X-Forwarded-For: 10.1.1.1`: scope client rewritten to the forged IP,
    # header still present.
    c = TestClient(server_module.app, client=("10.1.1.1", 40000))
    auth = {"Authorization": "Bearer " + raw}
    assert c.get("/api/media", headers=dict(auth, **{"X-Forwarded-For": "10.1.1.1"})).status_code == 403
    assert c.get("/api/media", headers=auth).status_code == 200  # a real 10.x peer still works
    monkeypatch.setenv("ARKIV_TRUST_PROXY_HEADERS", "1")  # explicit reverse-proxy opt-in
    assert c.get("/api/media", headers=dict(auth, **{"X-Forwarded-For": "10.1.1.1"})).status_code == 200


def test_wildcard_tokens_unaffected_by_forwarded_header(server_module):
    raw = _token(["videos_read"], None)
    c = TestClient(server_module.app, client=("10.1.1.1", 40000))
    r = c.get("/api/media", headers={"Authorization": "Bearer " + raw, "X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 200


@pytest.mark.parametrize("rel,needle", [
    ("arkiv.command", "--no-proxy-headers"),
    ("Dockerfile", "--no-proxy-headers"),
    ("src-tauri/src/main.rs", "\"--no-proxy-headers\""),
])
def test_every_launcher_disables_proxy_headers(rel, needle):
    text = (ROOT / rel).read_text(encoding="utf-8")
    launch_lines = [l for l in text.splitlines() if "server:app" in l and not l.lstrip().startswith(("#", "//"))]
    if rel.endswith(".rs"):
        assert needle in text
    else:
        assert launch_lines and all(needle in l for l in launch_lines), launch_lines


# ── MED: WebSocket ?token= never reaches the log ────────────────────────────
def test_ws_handshake_log_line_is_redacted(server_module):
    server_module._install_token_redaction_filter()
    records = []

    class _Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    lg = logging.getLogger("uvicorn.error")
    h = _Grab()
    lg.addHandler(h)
    old = lg.level
    lg.setLevel(logging.INFO)
    try:
        lg.info('%s - "WebSocket %s" [accepted]', "127.0.0.1:5555", "/ws/ingest?token=SECRETRAWTOKEN123")
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)
    assert records and "SECRETRAWTOKEN123" not in records[0]
    assert "token=REDACTED" in records[0]


def test_logs_tail_redacts_tokens(server_module, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path)
    (tmp_path / "backend.log").write_text(
        'INFO: 127.0.0.1:5555 - "WebSocket /ws/ingest?token=SECRETRAWTOKEN123" [accepted]\n',
        encoding="utf-8")
    r = _local(server_module).get("/api/logs/tail")
    assert r.status_code == 200
    body = r.text
    assert "SECRETRAWTOKEN123" not in body and "token=REDACTED" in body


# ── #498 review C1: a bogus token must not exempt a request from the guard ───
@pytest.mark.parametrize("how", ["query", "bearer"])
def test_bogus_token_does_not_bypass_write_guard(server_module, how):
    c = _local(server_module, host=EVIL)
    hdr = {"Origin": "http://" + EVIL}
    path = "/api/client-log"  # unauthenticated sink → only the guard stands in the way
    body = {"level": "error", "message": "injected"}
    if how == "query":
        r = c.post(path + "?token=x", json=body, headers=hdr)
    else:
        r = c.post(path, json=body, headers=dict(hdr, Authorization="Bearer junk"))
    assert r.status_code == 403, r.text


def test_valid_token_on_custom_host_still_passes_guard(server_module):
    import admin, auth
    raw = admin.create_token(name="remote", scopes=sorted(auth.SCOPES))["raw_token"]
    c = TestClient(server_module.app, client=("10.0.0.7", 40000), base_url="http://arkiv.example:8501")
    r = c.post("/api/projects/sync", headers={"Origin": "http://arkiv.example:8501",
                                               "Authorization": "Bearer " + raw})
    assert r.status_code == 200, r.text


def test_untrusted_host_401_says_how_to_fix(server_module):
    r = _local(server_module, host="m2max:8501").get("/api/media")
    assert r.status_code == 401 and "ARKIV_ALLOWED_HOSTS" in r.text
