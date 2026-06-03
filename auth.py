import hashlib
import ipaddress
import json
import os
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

import config
from db import get_conn

try:
    from nanoid import generate as nanoid_generate
except Exception:
    nanoid_generate = None


SCOPES = frozenset((
    "videos_read",
    "videos_write",
    "media_read",
    "collections_read",
    "collections_write",
    "projects_read",
    "projects_write",
    "ingest_write",
    "chat_read",
    "chat_write",
    "admin",
))


_LOOPBACK_HOSTS = frozenset(("127.0.0.1", "::1", "localhost"))


def _trust_loopback() -> bool:
    """Local browser on the same machine is trusted by default (single-machine
    use, and per-machine local UIs in a fleet). Set ARKIV_TRUST_LOOPBACK=false
    when arkiv sits behind a same-host reverse proxy or is otherwise exposed,
    so even loopback requests must present a token."""
    return os.getenv("ARKIV_TRUST_LOOPBACK", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


# A genuine same-machine request carries none of these. A reverse proxy /
# `tailscale serve` / host-net forwarder connects to the backend FROM 127.0.0.1
# but adds a forwarding header — so their presence means the real client is
# remote and loopback trust must NOT apply (else any proxied remote request
# would be handed full admin). This also neutralizes a spoofed
# `X-Forwarded-For: 127.0.0.1` from a remote attacker.
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")


def _looks_proxied(request: Request) -> bool:
    return any(h in request.headers for h in _PROXY_HEADERS)


def hash_token(raw):
    """Legacy unsalted SHA-256. Kept for the dual-read transition (Phase 16.1)
    and as the fallback when no server HMAC key is configured."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hmac_token(raw):
    import hmac as _hmac
    return _hmac.new(
        config.ARKIV_TOKEN_HMAC_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def preferred_hash(raw):
    """(hash, algo) for a NEWLY minted token: HMAC-SHA256 when a server key is
    set (ARKIV_TOKEN_HMAC_KEY), else legacy sha256."""
    if config.ARKIV_TOKEN_HMAC_KEY:
        return _hmac_token(raw), "hmac-sha256"
    return hash_token(raw), "sha256"


def _candidate_hashes(raw):
    """Every (hash, algo) a stored token for `raw` could be under — the dual-read
    set. token_hash is unique so at most one matches; order is irrelevant."""
    cands = [(hash_token(raw), "sha256")]
    if config.ARKIV_TOKEN_HMAC_KEY:
        cands.append((_hmac_token(raw), "hmac-sha256"))
    return cands


def new_token_id():
    if nanoid_generate is not None:
        return nanoid_generate()
    return secrets.token_urlsafe(16)


def new_raw_token():
    return secrets.token_urlsafe(32)


def _check_ip_allowed(client_ip, allowed_ips_json):
    try:
        allowed = json.loads(allowed_ips_json or "[]")
    except Exception:
        return False
    if "*" in allowed:
        return True
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in allowed:
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except Exception:
            continue
    return False


def verify_token(request: Request) -> dict:
    client_host = request.client.host if request.client is not None else ""
    # Loopback trust requires BOTH a loopback peer AND no forwarding header — a
    # reverse proxy / tailscale-serve forwards from 127.0.0.1 but adds one, so
    # this stops a proxied remote request from being handed full admin (and a
    # spoofed X-Forwarded-For from a remote peer, whose own host isn't loopback,
    # never reaches here anyway).
    if _trust_loopback() and client_host in _LOOPBACK_HOSTS and not _looks_proxied(request):
        return {"id": "loopback", "name": "loopback (local)", "scopes": SCOPES}

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        raw = auth_header[len("Bearer "):].strip()
    else:
        # Fallback: token in a `token` query param. Media URLs consumed as
        # <video src>/<img src> cannot attach an Authorization header, so the
        # stream endpoint (and any asset URL) accepts ?token=<raw>. The same
        # hash / expiry / IP-allowlist / scope checks apply; the token's IP
        # allowlist still bounds exposure even though the value rides in a URL.
        raw = (request.query_params.get("token") or "").strip()
    client_ip = request.client.host if (request.client is not None and request.client.host) else ""
    user_agent = request.headers.get("user-agent", "")
    return resolve_raw_token(raw, client_ip, user_agent)


def resolve_raw_token(raw: str, client_ip: str, user_agent: str = "") -> dict:
    """Validate a raw token string → token dict (id/name/scopes). Hash lookup +
    expiry + per-token IP allowlist + scopes, and records last-used. Raises
    HTTPException(401/403). Shared by the HTTP path (verify_token) and the
    WebSocket path (which can't use a Request-typed Depends)."""
    if not raw:
        raise HTTPException(401, "Missing token (expected 'Authorization: Bearer <token>' or ?token=<token>)")
    # Phase 16.1 dual-read: look the token up under every hash it could be
    # stored as (legacy sha256 and, when a server key is set, HMAC-SHA256).
    candidates = _candidate_hashes(raw)
    cand_hashes = [h for h, _ in candidates]
    placeholders = ",".join("?" for _ in cand_hashes)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, expires_at, allowed_ips_json, hash_algo "
            "FROM access_tokens WHERE token_hash IN ({0})".format(placeholders),
            cand_hashes,
        ).fetchone()
        if not row:
            raise HTTPException(401, "Invalid token")

        # Opportunistic migration: a legacy sha256 token that verifies while a
        # server key is configured is rehashed to HMAC on this use, so the fleet
        # drains to the stronger scheme over time. Never block auth on it.
        try:
            row_algo = row["hash_algo"] if "hash_algo" in row.keys() else "sha256"
        except Exception:
            row_algo = "sha256"
        if config.ARKIV_TOKEN_HMAC_KEY and (row_algo or "sha256") == "sha256":
            try:
                conn.execute(
                    "UPDATE access_tokens SET token_hash = ?, hash_algo = ? WHERE id = ?",
                    (_hmac_token(raw), "hmac-sha256", row["id"]),
                )
            except Exception:
                pass

        expires_at = row["expires_at"]
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                raise HTTPException(401, "Token has malformed expires_at")
            if expiry < datetime.now(timezone.utc):
                raise HTTPException(401, "Token expired")

        if not _check_ip_allowed(client_ip, row["allowed_ips_json"]):
            raise HTTPException(403, "Client IP not in token's allowlist")

        scope_rows = conn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ?",
            (row["id"],),
        ).fetchall()
        scopes = frozenset(scope_row["scope"] for scope_row in scope_rows)

        try:
            conn.execute(
                "UPDATE access_tokens SET last_used_at = ?, last_used_ip = ?, last_used_user_agent = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), client_ip, (user_agent or "")[:200], row["id"]),
            )
        except Exception:
            pass

    return {"id": row["id"], "name": row["name"], "scopes": scopes}


def require_scopes(*needed):
    for scope in needed:
        if scope not in SCOPES:
            raise ValueError("Unknown scope {0}".format(scope))

    def _check(tok: dict = Depends(verify_token)) -> dict:
        missing = [scope for scope in needed if scope not in tok["scopes"]]
        if missing:
            raise HTTPException(
                403,
                "Insufficient scope: token has {0}, needs {1} (missing: {2})".format(
                    sorted(tok["scopes"]),
                    sorted(needed),
                    missing,
                ),
            )
        return tok

    return _check
