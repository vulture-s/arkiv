"""Guards for `ingest.py --migrate-storage`'s idempotency check.

The check used to be `if (BASE_DIR/.arkiv/project.db).exists(): print SKIP; return`.
That conflates two different states. An empty `project.db` also appears the moment
anything opens the new layout — a stray server start, an ingest with no `--dir`, a
health check — and from then on the migration refused to run while the real library
sat in `BASE_DIR/media.db`. It stranded 62 media rows on one machine, and nothing
surfaced it: the tool printed `[SKIP]` and returned, so the process exited **0** and
both a human skimming output and any script checking the return code read it as
success.

The remedy it printed made things worse: `rm -rf .arkiv` deletes the `access_tokens`
rows too, and the DB stores only hashes — following that advice revokes every API
token on the machine with no way to recover them, only reissue.

These tests therefore assert on the exit status and on the absence of the
destructive advice, not merely on wording.
"""
import sqlite3
from pathlib import Path

import pytest

import config
import ingest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_db(path, media_rows=0, token_rows=0, corrupt=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if corrupt:
        path.write_bytes(b"this is not a sqlite database at all")
        return path
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute("CREATE TABLE access_tokens (id INTEGER PRIMARY KEY, token_hash TEXT)")
    for i in range(media_rows):
        conn.execute("INSERT INTO media (path) VALUES (?)", ("/tmp/clip{0}.mp4".format(i),))
    for i in range(token_rows):
        conn.execute("INSERT INTO access_tokens (token_hash) VALUES (?)", ("h{0}".format(i),))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Point BASE_DIR at a scratch dir — it is normally the install directory."""
    d = tmp_path / "lib"
    d.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", d)
    return d


def test_empty_new_db_blocking_real_legacy_data_does_not_report_success(base, capsys):
    """THE regression: an empty shell in the new layout plus a populated legacy DB
    used to print [SKIP] and return normally, stranding the library in silence."""
    _make_db(base / ".arkiv" / "project.db", media_rows=0)
    _make_db(base / "media.db", media_rows=62)

    with pytest.raises(SystemExit) as exc:
        ingest._migrate_storage()

    assert exc.value.code not in (0, None), \
        "a blocked migration must not exit 0 — that is what made this silent"
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert "62" in out, "must show what is actually stranded"
    assert (base / "media.db").exists(), "a blocked run must touch nothing"


def test_blocked_run_never_advises_rm_rf_on_a_dir_holding_tokens(base, capsys):
    _make_db(base / ".arkiv" / "project.db", media_rows=0, token_rows=3)
    _make_db(base / "media.db", media_rows=10)

    with pytest.raises(SystemExit):
        ingest._migrate_storage()

    out = capsys.readouterr().out
    assert "rm -rf" not in out, "that advice revokes every API token, unrecoverably"
    assert "mv " in out, "must offer a reversible remedy instead"
    assert "access_tokens" in out and "3" in out, "must say what would be lost"


def test_genuinely_migrated_library_still_skips_quietly(base, capsys):
    """The real idempotent case stays idempotent: new layout populated, nothing left
    in legacy — skip and return normally."""
    _make_db(base / ".arkiv" / "project.db", media_rows=62)

    ingest._migrate_storage()  # must not raise SystemExit

    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "BLOCKED" not in out


def test_empty_new_db_with_empty_legacy_is_not_blocked(base, capsys):
    """An empty shell only matters when something is actually waiting to move."""
    _make_db(base / ".arkiv" / "project.db", media_rows=0)
    _make_db(base / "media.db", media_rows=0)

    ingest._migrate_storage()

    assert "BLOCKED" not in capsys.readouterr().out


def test_unreadable_new_db_is_reported_as_unknown_not_assumed_empty(base, capsys):
    """'Could not read' and 'is empty' are different answers; collapsing them is how
    the original guard acted on an unverified assumption."""
    _make_db(base / ".arkiv" / "project.db", corrupt=True)
    _make_db(base / "media.db", media_rows=7)

    with pytest.raises(SystemExit) as exc:
        ingest._migrate_storage()

    assert exc.value.code not in (0, None)
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert "讀不出來" in out
    assert (base / ".arkiv" / "project.db").exists(), "must not touch what it can't read"


def test_helpers_distinguish_zero_from_unreadable(tmp_path):
    empty = _make_db(tmp_path / "empty.db", media_rows=0)
    full = _make_db(tmp_path / "full.db", media_rows=4, token_rows=2)
    junk = _make_db(tmp_path / "junk.db", corrupt=True)

    assert ingest._media_row_count(empty) == 0
    assert ingest._media_row_count(full) == 4
    assert ingest._media_row_count(junk) is None
    assert ingest._media_row_count(tmp_path / "nope.db") is None

    assert ingest._access_token_count(full) == 2
    assert ingest._access_token_count(junk) is None

    assert ingest._fmt_count(0) == "0"
    assert ingest._fmt_count(4) == "4"
    assert ingest._fmt_count(None) == "?", "unknown must never render as a number"


def test_row_count_does_not_create_or_mutate_the_file(tmp_path):
    """The guard inspects databases whose state is the thing in question — opening
    one read-only must not bring a missing file into existence."""
    missing = tmp_path / "absent.db"
    assert ingest._media_row_count(missing) is None
    assert not missing.exists(), "read-only probe must not create the DB"


def test_no_print_statement_hands_the_user_an_rm_rf():
    """Pin it at the source too, so a later edit can't quietly reintroduce it.

    Scoped to what actually reaches the user: a *comment* saying "we removed the
    `rm -rf` advice because it destroys tokens" is the documentation of this fix and
    must stay allowed. Only emitted strings are the hazard."""
    offenders = [
        (n, line.strip())
        for n, line in enumerate((REPO_ROOT / "ingest.py").read_text(encoding="utf-8").splitlines(), 1)
        if "rm -rf" in line and "print(" in line
    ]
    assert not offenders, "printed rm -rf advice: {0}".format(offenders)
