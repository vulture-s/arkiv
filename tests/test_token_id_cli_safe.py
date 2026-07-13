"""Regression: token ids must be CLI-safe (never start with '-').

`new_token_id()` draws from the base64url / nanoid alphabet [A-Za-z0-9_-]. An id
that STARTS with '-' makes `arkiv_token.py revoke <id>` argparse-parse the id as
an option flag, leaving the positional unfilled ("error: the following arguments
are required: token_id"). That was the intermittent ~1/64 CI failure of
test_cli_revoke_removes_token — deterministic per-id, not truly random.
"""
import auth


def test_new_token_id_never_starts_with_dash():
    ids = [auth.new_token_id() for _ in range(5000)]
    # Without the re-roll, ~1/64 of 5000 ids (~78) would start with '-'.
    bad = [t for t in ids if t.startswith("-")]
    assert not bad, "leading-dash token ids break `revoke <id>`: {0}".format(bad[:5])
    assert all(ids), "token ids must be non-empty"
