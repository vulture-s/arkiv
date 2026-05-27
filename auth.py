import hashlib
import ipaddress
import json
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

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
    "admin",
))


def hash_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header (expected 'Bearer <token>')")

    raw = auth_header[len("Bearer "):].strip()
    if not raw:
        raise HTTPException(401, "Empty Bearer token")

    token_hash = hash_token(raw)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, expires_at, allowed_ips_json FROM access_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row:
            raise HTTPException(401, "Invalid token")

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

        client_ip = ""
        if request.client is not None and request.client.host:
            client_ip = request.client.host
        if not _check_ip_allowed(client_ip, row["allowed_ips_json"]):
            raise HTTPException(403, "Client IP not in token's allowlist")

        scope_rows = conn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ?",
            (row["id"],),
        ).fetchall()
        scopes = frozenset(scope_row["scope"] for scope_row in scope_rows)

        try:
            user_agent = request.headers.get("user-agent", "")[:200]
            conn.execute(
                "UPDATE access_tokens SET last_used_at = ?, last_used_ip = ?, last_used_user_agent = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), client_ip, user_agent, row["id"]),
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
