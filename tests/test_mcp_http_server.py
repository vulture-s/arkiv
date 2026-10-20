"""The LAN MCP transport (#417) and the Host allow-list it needs (#424).

stdio MCP needs a local process and a local DB; neither travels across a LAN, so
an editor on another machine cannot query the library at all. That is #417.

#424 is what stands in the way once the transport exists. The MCP SDK ships DNS
rebinding protection ON with a loopback allow-list, so a LAN client is refused
with 421 before any arkiv code runs. Measured on the pinned SDK (mcp 1.28.0):

    Host: 127.0.0.1:8502      ✅
    Host: localhost:8502      ✅
    Host: 192.168.1.50:8502   🔴 421
    Host: arkiv.local:8502    🔴 421

The issue's own diagnosis is wrong on two counts — the protection is already on
at 1.28 rather than "1.29+", and `allowed_hosts` is not empty, so `127.0.0.1`
was never rejected. The symptom is real; the explanation was not. These tests
pin the symptom, so a future SDK bump that changes the behaviour is visible.
"""
import asyncio
import importlib

import pytest

mcp_http_server = pytest.importorskip("mcp_http_server")
pytest.importorskip("mcp.server.transport_security")

from mcp.server.transport_security import (  # noqa: E402
    TransportSecurityMiddleware,
    TransportSecuritySettings,
)


class FakeMCP:
    """Just the one attribute `apply_transport_security` touches."""

    class _S:
        pass

    def __init__(self, hosts=None, origins=None, protection=True):
        self.settings = self._S()
        self.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=protection,
            allowed_hosts=list(hosts if hosts is not None else ["127.0.0.1:*", "localhost:*"]),
            allowed_origins=list(origins if origins is not None else ["http://127.0.0.1:*"]),
        )


def _host_allowed(settings, host):
    mw = TransportSecurityMiddleware(settings)

    class R:
        headers = {"host": host}

    return asyncio.run(mw.validate_request(R(), is_post=False)) is None


# ── reading the env ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("", []),
    ("   ", []),
    ("a:1", ["a:1"]),
    ("a:1,b:2", ["a:1", "b:2"]),
    (" a:1 , , b:2 ", ["a:1", "b:2"]),
])
def test_configured_hosts_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("ARKIV_MCP_ALLOWED_HOSTS", raw)
    assert mcp_http_server._configured_hosts() == expected


def test_unset_env_changes_nothing(monkeypatch):
    monkeypatch.delenv("ARKIV_MCP_ALLOWED_HOSTS", raising=False)
    m = FakeMCP()
    before = list(m.settings.transport_security.allowed_hosts)
    mcp_http_server.apply_transport_security(m)
    assert m.settings.transport_security.allowed_hosts == before


# ── the additive property ────────────────────────────────────────────────────
def test_lan_host_is_added_without_losing_loopback(monkeypatch):
    """🔴 Assigning `allowed_hosts` REPLACES the list. Setting it to the LAN
    address alone makes 127.0.0.1 start failing — a maddening thing to debug
    from the same machine. (webguard's ARKIV_INGEST_ROOTS has this exact shape
    and does replace; that trap is documented in docker-compose.yml.)"""
    monkeypatch.setenv("ARKIV_MCP_ALLOWED_HOSTS", "192.168.1.50:8502")
    m = FakeMCP()
    mcp_http_server.apply_transport_security(m)
    s = m.settings.transport_security

    assert _host_allowed(s, "192.168.1.50:8502"), "the LAN host must now pass"
    assert _host_allowed(s, "127.0.0.1:8502"), "loopback must survive"
    assert not _host_allowed(s, "evil.example:8502"), "everything else stays refused"


def test_protection_stays_on(monkeypatch):
    """The fix is a wider allow-list, NOT switching the protection off. Rebinding
    protection is what stops a page in the operator's browser resolving a name to
    their LAN and reaching this port."""
    monkeypatch.setenv("ARKIV_MCP_ALLOWED_HOSTS", "192.168.1.50:8502")
    m = FakeMCP()
    mcp_http_server.apply_transport_security(m)
    assert m.settings.transport_security.enable_dns_rebinding_protection is True


def test_origins_get_the_host_too(monkeypatch):
    monkeypatch.setenv("ARKIV_MCP_ALLOWED_HOSTS", "arkiv.local:8502")
    m = FakeMCP()
    mcp_http_server.apply_transport_security(m)
    origins = m.settings.transport_security.allowed_origins
    assert "http://arkiv.local:8502" in origins
    assert "https://arkiv.local:8502" in origins


def test_repeated_application_does_not_duplicate(monkeypatch):
    monkeypatch.setenv("ARKIV_MCP_ALLOWED_HOSTS", "arkiv.local:8502")
    m = FakeMCP()
    mcp_http_server.apply_transport_security(m)
    mcp_http_server.apply_transport_security(m)
    hosts = m.settings.transport_security.allowed_hosts
    assert hosts.count("arkiv.local:8502") == 1


# ── the explicit opt-out ─────────────────────────────────────────────────────
def test_star_disables_and_must_be_spelled_out(monkeypatch):
    monkeypatch.setenv("ARKIV_MCP_ALLOWED_HOSTS", "*")
    m = FakeMCP()
    mcp_http_server.apply_transport_security(m)
    assert m.settings.transport_security.enable_dns_rebinding_protection is False


def test_a_normal_host_never_disables_protection(monkeypatch):
    """Only the literal `*` opts out; nothing else may reach that branch."""
    for value in ("192.168.1.50:8502", "star", "**", "a,*b"):
        monkeypatch.setenv("ARKIV_MCP_ALLOWED_HOSTS", value)
        m = FakeMCP()
        mcp_http_server.apply_transport_security(m)
        assert m.settings.transport_security.enable_dns_rebinding_protection is True, value


# ── the token gate ───────────────────────────────────────────────────────────
def _scope(headers=None, query=b"", client=("10.0.0.9", 5555)):
    return {
        "type": "http",
        "headers": headers or [],
        "query_string": query,
        "client": client,
        "path": "/sse",
    }


def _run_gate(monkeypatch, scope, resolver):
    monkeypatch.setattr(mcp_http_server.auth, "resolve_raw_token", resolver)
    sent = []
    reached = {"app": False}

    async def app(s, r, sd):
        reached["app"] = True

    async def send(msg):
        sent.append(msg)

    asyncio.run(mcp_http_server.token_gate(app, scope, None, send))
    return reached["app"], sent


def test_no_token_is_refused(monkeypatch):
    def resolver(raw, ip):
        raise Exception("no token")

    reached, sent = _run_gate(monkeypatch, _scope(), resolver)
    assert reached is False
    assert sent[0]["status"] == 401


# ── scope, not just identity ─────────────────────────────────────────────────
@pytest.mark.parametrize("scopes", [[], ["chat_read"], ["ingest_write"], ["admin"]])
def test_a_valid_token_without_videos_read_is_refused(monkeypatch, scopes):
    """🔴 The audit finding. The first version resolved the token and discarded
    the result, so ANY valid token — a chat widget's, an ingest bot's, a
    zero-scope one — read the entire library over the LAN, while REST answered
    403 to those same tokens. Note `admin` is in this list on purpose: arkiv's
    scopes are a flat set, not a hierarchy, and REST does not treat admin as a
    superset either."""
    reached, sent = _run_gate(
        monkeypatch,
        _scope([(b"authorization", b"Bearer tok")]),
        lambda raw, ip: {"scopes": scopes},
    )
    assert reached is False
    assert sent[0]["status"] == 403


def test_videos_read_gets_through(monkeypatch):
    reached, _ = _run_gate(
        monkeypatch,
        _scope([(b"authorization", b"Bearer tok")]),
        lambda raw, ip: {"scopes": ["chat_read", "videos_read"]},
    )
    assert reached is True


def test_a_resolver_returning_none_is_refused(monkeypatch):
    """Defensive: a token store that answers None must not be read as
    "unrestricted"."""
    reached, sent = _run_gate(
        monkeypatch, _scope([(b"authorization", b"Bearer tok")]), lambda raw, ip: None)
    assert reached is False
    assert sent[0]["status"] == 403


def test_the_required_scope_matches_what_rest_asks_for():
    """If REST's media routes ever move off videos_read, this transport must
    move with them rather than quietly becoming the looser door."""
    rest = (ROOT_MEDIA_ROUTER := importlib.import_module("routers.media"))
    src = open(rest.__file__, encoding="utf-8").read()
    assert 'require_scopes("{0}")'.format(mcp_http_server.REQUIRED_SCOPE) in src


# ── refusing in the right protocol ───────────────────────────────────────────
def test_a_websocket_is_closed_not_answered_with_http(monkeypatch):
    """🔴 `http.response.start` on a websocket scope makes uvicorn raise
    RuntimeError — a clean rejection becomes a 500 and a traceback per attempt.
    There are no ws routes here, so the only caller is someone probing, and
    filling the log is the one thing an unauthenticated neighbour could do."""
    scope = dict(_scope(), type="websocket")
    reached, sent = _run_gate(monkeypatch, scope, lambda raw, ip: (_ for _ in ()).throw(Exception()))
    assert reached is False
    assert sent[0]["type"] == "websocket.close"
    assert not any(m["type"].startswith("http.") for m in sent)


def test_auth_failure_is_logged(monkeypatch, caplog):
    """The comment used to claim "the server log already has the traceback".
    Nothing logged. A fleet-wide 401 (missing table, locked DB) was then
    undebuggable from outside."""
    import logging

    with caplog.at_level(logging.ERROR):
        _run_gate(monkeypatch, _scope(),
                  lambda raw, ip: (_ for _ in ()).throw(RuntimeError("token store locked")))
    assert any("token" in r.message.lower() for r in caplog.records)


def test_valid_token_reaches_the_app(monkeypatch):
    seen = {}

    def resolver(raw, ip):
        seen["raw"], seen["ip"] = raw, ip
        return {"scopes": ["videos_read"]}

    reached, _ = _run_gate(
        monkeypatch, _scope([(b"authorization", b"Bearer tok-123")]), resolver)
    assert reached is True
    assert seen["raw"] == "tok-123"
    assert seen["ip"] == "10.0.0.9", "the peer IP must reach the allow-list check"


def test_query_param_token_is_not_accepted(monkeypatch):
    """🔴 A token in a URL lands in uvicorn's access log, any proxy in front of
    it, and browser history. `?token=` must not be a way in."""
    def resolver(raw, ip):
        if not raw:
            raise Exception("empty")
        return {"scopes": []}

    reached, sent = _run_gate(
        monkeypatch, _scope(query=b"token=tok-123"), resolver)
    assert reached is False
    assert sent[0]["status"] == 401


def test_bearer_prefix_is_optional_but_stripped(monkeypatch):
    got = {}

    def resolver(raw, ip):
        got["raw"] = raw
        return {}

    _run_gate(monkeypatch, _scope([(b"authorization", b"bearer  spaced  ")]), resolver)
    assert got["raw"] == "spaced"


def test_auth_failure_detail_is_not_leaked(monkeypatch):
    """An unauthenticated caller learns that it failed, not how."""
    import json

    def resolver(raw, ip):
        raise ValueError("token store at /Users/someone/.arkiv/media.db is locked")

    _reached, sent = _run_gate(monkeypatch, _scope(), resolver)
    body = json.loads(sent[1]["body"])
    assert body == {"error": "unauthorized"}


def test_lifespan_scope_is_not_gated(monkeypatch):
    """ASGI lifespan has no client and no headers; gating it would stop the app
    from starting."""
    def resolver(raw, ip):
        raise AssertionError("the gate must not run for lifespan")

    reached, _ = _run_gate(monkeypatch, {"type": "lifespan"}, resolver)
    assert reached is True


# ── deployment defaults ──────────────────────────────────────────────────────
def test_bind_defaults_to_loopback():
    """Running this file directly is the "I am on the box" case. Publishing an
    MCP endpoint to the network has to be a decision, not a default; compose
    sets 0.0.0.0 explicitly, where the port mapping is the fence."""
    mod = importlib.reload(mcp_http_server)
    assert mod.BIND == "127.0.0.1"
    assert mod.PORT == 8502


def test_the_gate_is_plain_asgi_not_basehttpmiddleware():
    """BaseHTTPMiddleware reads the response to completion. An SSE stream never
    completes, so the whole transport would hang."""
    import starlette.middleware.base as base
    assert not issubclass(mcp_http_server.ASGIMiddleware, base.BaseHTTPMiddleware)
