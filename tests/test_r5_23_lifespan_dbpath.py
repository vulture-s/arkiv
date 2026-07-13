"""R5-23 (round-5 #53/#54): import-time side effects → _lifespan, and a single
source of truth for the DB path.

#53 — db.init_db() + the uvicorn-access token-redaction filter used to fire at
import time, so a transitional `import server` created .arkiv/ and ran migrations
against the production DB and mutated global logging state. They now run in
_lifespan (real startup only); TestClient enters the lifespan so fixtures work.

#54 — `--db` rebound db.DB_PATH (a frozen value copy) while health/server read
config.DB_PATH, so a --db run preflighted the DEFAULT DB while writing the backup
DB. Now db.get_db_path()/set_db_path() are the one knob; health/server route
through the accessor; value imports of DB_PATH are forbidden.
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── #54: single source of truth ─────────────────────────────────────────────
def test_get_db_path_follows_config_when_no_override(monkeypatch, tmp_path):
    import config
    import db
    p = tmp_path / "follows.db"
    monkeypatch.setattr(config, "DB_PATH", p)
    db.set_db_path(None)  # no override → follow config
    try:
        assert db.get_db_path() == p
    finally:
        db.set_db_path(None)


def test_set_db_path_overrides_and_get_conn_uses_it(tmp_path):
    import db
    override = tmp_path / "override.db"
    db.set_db_path(override)
    try:
        assert db.get_db_path() == override
        with db.get_conn() as conn:
            conn.execute("SELECT 1")
        assert override.exists(), "get_conn must open the override path, not the default"
    finally:
        db.set_db_path(None)


def test_override_decouples_from_config_default(tmp_path, monkeypatch):
    # The #54 bug: --db moved the WRITE target but health/server kept reading the
    # config default → mixed-database run. get_db_path() is now the ONLY knob, so
    # an override is what every routed reader (health.py, server.py) sees.
    import config
    import db
    default = tmp_path / "default.db"
    backup = tmp_path / "backup.db"
    monkeypatch.setattr(config, "DB_PATH", default)
    db.set_db_path(backup)
    try:
        assert db.get_db_path() == backup           # readers follow the accessor…
        assert config.DB_PATH == default            # …not the untouched config default
    finally:
        db.set_db_path(None)


def test_no_value_imports_of_db_path():
    pat = re.compile(r"^\s*from\s+(config|db)\s+import\s+.*\bDB_PATH\b", re.M)
    bad = [py.name for py in _ROOT.glob("*.py")
           if py.name != "config.py" and pat.search(py.read_text(encoding="utf-8"))]
    assert not bad, "value imports of DB_PATH freeze a copy — use db.get_db_path(): {0}".format(bad)


def test_health_and_server_read_db_path_via_accessor():
    # Regression guard for the dual-source bug: neither module may read
    # config.DB_PATH directly for the DB location (comments excluded).
    offenders = []
    for name in ("health.py", "server.py"):
        code = "\n".join(l for l in (_ROOT / name).read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        if "config.DB_PATH" in code:
            offenders.append(name)
    assert not offenders, "{0} still read config.DB_PATH directly; route through db.get_db_path()".format(offenders)


# ── #53: side effects moved to _lifespan ────────────────────────────────────
def test_redaction_filter_install_is_idempotent(server_module):
    import logging
    logger = logging.getLogger("uvicorn.access")
    RF = server_module._RedactTokenFilter
    for f in [x for x in logger.filters if isinstance(x, RF)]:
        logger.removeFilter(f)
    server_module._install_token_redaction_filter()
    server_module._install_token_redaction_filter()  # second call must not stack
    assert sum(isinstance(x, RF) for x in logger.filters) == 1


def test_lifespan_installs_filter_and_inits_db(server_module):
    # Entering the TestClient runs _lifespan → installs the redaction filter and
    # inits the DB. Proves the side effects fire on startup (not merely at import).
    import logging
    from starlette.testclient import TestClient
    logger = logging.getLogger("uvicorn.access")
    RF = server_module._RedactTokenFilter
    for f in [x for x in logger.filters if isinstance(x, RF)]:
        logger.removeFilter(f)
    assert not any(isinstance(x, RF) for x in logger.filters)
    with TestClient(server_module.app):
        assert any(isinstance(x, RF) for x in logger.filters), "lifespan must install the redaction filter"


def test_importing_server_does_not_install_filter_at_module_top():
    # The install call must live in _lifespan / _install_token_redaction_filter,
    # not as a bare module-level statement that fires on import.
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    # a bare top-level `getLogger("uvicorn.access").addFilter(` (no indentation)
    # would mean import-time mutation of global logging state.
    assert not re.search(r"^_logging\.getLogger\([\"']uvicorn\.access[\"']\)\.addFilter\(",
                         src, re.M), "token-redaction filter must be installed in _lifespan, not at import"
