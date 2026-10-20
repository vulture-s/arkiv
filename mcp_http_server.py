"""arkiv HTTP/SSE MCP server — the LAN variant of `mcp_server.py` (issue #417).

Serves the SAME read-only tools as the stdio server, over HTTP/SSE, so an MCP
client on another machine can query the library without local DB access. The
tools are registered once on `mcp_server.mcp`; this file only exposes that
instance over SSE behind a token gate.

    Machine A (editor's Mac)   MCP client  ──HTTP/SSE──▶  Machine B: arkiv + this
    stdio                      needs a local process and a local DB. Neither
                               travels across a LAN, which is why #417 exists.

Design credit: @pixb, who built this in his fork (pixb/arkiv#…) and reported
#417. Three of his decisions are kept verbatim because they are right:

  * reuse `mcp_server.mcp` rather than re-register the tools — one definition,
    no chance of the two transports drifting apart;
  * a plain ASGI middleware for the token gate, NOT Starlette's
    `BaseHTTPMiddleware` — the latter buffers the response, and an SSE stream
    that never ends would never be delivered;
  * refuse `?token=` entirely. A token in a URL lands in uvicorn's access log,
    any reverse proxy in front of it, and browser history — three places that
    outlive the request and nobody scrubs.

Env:
    ARKIV_MCP_BIND          bind address (default 127.0.0.1)
    ARKIV_MCP_PORT          listen port (default 8502)
    ARKIV_MCP_ALLOWED_HOSTS extra Host: values to accept, comma-separated
                            (see the DNS-rebinding note below)
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import HTTPException

import auth
import mcp_server

_LOGGER = logging.getLogger(__name__)

# 127.0.0.1, not 0.0.0.0. Running this file directly is the "I am on the box"
# case and should not silently publish an MCP endpoint to the network. The
# compose service sets 0.0.0.0 explicitly, where the port mapping is the fence —
# the same split, and the same reasoning, as ARKIV_HOST in docker-compose.yml.
BIND = os.getenv("ARKIV_MCP_BIND", "127.0.0.1")
PORT = int(os.getenv("ARKIV_MCP_PORT", "8502"))


def _configured_hosts() -> list:
    """Extra `Host:` values this deployment answers to.

    ── Why this exists ──────────────────────────────────────────────────────
    The MCP SDK ships DNS-rebinding protection ON, with an allow-list of
    loopback patterns. Measured on the version this repo pins (mcp 1.28.0):

        Host: 127.0.0.1:8502      ✅
        Host: localhost:8502      ✅
        Host: 192.168.1.50:8502   🔴 421 Misdirected Request
        Host: arkiv.local:8502    🔴 421

    So a LAN client is rejected before any arkiv code runs. That is the real
    complaint in #424 — though not for the reasons it gives: the protection is
    already on at 1.28 (not "1.29+"), `allowed_hosts` is not empty, and
    `127.0.0.1` is not rejected. The symptom is right, the diagnosis was not.

    🔴 The fix is NOT to turn the protection off. Rebinding protection is what
    stops a page in the operator's browser from resolving a name to their LAN
    and talking to this port. Instead the deployment states which names it
    actually answers to, and everything else stays refused.

    🔴 These are APPENDED to the loopback defaults, never substituted for them.
    Assigning `allowed_hosts` replaces the list outright — set it to the LAN
    address alone and `127.0.0.1` starts failing, which is a maddening thing to
    debug from the same machine. (`ARKIV_INGEST_ROOTS` in webguard has exactly
    this shape and exactly this trap; there it replaces, and the compose file
    carries a comment saying so.)

    `*` disables host checking. It is spelled out rather than inferred so that
    it can never be reached by accident.
    """
    raw = os.getenv("ARKIV_MCP_ALLOWED_HOSTS", "").strip()
    return [h.strip() for h in raw.split(",") if h.strip()]


def apply_transport_security(mcp_instance) -> None:
    """Widen the Host/Origin allow-list from the environment, additively."""
    extra = _configured_hosts()
    if not extra:
        return

    settings = mcp_instance.settings.transport_security
    if "*" in extra:
        # Explicit opt-out. Everything else in this file (the token gate above
        # all) still applies; this only stops arkiv answering to a name it was
        # not told about.
        settings.enable_dns_rebinding_protection = False
        return

    hosts = list(settings.allowed_hosts or [])
    origins = list(settings.allowed_origins or [])
    for h in extra:
        if h not in hosts:
            hosts.append(h)
        for scheme in ("http://", "https://"):
            o = scheme + h
            if o not in origins:
                origins.append(o)
    settings.allowed_hosts = hosts
    settings.allowed_origins = origins


def _extract_token(scope) -> str:
    """Pull a raw bearer token from the Authorization header, and only there."""
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            raw = value.decode("latin-1", "replace")
            if raw.lower().startswith("bearer "):
                return raw[7:].strip()
            return raw.strip()
    return ""


# The scope every tool on this transport needs. All seven read media rows,
# transcripts, frames or the vector index — the same thing `routers/media.py`
# guards with `require_scopes("videos_read")`.
REQUIRED_SCOPE = "videos_read"


async def token_gate(app, scope, receive, send):
    """ASGI middleware: reject anything without a valid, SUFFICIENTLY SCOPED token.

    Reuses `auth.resolve_raw_token`, so this endpoint inherits the same token
    store, IP allow-list and expiry as the HTTP API — an existing token works
    here unchanged, and revoking one revokes it everywhere.

    🔴 Resolving the token is not the same as authorising it. The first version
    of this file called `resolve_raw_token` and threw the result away, so ANY
    valid token reached every tool: a `chat_read`-only token, an ingest bot's
    token, even a zero-scope one, all read the whole library over the LAN, while
    the REST API refused those same tokens with 403. Every narrow token ever
    minted was a full read key the moment this transport shipped.
    """
    if scope["type"] in ("http", "websocket"):
        client_ip = (scope.get("client") or ("", 0))[0]
        try:
            tok = auth.resolve_raw_token(_extract_token(scope), client_ip)
        except HTTPException as exc:
            await _refuse(send, scope, getattr(exc, "status_code", 401), str(exc.detail))
            return
        except Exception:
            # Never hand an unauthenticated caller the internals of an auth
            # failure — but do log it, or a fleet-wide 401 (missing table, locked
            # DB, unreadable token store) is undebuggable from the outside.
            _LOGGER.exception("MCP token resolution failed")
            await _refuse(send, scope, 401, "unauthorized")
            return
        if REQUIRED_SCOPE not in (tok or {}).get("scopes", ()):
            await _refuse(
                send, scope, 403,
                "Insufficient scope: MCP needs {0}".format(REQUIRED_SCOPE),
            )
            return
    await app(scope, receive, send)


async def _refuse(send, scope, status: int, detail: str) -> None:
    """Refuse in the protocol the caller is actually speaking.

    A websocket scope cannot be answered with `http.response.start`: uvicorn
    raises RuntimeError and turns a clean rejection into a 500 plus a traceback
    per attempt. There are no websocket routes here, so the only thing that
    reaches this branch is someone probing — and letting them fill the log is
    the one thing an unauthenticated neighbour could otherwise do.
    """
    if scope.get("type") == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"error": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})


class ASGIMiddleware:
    """Minimal wrapper. Starlette's `add_middleware` builds a BaseHTTPMiddleware
    stack, which reads the response body to completion — fatal for SSE."""

    def __init__(self, app, middleware):
        self.app = app
        self.middleware = middleware

    async def __call__(self, scope, receive, send):
        await self.middleware(self.app, scope, receive, send)


def build_app():
    """The ASGI app, assembled but not served. Separated from `main` so tests can
    exercise the gate without binding a port."""
    apply_transport_security(mcp_server.mcp)
    return ASGIMiddleware(mcp_server.mcp.sse_app(), token_gate)


def main() -> None:
    mcp_server._prewarm_vectordb()
    import uvicorn

    # 🔴 proxy_headers defaults to True, and with no `forwarded_allow_ips` uvicorn
    # trusts 127.0.0.1. BIND also defaults to 127.0.0.1, so in host-run mode EVERY
    # peer is loopback and therefore trusted: a token whose IP allow-list says
    # 10.0.0.0/8 is refused from localhost, then accepted by adding
    # `X-Forwarded-For: 10.1.1.1` — and the audit trail records the spoofed IP.
    # The same hole is open behind any tunnel that does not set XFF itself
    # (`ssh -L`, socat). arkiv terminates its own connections; nothing here sits
    # behind a reverse proxy, so the header has no legitimate reading.
    uvicorn.run(build_app(), host=BIND, port=PORT, proxy_headers=False)


if __name__ == "__main__":
    main()
