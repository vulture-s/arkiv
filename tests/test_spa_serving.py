"""Svelte cutover (Phase 3): server.py serves ONLY the built SPA at /. The legacy
Tailwind index.html + the /legacy route + the ARKIV_UI=legacy escape hatch are
retired. A missing build now surfaces a clear "run npm run build" message instead
of falling back to a page that no longer exists.

frontend/dist is gitignored and CI doesn't build the frontend, so these tests
monkeypatch FRONTEND_DIST to a fake build dir. The SPA shell is identified by
`id="app"`.
"""

SPA_INDEX = '<!doctype html><html><body><div id="app"></div>' \
            '<script type="module" src="/assets/index-abc.js"></script></body></html>'


def _fake_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(SPA_INDEX, encoding="utf-8")
    return dist


def test_load_index_serves_built_spa(server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "FRONTEND_DIST", _fake_dist(tmp_path))
    html = server_module._load_index()
    assert 'id="app"' in html


def test_load_index_without_build_shows_build_hint_not_legacy(server_module, tmp_path, monkeypatch):
    # No dist → a clear "run npm run build" message, NOT a legacy Tailwind page
    # (which no longer exists).
    monkeypatch.setattr(server_module, "FRONTEND_DIST", tmp_path / "does-not-exist")
    html = server_module._load_index()
    assert 'id="app"' not in html
    assert "npm run build" in html


def test_arkiv_ui_legacy_env_is_ignored(server_module, tmp_path, monkeypatch):
    # The rollback hatch is gone: ARKIV_UI=legacy no longer changes anything —
    # the SPA is served regardless.
    monkeypatch.setattr(server_module, "FRONTEND_DIST", _fake_dist(tmp_path))
    monkeypatch.setenv("ARKIV_UI", "legacy")
    html = server_module._load_index()
    assert 'id="app"' in html


def test_root_route_serves_spa_end_to_end(fastapi_client, server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "FRONTEND_DIST", _fake_dist(tmp_path))
    r = fastapi_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="app"' in r.text


def test_legacy_route_is_gone(fastapi_client, server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "FRONTEND_DIST", _fake_dist(tmp_path))
    r = fastapi_client.get("/legacy")
    assert r.status_code == 404


def test_dit_redirects_to_spa_offload(fastapi_client):
    # /dit used to serve the standalone island; now it 308-redirects to the SPA.
    r = fastapi_client.get("/dit", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/#/offload"


def test_tailwind_routes_are_gone(fastapi_client):
    assert fastapi_client.get("/tailwind.cdn.js").status_code == 404
    assert fastapi_client.get("/tailwind-static.css").status_code == 404
