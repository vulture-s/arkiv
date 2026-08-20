"""The grandfathering stamp: written once, never restamped, absent means exempt.

The published terms promise that libraries in use before the free-tier cap
shipped keep unlimited projects permanently. That promise can only be kept with
evidence recorded *before* the cap exists: afterwards there is no way to tell
whether a two-project library was started under the old terms or the new ones.

The failure these exist to prevent is silent and one-way — anything that
reclassifies an old library as post-cap revokes an exemption promised in public,
the library keeps working, and nothing surfaces it.

(This file used to open with "nothing reads this yet". `entitlements` reads it
now — the cap armed in 1.1.0.)
"""
from __future__ import annotations

import importlib

import pytest


def test_fresh_library_is_stamped(tmp_db):
    import config
    import db

    origin = db.get_library_origin()
    assert origin is not None, "a newly initialised library must record its origin"
    assert origin["version"] == config.VERSION
    assert origin["created_at"], "the stamp must carry a timestamp"


def test_reinit_does_not_restamp(tmp_db, monkeypatch):
    """An upgrade must not overwrite the stamp — that would revoke the exemption.

    This is the one that matters. `INSERT OR IGNORE` makes it true; an upsert or
    a plain INSERT-after-DELETE would not, and the damage would be invisible:
    the library keeps working, just silently reclassified as post-cap.
    """
    import config
    import db

    first = db.get_library_origin()
    assert first is not None

    # Simulate the user upgrading to a later build and reopening the library.
    monkeypatch.setattr(config, "VERSION", "99.0.0")
    db.init_db()

    after = db.get_library_origin()
    assert after["version"] == first["version"], (
        "re-initialising under a newer version restamped the library; "
        "an upgraded library would lose its pre-cap exemption"
    )
    assert after["created_at"] == first["created_at"]


def test_library_that_predates_the_anchor_is_not_stamped_with_the_current_version(
    tmp_path, monkeypatch
):
    """The upgrade path that skipped the anchor's one-release window.

    The anchor shipped in 1.0.0 and the cap armed in 1.1.0 — one day apart.
    Anyone upgrading straight from 0.12.x to 1.1.0 (i.e. to the newest release,
    which is what people do) opens a long-standing library whose first init
    under the new build is also its first stamp. Stamping the CURRENT version
    there marks a years-old library as post-cap and silently revokes the
    exemption the product page promises permanently.

    `entitlements`' latch cannot cover this: `init_db` runs when the app opens,
    before any entitlement question is asked, so the pre-cap state is never
    observable to be latched.
    """
    import config
    import db
    import entitlements
    import sqlite3

    db_path = tmp_path / "old.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "VERSION", "1.0.0")
    db.init_db()

    # A 0.12.x-era library: real content, and never stamped because no build it
    # ever ran carried the anchor.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DROP TABLE library_meta")
        conn.execute("INSERT INTO media (path, filename) VALUES ('/a.mp4','a.mp4')")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(config, "VERSION", entitlements.CAP_VERSION)
    db.init_db()

    origin = db.get_library_origin()
    assert origin["version"] == db.PRE_ANCHOR_VERSION, (
        "an existing library was stamped with the capped version on first open; "
        "its permanent exemption is gone"
    )
    assert entitlements.install_is_grandfathered([db_path]) is True
    assert entitlements.check_add_project(99, db_paths=[db_path]).allowed is True
    assert entitlements.check_cross_project(db_paths=[db_path]).allowed is True


def test_a_genuinely_new_library_is_still_capped(tmp_path, monkeypatch):
    """The other direction: the fix above must not exempt everybody.

    A library created BY the capped build has no prior use to honour, so it must
    carry the real version and the cap must bite.
    """
    import config
    import db
    import entitlements

    db_path = tmp_path / "new.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "VERSION", entitlements.CAP_VERSION)
    db.init_db()

    assert db.get_library_origin()["version"] == entitlements.CAP_VERSION
    assert entitlements.install_is_grandfathered([db_path]) is False
    assert entitlements.check_add_project(3, db_paths=[db_path]).allowed is False


def test_legacy_library_without_the_table_reads_as_exempt(tmp_db):
    """No row is the oldest case, so it must read as None rather than raise.

    Libraries created before this code existed carry no `library_meta` at all.
    Every build that shipped before it allowed unlimited projects, so callers
    must treat None as exempt. A crash here would turn the licensing question
    into an outage for exactly the longest-standing users.
    """
    import db

    with db.get_conn() as conn:
        conn.execute("DROP TABLE IF EXISTS library_meta")

    assert db.get_library_origin() is None


def test_row_present_but_key_missing_reads_as_exempt(tmp_db):
    """Table there, key gone — still the permissive answer, not an exception."""
    import db

    with db.get_conn() as conn:
        conn.execute("DELETE FROM library_meta WHERE key='first_seen_version'")

    assert db.get_library_origin() is None


def test_stamp_survives_other_init_work(tmp_db, monkeypatch):
    """init_db does a lot besides this; the stamp must not be collateral damage."""
    import config
    import db

    before = db.get_library_origin()
    for v in ("0.99.0", "1.0.0", "2.5.1"):
        monkeypatch.setattr(config, "VERSION", v)
        db.init_db()
    assert db.get_library_origin() == before
