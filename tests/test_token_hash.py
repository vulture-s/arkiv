"""Phase 16.1 — HMAC token hash + dual-read transition."""
import importlib

import pytest
from fastapi import HTTPException

import db


@pytest.fixture
def mods(tmp_db):
    config = importlib.import_module("config")
    auth = importlib.import_module("auth")
    admin = importlib.import_module("admin")
    return config, auth, admin


def _row(token_id):
    with db.get_conn() as c:
        return c.execute(
            "SELECT token_hash, hash_algo FROM access_tokens WHERE id=?", (token_id,)
        ).fetchone()


# --------------------------------------------------------------------------
# minting
# --------------------------------------------------------------------------
def test_no_key_mints_sha256(mods, monkeypatch):
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "")
    tok = admin.create_token(name="t", scopes=["videos_read"])
    row = _row(tok["id"])
    assert row["hash_algo"] == "sha256"
    assert row["token_hash"] == auth.hash_token(tok["raw_token"])
    assert "videos_read" in auth.resolve_raw_token(tok["raw_token"], "", "")["scopes"]


def test_key_mints_hmac_not_bare_sha256(mods, monkeypatch):
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "server-secret-123456")
    tok = admin.create_token(name="t", scopes=["videos_read"])
    raw = tok["raw_token"]
    row = _row(tok["id"])
    assert row["hash_algo"] == "hmac-sha256"
    assert row["token_hash"] != auth.hash_token(raw)        # not a bare sha256
    assert row["token_hash"] == auth._hmac_token(raw)
    assert "videos_read" in auth.resolve_raw_token(raw, "", "")["scopes"]


# --------------------------------------------------------------------------
# dual-read transition (the red line: existing tokens keep working)
# --------------------------------------------------------------------------
def test_legacy_sha256_token_still_resolves_after_key_set(mods, monkeypatch):
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "")     # mint legacy
    tok = admin.create_token(name="legacy", scopes=["videos_read"])
    raw = tok["raw_token"]
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "server-secret-123456")  # key turned on
    # must NOT 401 — dual-read still recognises the sha256 token
    assert "videos_read" in auth.resolve_raw_token(raw, "", "")["scopes"]


def test_legacy_token_opportunistically_migrates_to_hmac(mods, monkeypatch):
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "")
    tok = admin.create_token(name="legacy", scopes=["videos_read"])
    raw = tok["raw_token"]
    assert _row(tok["id"])["hash_algo"] == "sha256"
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "server-secret-123456")
    auth.resolve_raw_token(raw, "", "")            # use → rehash
    row = _row(tok["id"])
    assert row["hash_algo"] == "hmac-sha256"
    assert row["token_hash"] == auth._hmac_token(raw)
    # and it still resolves after the in-place migration
    assert "videos_read" in auth.resolve_raw_token(raw, "", "")["scopes"]


def test_rejected_request_does_not_migrate(mods, monkeypatch):
    # Codex SHOULD-FIX: a token that fails validation (expired) must NOT be
    # rehashed — migration happens only after the token fully validates.
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "")
    tok = admin.create_token(name="legacy", scopes=["videos_read"], expires_in_days=1)
    raw = tok["raw_token"]
    # force-expire the row
    with db.get_conn() as c:
        c.execute("UPDATE access_tokens SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (tok["id"],))
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "server-secret-123456")
    with pytest.raises(HTTPException):
        auth.resolve_raw_token(raw, "", "")
    assert _row(tok["id"])["hash_algo"] == "sha256"  # not migrated despite key


def test_no_migration_when_key_absent(mods, monkeypatch):
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "")
    tok = admin.create_token(name="legacy", scopes=["videos_read"])
    auth.resolve_raw_token(tok["raw_token"], "", "")
    assert _row(tok["id"])["hash_algo"] == "sha256"   # untouched without a key


# --------------------------------------------------------------------------
# failure modes
# --------------------------------------------------------------------------
def test_hmac_token_fails_if_key_lost(mods, monkeypatch):
    # The documented tradeoff: losing the key invalidates HMAC tokens.
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "server-secret-123456")
    tok = admin.create_token(name="t", scopes=["videos_read"])
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "")   # key lost
    with pytest.raises(HTTPException):
        auth.resolve_raw_token(tok["raw_token"], "", "")


def test_wrong_key_does_not_authenticate(mods, monkeypatch):
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "key-A-xxxxxxxxxxxx")
    tok = admin.create_token(name="t", scopes=["videos_read"])
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "key-B-yyyyyyyyyyyy")
    with pytest.raises(HTTPException):
        auth.resolve_raw_token(tok["raw_token"], "", "")


def test_invalid_token_401(mods, monkeypatch):
    config, auth, admin = mods
    monkeypatch.setattr(config, "ARKIV_TOKEN_HMAC_KEY", "server-secret-123456")
    with pytest.raises(HTTPException):
        auth.resolve_raw_token("not-a-real-token", "", "")


def test_empty_token_401(mods):
    _, auth, _ = mods
    with pytest.raises(HTTPException):
        auth.resolve_raw_token("", "", "")
