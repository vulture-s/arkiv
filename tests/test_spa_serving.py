"""Svelte cutover (Phase 1): server.py serves the built SPA at / when
frontend/dist exists, and falls back to the legacy Tailwind index.html when it
doesn't (fresh clone before `npm run build`) or when ARKIV_UI=legacy is set.

frontend/dist is gitignored and CI doesn't build the frontend, so these tests
monkeypatch FRONTEND_DIST to a fake build dir — they exercise the cutover
DECISION logic deterministically without needing a real Vite build. The SPA
shell is identified by `id="app"` (the legacy page has no such marker).
"""

SPA_INDEX = '<!doctype html><html><body><div id="app"></div>' \
            '<script type="module" src="/assets/index-abc.js"></script></body></html>'


def _fake_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(SPA_INDEX, encoding="utf-8")
    return dist


def test_load_index_prefers_built_spa(server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "FRONTEND_DIST", _fake_dist(tmp_path))
    monkeypatch.delenv("ARKIV_UI", raising=False)
    html = server_module._load_index()
    assert 'id="app"' in html


def test_load_index_falls_back_to_legacy_without_build(server_module, tmp_path, monkeypatch):
    # No dist → the legacy ROOT/index.html (shipped in the repo) is returned.
    monkeypatch.setattr(server_module, "FRONTEND_DIST", tmp_path / "does-not-exist")
    monkeypatch.delenv("ARKIV_UI", raising=False)
    html = server_module._load_index()
    assert 'id="app"' not in html  # legacy Tailwind page, not the SPA shell
    assert html.strip()  # non-empty


def test_arkiv_ui_legacy_forces_old_page(server_module, tmp_path, monkeypatch):
    # Rollback escape hatch: even with a build present, ARKIV_UI=legacy → old page.
    monkeypatch.setattr(server_module, "FRONTEND_DIST", _fake_dist(tmp_path))
    monkeypatch.setenv("ARKIV_UI", "legacy")
    html = server_module._load_index()
    assert 'id="app"' not in html


def test_root_route_serves_spa_end_to_end(fastapi_client, server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "FRONTEND_DIST", _fake_dist(tmp_path))
    monkeypatch.delenv("ARKIV_UI", raising=False)
    r = fastapi_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="app"' in r.text
