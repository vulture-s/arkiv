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
import os

from fastapi import HTTPException

import auth
import mcp_server

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


async def token_gate(app, scope, receive, send):
    """ASGI middleware: reject anything without a valid arkiv token.

    Reuses `auth.resolve_raw_token`, so this endpoint inherits the same token
    store, IP allow-list and expiry as the HTTP API — an existing token works
    here unchanged, and revoking one revokes it everywhere.
    """
    if scope["type"] in ("http", "websocket"):
        client_ip = (scope.get("client") or ("", 0))[0]
        try:
            auth.resolve_raw_token(_extract_token(scope), client_ip)
        except HTTPException as exc:
            await _refuse(send, getattr(exc, "status_code", 401), str(exc.detail))
            return
        except Exception:
            # Never leak the internals of an auth failure to an unauthenticated
            # caller; the server log already has the traceback.
            await _refuse(send, 401, "unauthorized")
            return
    await app(scope, receive, send)


async def _refuse(send, status: int, detail: str) -> None:
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
    uvicorn.run(build_app(), host=BIND, port=PORT)


if __name__ == "__main__":
    main()
