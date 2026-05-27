import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def auth_app(tmp_db):
    import auth

    importlib.reload(auth)

    app = FastAPI()

    @app.get("/test/read")
    def read_endpoint(_tok: dict = Depends(auth.require_scopes("videos_read"))):
        return {"ok": True, "scope": "videos_read"}

    @app.get("/test/write")
    def write_endpoint(_tok: dict = Depends(auth.require_scopes("videos_write"))):
        return {"ok": True, "scope": "videos_write"}

    return app, auth


def _insert_token(auth_mod, name, scopes, expires_at=None, allowed_ips_json='["*"]'):
    import db

    raw = auth_mod.new_raw_token()
    token_id = auth_mod.new_token_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO access_tokens (id, name, token_hash, expires_at, allowed_ips_json) VALUES (?, ?, ?, ?, ?)",
            (token_id, name, auth_mod.hash_token(raw), expires_at, allowed_ips_json),
        )
        for scope in scopes:
            conn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (token_id, scope),
            )
    return raw, token_id


def test_require_scopes_rejects_unknown_scope(auth_app):
    _, auth = auth_app
    with pytest.raises(ValueError):
        auth.require_scopes("no_such_scope")


def test_missing_authorization_returns_401(auth_app):
    app, _ = auth_app
    response = TestClient(app).get("/test/read")
    assert response.status_code == 401
    assert "Authorization" in response.json()["detail"]


def test_invalid_token_returns_401(auth_app):
    app, _ = auth_app
    response = TestClient(app).get(
        "/test/read",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert "Invalid token" in response.json()["detail"]


def test_expired_token_returns_401(auth_app):
    app, auth = auth_app
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    raw, _ = _insert_token(auth, "expired", ["videos_read"], expires_at=expired)
    response = TestClient(app).get(
        "/test/read",
        headers={"Authorization": "Bearer {0}".format(raw)},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_token_with_wrong_scope_returns_403(auth_app):
    app, auth = auth_app
    raw, _ = _insert_token(auth, "read-only", ["videos_read"])
    response = TestClient(app).get(
        "/test/write",
        headers={"Authorization": "Bearer {0}".format(raw)},
    )
    assert response.status_code == 403
    assert "Insufficient scope" in response.json()["detail"]


def test_valid_token_with_correct_scope_returns_200_and_updates_audit(auth_app):
    import db

    app, auth = auth_app
    raw, token_id = _insert_token(auth, "valid", ["videos_read"])

    response = TestClient(app).get(
        "/test/read",
        headers={"Authorization": "Bearer {0}".format(raw)},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "scope": "videos_read"}

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT last_used_at, last_used_ip, last_used_user_agent FROM access_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()

    assert row["last_used_at"] is not None
    assert row["last_used_ip"] is not None
    assert row["last_used_user_agent"] is not None
