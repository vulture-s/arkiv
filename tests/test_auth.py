import importlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent


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

    @app.get("/test/admin")
    def admin_endpoint(_tok: dict = Depends(auth.require_scopes("admin"))):
        return {"ok": True, "scope": "admin"}

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


def _make_token(name, scopes):
    import admin

    token = admin.create_token(name=name, scopes=scopes)
    return token["raw_token"], token


def _request(client, method, path, token=None, body=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer {0}".format(token)
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return client.request(method.upper(), path, **kwargs)


def _admin_create_body():
    return {"name": "child-token", "scopes": ["videos_read"]}


def _ingest_scan_body():
    import config

    target = config.PROJECT_ROOT / "temp" / "auth-scope-test"
    target.mkdir(parents=True, exist_ok=True)
    return {"path": str(target)}


def _cli_env(tmp_db):
    env = os.environ.copy()
    env["ARKIV_DB_PATH"] = str(tmp_db)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_cli(tmp_db, *args):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "arkiv_token.py")] + list(args),
        cwd=str(REPO_ROOT),
        env=_cli_env(tmp_db),
        capture_output=True,
        text=True,
    )


def _extract_token_id(stdout):
    match = re.search(r"^Token ID:\s+(.+)$", stdout, re.MULTILINE)
    assert match, stdout
    return match.group(1).strip()


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


def test_admin_crud_flow(server_module):
    admin_raw, _ = _make_token("admin-root", ["admin"])
    with TestClient(server_module.app) as client:
        create_response = _request(
            client,
            "post",
            "/api/admin/tokens",
            token=admin_raw,
            body={"name": "pc-dev", "scopes": ["videos_read", "videos_write"]},
        )
        assert create_response.status_code == 200
        created = create_response.json()
        token_id = created["id"]
        raw_token = created["raw_token"]
        assert created["name"] == "pc-dev"
        assert created["scopes"] == ["videos_read", "videos_write"]
        assert raw_token

        list_response = _request(client, "get", "/api/admin/tokens", token=admin_raw)
        assert list_response.status_code == 200
        tokens = list_response.json()["tokens"]
        assert any(item["id"] == token_id for item in tokens)

        get_response = _request(client, "get", "/api/admin/tokens/{0}".format(token_id), token=admin_raw)
        assert get_response.status_code == 200
        fetched = get_response.json()
        assert fetched["id"] == token_id
        assert fetched["name"] == "pc-dev"
        assert fetched["scopes"] == ["videos_read", "videos_write"]
        assert "raw_token" not in fetched

        revoke_response = _request(client, "delete", "/api/admin/tokens/{0}".format(token_id), token=admin_raw)
        assert revoke_response.status_code == 200
        assert revoke_response.json() == {"ok": True, "deleted": token_id}

        missing_response = _request(client, "get", "/api/admin/tokens/{0}".format(token_id), token=admin_raw)
        assert missing_response.status_code == 404


def test_bootstrap_seeds_admin_when_db_empty_and_env_set(server_module, monkeypatch, capsys):
    import config

    raw = "bootstrap-test-token-123"
    monkeypatch.setenv("ARKIV_ADMIN_BOOTSTRAP_TOKEN", raw)
    monkeypatch.setattr(config, "ARKIV_ADMIN_BOOTSTRAP_TOKEN", raw, raising=False)

    with TestClient(server_module.app) as client:
        response = _request(client, "get", "/api/admin/tokens", token=raw)
        assert response.status_code == 200
        body = response.json()
        assert len(body["tokens"]) == 1
        assert body["tokens"][0]["name"] == "bootstrap"
        assert body["tokens"][0]["scopes"] == ["admin"]

    out = capsys.readouterr().out
    assert "[BOOTSTRAP] Admin token seeded from ARKIV_ADMIN_BOOTSTRAP_TOKEN env." in out


def test_bootstrap_noop_when_tokens_exist(tmp_db, monkeypatch):
    import config
    import admin

    admin.create_token(name="existing", scopes=["videos_read"])
    monkeypatch.setenv("ARKIV_ADMIN_BOOTSTRAP_TOKEN", "bootstrap-test-token-456")
    monkeypatch.setattr(config, "ARKIV_ADMIN_BOOTSTRAP_TOKEN", "bootstrap-test-token-456", raising=False)

    assert admin.bootstrap_admin_token_if_empty() is None


def test_bootstrap_noop_when_env_empty(server_module, monkeypatch, capsys):
    import config
    import admin

    monkeypatch.delenv("ARKIV_ADMIN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.setattr(config, "ARKIV_ADMIN_BOOTSTRAP_TOKEN", "", raising=False)

    with TestClient(server_module.app):
        pass

    out = capsys.readouterr().out
    assert "[BOOTSTRAP] No access tokens in DB and ARKIV_ADMIN_BOOTSTRAP_TOKEN unset." in out
    assert admin.bootstrap_admin_token_if_empty() is None


@pytest.mark.parametrize(
    "method,path,good_scope,wrong_scope,body_factory",
    [
        ("get", "/api/media", "videos_read", "videos_write", None),
        ("get", "/api/stats", "videos_read", "videos_write", None),
        ("get", "/api/export/metadata-csv", "media_read", "videos_read", None),
        ("post", "/api/admin/tokens", "admin", "videos_read", _admin_create_body),
        ("post", "/api/ingest/scan", "ingest_write", "videos_read", _ingest_scan_body),
        # overnight audit: endpoints that previously had NO auth at all
        ("get", "/api/proxy/status", "videos_read", "chat_read", None),
        ("post", "/api/proxy/build", "ingest_write", "videos_read", None),
    ],
)
def test_route_scope_enforcement_samples(server_module, method, path, good_scope, wrong_scope, body_factory):
    good_raw, _ = _make_token("good-{0}".format(good_scope), [good_scope])
    wrong_raw, _ = _make_token("wrong-{0}".format(wrong_scope), [wrong_scope])
    body = body_factory() if body_factory else None

    with TestClient(server_module.app) as client:
        missing = _request(client, method, path, body=body)
        assert missing.status_code == 401

        denied = _request(client, method, path, token=wrong_raw, body=body)
        assert denied.status_code == 403

        allowed = _request(client, method, path, token=good_raw, body=body)
        assert allowed.status_code == 200


@pytest.mark.parametrize(
    "method,path,wrong_scope,body",
    [
        # These previously had NO auth dependency at all (overnight audit). They
        # don't return a clean 200 on an empty DB (404/400), so we assert the
        # security property only: no token → 401, wrong scope → 403.
        ("get", "/api/stream/1", "chat_read", None),
        ("post", "/api/open-file", "videos_read", {"path": "/etc/hosts"}),
        ("post", "/api/proxy/build/1", "videos_read", None),
        ("post", "/api/ingest/ws", "videos_read", {"path": "/tmp", "limit": 1}),
    ],
)
def test_previously_unauthed_endpoints_now_enforce_scope(server_module, method, path, wrong_scope, body):
    wrong_raw, _ = _make_token("wrong-{0}".format(wrong_scope), [wrong_scope])
    with TestClient(server_module.app) as client:
        assert _request(client, method, path, body=body).status_code == 401
        assert _request(client, method, path, token=wrong_raw, body=body).status_code == 403


def test_cli_create_inserts_token(tmp_db):
    result = _run_cli(
        tmp_db,
        "create",
        "--name",
        "pc-dev",
        "--scopes",
        "videos_read,videos_write",
        "--ip-allowlist",
        "127.0.0.1/32,100.64.0.0/10",
        "--expires-in",
        "30",
        "--description",
        "workstation token",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Token created." in result.stdout

    token_id = _extract_token_id(result.stdout)

    import db

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, description, expires_at, allowed_ips_json FROM access_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        assert row is not None
        assert row["name"] == "pc-dev"
        assert row["description"] == "workstation token"
        assert row["expires_at"] is not None
        assert json.loads(row["allowed_ips_json"]) == ["127.0.0.1/32", "100.64.0.0/10"]

        scope_rows = conn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ? ORDER BY scope",
            (token_id,),
        ).fetchall()
        assert [scope_row["scope"] for scope_row in scope_rows] == ["videos_read", "videos_write"]


def test_cli_list_includes_created_tokens(tmp_db):
    create = _run_cli(
        tmp_db,
        "create",
        "--name",
        "review-station",
        "--scopes",
        "media_read,videos_read",
    )
    assert create.returncode == 0, create.stdout + create.stderr

    result = _run_cli(tmp_db, "list")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "review-station" in result.stdout
    assert "media_read,videos_read" in result.stdout
    assert "RAW TOKEN" not in result.stdout


def test_cli_revoke_removes_token(tmp_db):
    create = _run_cli(
        tmp_db,
        "create",
        "--name",
        "old-token",
        "--scopes",
        "videos_read",
    )
    assert create.returncode == 0, create.stdout + create.stderr
    token_id = _extract_token_id(create.stdout)

    revoke = _run_cli(tmp_db, "revoke", token_id)
    assert revoke.returncode == 0, revoke.stdout + revoke.stderr
    assert "Revoked token" in revoke.stdout

    import db

    with db.get_conn() as conn:
        token_row = conn.execute(
            "SELECT id FROM access_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        scope_rows = conn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ?",
            (token_id,),
        ).fetchall()

    assert token_row is None
    assert scope_rows == []

    listing = _run_cli(tmp_db, "list")
    assert token_id[:22] not in listing.stdout
    assert "old-token" not in listing.stdout


def test_ip_allowlist_cidr_match(auth_app):
    app, auth = auth_app
    raw, _ = _insert_token(auth, "cidr", ["videos_read"], allowed_ips_json='["100.64.0.0/10"]')

    allowed_client = TestClient(app, client=("100.64.5.3", 12345))
    allowed = allowed_client.get(
        "/test/read",
        headers={"Authorization": "Bearer {0}".format(raw)},
    )
    assert allowed.status_code == 200

    denied_client = TestClient(app, client=("100.128.0.1", 12345))
    denied = denied_client.get(
        "/test/read",
        headers={"Authorization": "Bearer {0}".format(raw)},
    )
    assert denied.status_code == 403
    assert "allowlist" in denied.json()["detail"].lower()


def test_multi_scope_token_all_required_present(auth_app):
    app, auth = auth_app
    raw, _ = _insert_token(auth, "multi", ["videos_read", "videos_write"])

    with TestClient(app) as client:
        read = client.get(
            "/test/read",
            headers={"Authorization": "Bearer {0}".format(raw)},
        )
        assert read.status_code == 200
        assert read.json() == {"ok": True, "scope": "videos_read"}

        write = client.get(
            "/test/write",
            headers={"Authorization": "Bearer {0}".format(raw)},
        )
        assert write.status_code == 200
        assert write.json() == {"ok": True, "scope": "videos_write"}

        admin = client.get(
            "/test/admin",
            headers={"Authorization": "Bearer {0}".format(raw)},
        )
        assert admin.status_code == 403
        assert "admin" in admin.json()["detail"].lower()
