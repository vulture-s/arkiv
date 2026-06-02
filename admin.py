"""Admin functions for access token management."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from auth import SCOPES, hash_token, new_raw_token, new_token_id
from db import get_conn


def _row_to_token_dict(row, scope_rows):
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "scopes": sorted(scope_row["scope"] for scope_row in scope_rows),
        "expires_at": row["expires_at"],
        "allowed_ips": json.loads(row["allowed_ips_json"]),
        "last_used_at": row["last_used_at"],
        "last_used_ip": row["last_used_ip"],
        "last_used_user_agent": row["last_used_user_agent"],
        "created_at": row["created_at"],
    }


def create_token(
    name: str,
    scopes: List[str],
    description: Optional[str] = None,
    expires_in_days: Optional[int] = None,
    allowed_ips: Optional[List[str]] = None,
) -> dict:
    if not scopes:
        raise ValueError("scopes list cannot be empty")
    unknown = [scope for scope in scopes if scope not in SCOPES]
    if unknown:
        raise ValueError("Unknown scopes: {0}. Valid: {1}".format(unknown, sorted(SCOPES)))

    raw = new_raw_token()
    token_id = new_token_id()
    expires_at = None
    if expires_in_days:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    allowed_ips_json = json.dumps(allowed_ips or ["*"])

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, expires_at, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token_id, name, description, hash_token(raw), expires_at, allowed_ips_json),
        )
        for scope in scopes:
            conn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (token_id, scope),
            )

    return {
        "id": token_id,
        "name": name,
        "scopes": sorted(scopes),
        "expires_at": expires_at,
        "allowed_ips": allowed_ips or ["*"],
        "raw_token": raw,
    }


def list_tokens() -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, description, expires_at, allowed_ips_json, "
            "last_used_at, last_used_ip, last_used_user_agent, created_at "
            "FROM access_tokens ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for row in rows:
            scope_rows = conn.execute(
                "SELECT scope FROM access_token_scopes WHERE token_id = ?",
                (row["id"],),
            ).fetchall()
            result.append(_row_to_token_dict(row, scope_rows))
    return result


def get_token(token_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, description, expires_at, allowed_ips_json, "
            "last_used_at, last_used_ip, last_used_user_agent, created_at "
            "FROM access_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        if not row:
            return None
        scope_rows = conn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ?",
            (token_id,),
        ).fetchall()
        return _row_to_token_dict(row, scope_rows)


def revoke_token(token_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM access_tokens WHERE id = ?", (token_id,))
        return cur.rowcount > 0


def bootstrap_admin_token_if_empty() -> Optional[str]:
    from config import ARKIV_ADMIN_BOOTSTRAP_TOKEN

    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM access_tokens").fetchone()["c"]
        if count > 0:
            return None

    if not ARKIV_ADMIN_BOOTSTRAP_TOKEN:
        return None

    # This seeds a FULL-ADMIN token allowed from ANY IP (["*"]). A weak,
    # operator-chosen value (e.g. "admin") would therefore be a remotely
    # brute-forceable admin credential. Refuse to seed anything below ~128 bits
    # of typical entropy — fail loud at startup instead of silently accepting it.
    if len(ARKIV_ADMIN_BOOTSTRAP_TOKEN) < 24:
        raise ValueError(
            "ARKIV_ADMIN_BOOTSTRAP_TOKEN is too weak (min 24 chars). "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    token_id = new_token_id()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                token_id,
                "bootstrap",
                "Admin token from ARKIV_ADMIN_BOOTSTRAP_TOKEN env (delete after creating per-machine tokens)",
                hash_token(ARKIV_ADMIN_BOOTSTRAP_TOKEN),
                '["*"]',
            ),
        )
        conn.execute(
            "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
            (token_id, "admin"),
        )

    return ARKIV_ADMIN_BOOTSTRAP_TOKEN
