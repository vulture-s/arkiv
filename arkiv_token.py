#!/usr/bin/env python3
"""CLI for managing arkiv API access tokens."""

import argparse
import ipaddress
import json
import sys
from datetime import datetime, timedelta, timezone

from auth import SCOPES, preferred_hash, new_raw_token, new_token_id
from db import get_conn, init_db


def _fail(message):
    print("error: {0}".format(message), file=sys.stderr)
    raise SystemExit(2)


def _parse_scope_list(value):
    scopes = [item.strip() for item in value.split(",") if item.strip()]
    if not scopes:
        _fail("scopes cannot be empty")
    unknown = [scope for scope in scopes if scope not in SCOPES]
    if unknown:
        print("error: unknown scope(s): {0}".format(", ".join(sorted(unknown))), file=sys.stderr)
        print("valid scopes: {0}".format(", ".join(sorted(SCOPES))), file=sys.stderr)
        raise SystemExit(2)
    return scopes


def _parse_ip_allowlist(value):
    if value is None:
        return ["*"]
    value = value.strip()
    if not value or value == "*":
        return ["*"]
    entries = [item.strip() for item in value.split(",") if item.strip()]
    if not entries:
        return ["*"]
    if "*" in entries:
        return ["*"]
    for entry in entries:
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            _fail("invalid CIDR in --ip-allowlist: {0}".format(entry))
    return entries


def _expires_at(days):
    # None (not passed) and 0 both mean "never". Everything else is N days out.
    # The CLI default is 90, so "never" is now an explicit `--expires-in 0` rather
    # than the silent default it used to be.
    if not days:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _token_row(token_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, description, expires_at, allowed_ips_json, "
            "last_used_at, last_used_ip, last_used_user_agent, created_at "
            "FROM access_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        if not row:
            return None, []
        scope_rows = conn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ? ORDER BY scope",
            (token_id,),
        ).fetchall()
        return row, [scope_row["scope"] for scope_row in scope_rows]


def cmd_create(args):
    init_db()
    scopes = _parse_scope_list(args.scopes)
    allowed_ips = _parse_ip_allowlist(args.ip_allowlist)
    raw_token = new_raw_token()
    token_id = new_token_id()
    expires_at = _expires_at(args.expires_in)

    token_hash, hash_algo = preferred_hash(raw_token)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, hash_algo, expires_at, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                token_id,
                args.name,
                args.description,
                token_hash,
                hash_algo,
                expires_at,
                json.dumps(allowed_ips),
            ),
        )
        for scope in scopes:
            conn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (token_id, scope),
            )

    print("=" * 72)
    print("Token created. Raw token (save this now; it cannot be recovered later):")
    print("")
    print("  {0}".format(raw_token))
    print("")
    print("Token ID:     {0}".format(token_id))
    print("Name:         {0}".format(args.name))
    print("Scopes:       {0}".format(", ".join(sorted(scopes))))
    print("IP allowlist: {0}".format(", ".join(allowed_ips)))
    print("Expires:      {0}".format(expires_at or "never"))
    print("=" * 72)


def cmd_list(args):
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, expires_at, last_used_at, last_used_ip, created_at "
            "FROM access_tokens ORDER BY created_at DESC"
        ).fetchall()
        if not rows:
            print("No tokens. Run: arkiv_token.py create --name <name> --scopes <s1,s2,...>")
            return
        print("{0:<24} {1:<20} {2:<22} {3:<22} {4}".format("ID", "NAME", "LAST USED", "EXPIRES", "SCOPES"))
        now = datetime.now(timezone.utc)
        soon = (now + timedelta(days=30)).isoformat()
        now_iso = now.isoformat()
        warn_soon = []
        warn_never = []
        for row in rows:
            scope_rows = conn.execute(
                "SELECT scope FROM access_token_scopes WHERE token_id = ? ORDER BY scope",
                (row["id"],),
            ).fetchall()
            scopes = ",".join(scope_row["scope"] for scope_row in scope_rows)
            exp = row["expires_at"]
            # ISO-8601 UTC strings sort lexicographically the same as chronologically,
            # so a plain string compare is a valid date compare here.
            flag = ""
            if exp is None:
                flag = " ⚠ never-expires"
                warn_never.append(row["id"])
            elif exp < now_iso:
                flag = " ⚠ EXPIRED"
            elif exp < soon:
                flag = " ⚠ expires <30d"
                warn_soon.append(row["id"])
            print(
                "{0:<24} {1:<20} {2:<22} {3:<22} {4}{5}".format(
                    row["id"][:22],
                    row["name"][:18],
                    row["last_used_at"] or "never",
                    row["expires_at"] or "never",
                    scopes,
                    flag,
                )
            )
        if warn_soon:
            print("")
            print("Expiring within 30 days: {0}".format(", ".join(warn_soon)))
            print("  Rotate without downtime:  arkiv_token.py rotate <id>")
        if warn_never:
            print("")
            print("Non-expiring tokens: {0}".format(", ".join(warn_never)))
            print("  A leaked non-expiring token is valid forever. Give it an expiry")
            print("  by rotating:  arkiv_token.py rotate <id> --expires-in 90")


def cmd_show(args):
    init_db()
    row, scopes = _token_row(args.token_id)
    if row is None:
        _fail("token not found: {0}".format(args.token_id))

    print("Token ID:      {0}".format(row["id"]))
    print("Name:          {0}".format(row["name"]))
    print("Description:   {0}".format(row["description"] or "(none)"))
    print("Scopes:        {0}".format(", ".join(scopes)))
    print("IP allowlist:  {0}".format(", ".join(json.loads(row["allowed_ips_json"]))))
    print("Expires:       {0}".format(row["expires_at"] or "never"))
    print("Created:       {0}".format(row["created_at"]))
    print("Last used:     {0}".format(row["last_used_at"] or "never"))
    if row["last_used_at"]:
        print("Last IP:       {0}".format(row["last_used_ip"] or "(unknown)"))
        print("Last UA:       {0}".format(row["last_used_user_agent"] or "(none)"))


def cmd_revoke(args):
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM access_tokens WHERE id = ?",
            (args.token_id,),
        ).fetchone()
        if row is None:
            _fail("token not found: {0}".format(args.token_id))
        conn.execute(
            "DELETE FROM access_token_scopes WHERE token_id = ?",
            (args.token_id,),
        )
        conn.execute(
            "DELETE FROM access_tokens WHERE id = ?",
            (args.token_id,),
        )

    print("Revoked token: {0}".format(args.token_id))


def _grace_expiry(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def cmd_rotate(args):
    """Mint a replacement for an existing token and retire the old one on a grace period.

    Rotation without downtime: the new token is live immediately, the old one keeps
    working for --grace days so a caller that still holds it (a running OpenClaw
    container, another workstation) does not break the instant you rotate. Same
    scopes and same IP allowlist as the original — rotating should not silently widen
    or narrow what the token can do.
    """
    init_db()
    old_row, old_scopes = _token_row(args.token_id)
    if old_row is None:
        _fail("token not found: {0}".format(args.token_id))
    if not old_scopes:
        _fail("token {0} has no scopes; refusing to rotate a broken row".format(args.token_id))

    allowed_ips = json.loads(old_row["allowed_ips_json"])
    raw_token = new_raw_token()
    new_id = new_token_id()
    expires_at = _expires_at(args.expires_in)
    token_hash, hash_algo = preferred_hash(raw_token)
    grace_at = _grace_expiry(args.grace)

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, hash_algo, expires_at, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                old_row["name"],
                "rotated from {0}".format(args.token_id),
                token_hash,
                hash_algo,
                expires_at,
                json.dumps(allowed_ips),
            ),
        )
        for scope in old_scopes:
            conn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (new_id, scope),
            )
        # Retire the old token on a grace window rather than deleting it now, so an
        # in-flight caller is not cut off mid-request. Only ever SHORTENS its life —
        # if it already expires sooner than the grace window, leave that alone.
        cur_exp = old_row["expires_at"]
        if cur_exp is None or cur_exp > grace_at:
            conn.execute(
                "UPDATE access_tokens SET expires_at = ? WHERE id = ?",
                (grace_at, args.token_id),
            )

    print("=" * 72)
    print("Rotated. New raw token (save this now; it cannot be recovered later):")
    print("")
    print("  {0}".format(raw_token))
    print("")
    print("New token ID:  {0}".format(new_id))
    print("Name:          {0}".format(old_row["name"]))
    print("Scopes:        {0}".format(", ".join(sorted(old_scopes))))
    print("IP allowlist:  {0}".format(", ".join(allowed_ips)))
    print("New expires:   {0}".format(expires_at or "never"))
    print("")
    print("Old token {0} now expires {1} (grace window).".format(args.token_id, grace_at))
    print("Swap the new token into every caller before then, then it lapses on its own.")
    print("=" * 72)


def build_parser():
    parser = argparse.ArgumentParser(description="arkiv API access token CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a new access token")
    create.add_argument("--name", required=True, help="Human-readable token name")
    create.add_argument("--scopes", required=True, help="Comma-separated scope list")
    create.add_argument(
        "--ip-allowlist",
        default=None,
        help="Comma-separated CIDR allowlist. Use '*' or omit for any IP.",
    )
    create.add_argument(
        "--expires-in",
        type=int,
        default=90,
        help="Expire after N days (default 90). Pass 0 for a non-expiring token — "
             "a deliberate choice, since a leaked non-expiring token is valid forever.",
    )
    create.add_argument(
        "--description",
        default=None,
        help="Optional token description",
    )
    create.set_defaults(func=cmd_create)

    list_cmd = subparsers.add_parser("list", help="List tokens")
    list_cmd.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Show token details")
    show.add_argument("token_id", help="Token ID")
    show.set_defaults(func=cmd_show)

    revoke = subparsers.add_parser("revoke", help="Revoke a token")
    revoke.add_argument("token_id", help="Token ID")
    revoke.set_defaults(func=cmd_revoke)

    rotate = subparsers.add_parser(
        "rotate",
        help="Mint a replacement token (same scopes/allowlist) and grace-expire the old one",
    )
    rotate.add_argument("token_id", help="Token ID to rotate")
    rotate.add_argument(
        "--expires-in",
        type=int,
        default=90,
        help="New token expires after N days (default 90). 0 for never.",
    )
    rotate.add_argument(
        "--grace",
        type=int,
        default=7,
        help="Days the old token keeps working after rotation (default 7).",
    )
    rotate.set_defaults(func=cmd_rotate)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
