"""`arkiv_token.py rotate` and the expiry-default hardening (audit P3).

Two behaviour changes are pinned here:

  1. `create --expires-in` now defaults to 90 days, not never. A non-expiring
     token has to be asked for explicitly (`--expires-in 0`). The old default
     silently minted `ARKIV_TOKEN_OPENCLAW`-style forever-tokens.
  2. `rotate <id>` mints a replacement with the SAME scopes and allowlist, and
     grace-expires the old one instead of deleting it — so an in-flight caller
     (a running OpenClaw container, another workstation) is not cut off mid-request.
"""
import json
from datetime import datetime, timedelta, timezone

import arkiv_token
import db


def _make_token(name, scopes, allowed_ips, expires_at=None):
    """Insert a token row directly, returning its id. Mirrors cmd_create's writes."""
    from auth import preferred_hash, new_raw_token, new_token_id

    tid = new_token_id()
    token_hash, hash_algo = preferred_hash(new_raw_token())
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, hash_algo, expires_at, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, name, None, token_hash, hash_algo, expires_at, json.dumps(allowed_ips)),
        )
        for s in scopes:
            conn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (tid, s),
            )
    return tid


def _row(tid):
    with db.get_conn() as conn:
        r = conn.execute(
            "SELECT id, name, expires_at, allowed_ips_json FROM access_tokens WHERE id = ?",
            (tid,),
        ).fetchone()
        scopes = [
            x["scope"]
            for x in conn.execute(
                "SELECT scope FROM access_token_scopes WHERE token_id = ? ORDER BY scope",
                (tid,),
            ).fetchall()
        ]
    return r, scopes


class TestExpiryDefault:
    def test_create_defaults_to_90_days(self):
        p = arkiv_token.build_parser()
        a = p.parse_args(["create", "--name", "x", "--scopes", "admin"])
        assert a.expires_in == 90, "create must default to an expiry, not never"

    def test_zero_and_none_both_mean_never(self):
        assert arkiv_token._expires_at(0) is None
        assert arkiv_token._expires_at(None) is None

    def test_positive_days_produces_a_future_iso_timestamp(self):
        got = arkiv_token._expires_at(90)
        assert got is not None
        # Parses as a real UTC datetime roughly 90 days out.
        when = datetime.fromisoformat(got)
        delta_days = (when - datetime.now(timezone.utc)).days
        assert 88 <= delta_days <= 90


class TestRotate:
    def test_rotate_preserves_scopes_and_allowlist(self, tmp_db):
        old = _make_token("openclaw", ["admin", "ingest_write"], ["127.0.0.1/32", "100.64.0.0/10"])
        args = _Args(token_id=old, expires_in=90, grace=7)
        arkiv_token.cmd_rotate(args)

        # New token exists, carries the same capabilities.
        with db.get_conn() as conn:
            new_ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM access_tokens WHERE description LIKE 'rotated from %'"
                ).fetchall()
            ]
        assert len(new_ids) == 1, "rotate should mint exactly one replacement"
        new_row, new_scopes = _row(new_ids[0])
        assert new_scopes == ["admin", "ingest_write"]
        assert json.loads(new_row["allowed_ips_json"]) == ["127.0.0.1/32", "100.64.0.0/10"]
        assert new_row["name"] == "openclaw"

    def test_rotate_grace_expires_the_old_token_not_deletes_it(self, tmp_db):
        old = _make_token("m2max", ["admin"], ["*"], expires_at=None)  # was never-expiring
        arkiv_token.cmd_rotate(_Args(token_id=old, expires_in=90, grace=7))

        old_row, old_scopes = _row(old)
        assert old_row is not None, "old token must survive (grace window), not be deleted"
        assert old_row["expires_at"] is not None, "old token must now have a grace expiry"
        when = datetime.fromisoformat(old_row["expires_at"])
        delta_days = (when - datetime.now(timezone.utc)).days
        assert 6 <= delta_days <= 7, "grace expiry should be ~7 days out"

    def test_rotate_only_shortens_a_sooner_expiry(self, tmp_db):
        # Old token already expires in 2 days — the 7-day grace must not EXTEND it.
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        old = _make_token("shortlived", ["videos_read"], ["*"], expires_at=soon)
        arkiv_token.cmd_rotate(_Args(token_id=old, expires_in=90, grace=7))
        old_row, _ = _row(old)
        assert old_row["expires_at"] == soon, "grace must never lengthen an existing expiry"

    def test_rotate_unknown_token_fails(self, tmp_db):
        import pytest

        with pytest.raises(SystemExit):
            arkiv_token.cmd_rotate(_Args(token_id="tok_does_not_exist", expires_in=90, grace=7))


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)
