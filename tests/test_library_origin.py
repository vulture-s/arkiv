"""The grandfathering stamp: written once, never restamped, absent means exempt.

Nothing reads this yet. It ships ahead of the feature it serves on purpose —
the free-tier project cap does not exist in any build in the wild, and the
published terms promise that libraries in use before it ships keep unlimited
projects permanently. That promise can only be kept with evidence recorded
*before* the cap exists: afterwards there is no way to tell whether a
two-project library was started under the old terms or the new ones.

So these tests guard a contract with no current caller. The failure they exist
to prevent is silent and one-way — an upgrade that restamps an old library
revokes an exemption that was promised in public, and nothing would surface it.
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
