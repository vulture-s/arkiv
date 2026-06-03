# Phase 16.1 — Token hash salt/HMAC

**Status: IMPLEMENTED 2026-06-03** — Option A (env-var HMAC key + dual-read), the
recommendation below, chosen by the operator. `ARKIV_TOKEN_HMAC_KEY` (when set)
stores tokens as HMAC-SHA256; existing sha256 tokens keep working via dual-read
and upgrade in place on next use. No flag-day. The rest of this doc is the
original decision record.

## Current state

`auth.hash_token` is an **unsalted SHA-256**:

```python
def hash_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

Tokens are 256-bit random (`secrets.token_urlsafe(32)`), so an unsalted hash is
**not practically brute-forceable** — this is pure defense-in-depth (e.g. a
leaked `access_tokens` table can't be rainbow-tabled, though it never could be
for 256-bit inputs anyway). That's why this is **low priority** and was held out
of rounds 1–2.

## Why it wasn't done autonomously

It's a **breaking change with a red line**: switching the hash function
invalidates every existing token (`token_hash` column no longer matches), which
would lock out every deployed client (PC, mini, any tailscale collaborator) on
upgrade. The roadmap red line: *"不可讓現有部署的 token 一夕全失效而無過渡。"*

It also needs a **product decision** that isn't mine to make:

> **DECISION NEEDED — where does the HMAC key live, and how is it rotated?**
> - Option A: `ARKIV_TOKEN_HMAC_KEY` env var (operator sets it; lost key = all
>   tokens invalid — same failure mode as losing the DB).
> - Option B: auto-generated key file under `.arkiv/` (0600), created on first
>   run. Simpler ops, but the key sits next to the DB it protects (lower marginal
>   security — mainly protects against DB-only exfiltration).
> - Option C: per-token random salt column (no server-wide key to manage;
>   slightly larger schema; each row self-contained).

## Proposed non-breaking transition (dual-read)

Once the key strategy is chosen, ship it without invalidating anyone:

1. Add `hash_algo` (or `salt`) column to `access_tokens` (default `'sha256'` for
   existing rows).
2. `resolve_raw_token` **verifies against the row's recorded algo** — old rows
   verify with plain SHA-256, new rows with HMAC/salted. Both work
   simultaneously (the dual-read).
3. New tokens (`admin.create_token`) are minted with the new algo.
4. Optional opportunistic migration: on a successful old-algo verify, re-hash
   with the new algo and update the row, so the fleet drains to the new scheme
   over normal use. Never forced.

Net effect: existing tokens keep working; new tokens are salted/HMAC'd; no
flag-day.

## Acceptance (when implemented)

- A token minted before the change still authenticates after it (dual-read).
- A token minted after the change authenticates and its stored hash is *not* a
  bare SHA-256 of the raw token.
- `red→green` tests for both algos + the migration path; Codex review.

## Recommendation

Low urgency (256-bit tokens). When picked up, **Option A (env HMAC key) +
dual-read** is the cleanest: no schema-per-row state, key lives outside the DB,
and the transition is invisible to clients. Needs the operator to commit to
managing one env var (and accept that losing it = re-mint tokens, same as losing
the DB).

*Drafted 2026-06-03 during security round 3. 16.2 shipped; this awaits the
key-management call.*
