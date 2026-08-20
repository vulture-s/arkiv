"""Free-tier cap, grandfathering, and Pro entitlement.

These assert the PROMISE, not the implementation: the published terms say the
allowance applies to new installations from a future release onward and that
libraries already in use keep unlimited projects permanently. Every test below
is a way that promise can be broken silently.

The gate is inert until `config.VERSION` reaches `CAP_VERSION`, so the armed
cases pin `config.VERSION` explicitly rather than inheriting whatever the
current build says. A test that only passed because the cap happened to be
switched off would be worse than no test — it would go green for the whole
pre-1.1.0 window and then start failing on the release that matters most.
"""
import importlib
import json
import sqlite3

import pytest

import config
import entitlements


ARMED = entitlements.CAP_VERSION       # 1.1.0 — the cap is in force
PRE_CAP = "1.0.0"                      # a build (and a library) from before it


@pytest.fixture(autouse=True)
def _no_ambient_pro_license(tmp_path, monkeypatch):
    """Never let the developer's real ~/.arkiv/pro-license.json decide a test.

    Without this every assertion about the free tier would silently invert on a
    machine that happens to own a licence — passing for the wrong reason on the
    maintainer's box and failing only in CI, or vice versa.
    """
    monkeypatch.setenv("ARKIV_PRO_LICENSE", str(tmp_path / "absent" / "pro-license.json"))


def _library(path, version):
    """A real SQLite library stamped with `version` (None = no anchor row)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE library_meta (key TEXT PRIMARY KEY, value TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        if version is not None:
            conn.execute(
                "INSERT INTO library_meta (key, value) VALUES ('first_seen_version', ?)",
                (version,),
            )
        conn.commit()
    finally:
        conn.close()
    return path


# ── version comparison ────────────────────────────────────────────────────────

def test_versions_order_numerically_not_lexically():
    # "1.10.0" < "1.9.0" is TRUE as strings and false in fact. A string compare
    # here would hand the exemption to every user on a double-digit minor.
    assert entitlements.parse_version("1.10.0") > entitlements.parse_version("1.9.0")
    assert entitlements.parse_version("1.1") == entitlements.parse_version("1.1.0")


def test_unreadable_version_strings_are_exempt_not_capped():
    # The direction matters more than the parsing: "we cannot tell" must never
    # come out as "capped", or the longest-standing users lose their exemption.
    for unknown in (None, "", "   ", "garbage", "v1.2.3", "1.x.0"):
        assert entitlements.predates_cap(unknown) is True


def test_cap_boundary_is_the_cap_version_itself():
    assert entitlements.predates_cap("1.0.9") is True
    assert entitlements.predates_cap(entitlements.CAP_VERSION) is False
    assert entitlements.predates_cap("1.2.0") is False
    # Suffixed builds reduce to their numeric core rather than falling into the
    # unknown bucket — a dated library is evidence even when the tag is messy.
    assert entitlements.predates_cap("1.1.0-rc1") is False
    assert entitlements.predates_cap("1.0.0+win") is True


# ── reading the anchor ────────────────────────────────────────────────────────

def test_probing_a_library_never_creates_it(tmp_path):
    """A licensing question must not write to the thing it inspects."""
    missing = tmp_path / "never" / "project.db"
    assert entitlements.read_library_origin(missing) is None
    assert not missing.exists()
    assert not missing.parent.exists()

    # And an existing library must not gain -wal / -shm side files from a probe.
    live = _library(tmp_path / "live" / "project.db", PRE_CAP)
    entitlements.read_library_origin(live)
    siblings = {p.name for p in live.parent.iterdir()}
    assert siblings == {"project.db"}


def test_reads_the_recorded_version(tmp_path):
    live = _library(tmp_path / "a" / "project.db", "0.12.1")
    assert entitlements.read_library_origin(live) == "0.12.1"


def test_library_without_an_anchor_row_reads_as_unknown(tmp_path):
    live = _library(tmp_path / "b" / "project.db", None)
    assert entitlements.read_library_origin(live) is None


# ── the machine-level grandfather question ────────────────────────────────────

def test_old_library_grandfathers_the_whole_install(tmp_path):
    old = _library(tmp_path / "old" / "project.db", PRE_CAP)
    new = _library(tmp_path / "new" / "project.db", ARMED)
    assert entitlements.install_is_grandfathered([old, new]) is True


def test_install_with_only_new_libraries_is_not_grandfathered(tmp_path):
    new = _library(tmp_path / "new" / "project.db", ARMED)
    assert entitlements.install_is_grandfathered([new]) is False


def test_install_with_no_libraries_is_not_grandfathered():
    # A genuinely fresh install: no evidence of prior use. Safe to read as new
    # because it has zero projects, so the cap cannot bite it today anyway.
    assert entitlements.install_is_grandfathered([]) is False


def test_unreadable_library_counts_as_old(tmp_path):
    # A file that exists but is not a usable SQLite DB (a stub, a truncated
    # copy, a permissions failure) is not evidence that the install is new.
    stub = tmp_path / "stub" / "project.db"
    stub.parent.mkdir(parents=True)
    stub.write_text("not a database", encoding="utf-8")
    assert entitlements.install_is_grandfathered([stub]) is True


def test_missing_library_does_not_manufacture_an_exemption(tmp_path):
    """The unmounted-NAS case.

    A registry entry whose project.db is absent must not read as exempt, or the
    tier would flip on a bad-mount day and back again the next — and an install
    could earn permanent unlimited projects by registering a path that never
    existed.
    """
    assert entitlements.install_is_grandfathered([tmp_path / "gone" / "project.db"]) is False


# ── the rule's own start date ─────────────────────────────────────────────────

def test_cap_does_not_bite_on_builds_that_predate_it(monkeypatch, tmp_path):
    """The published terms say "from a future release onward".

    The failing shape this guards: an install that registered several project
    roots but never ingested into them has NO project.db anywhere, so nothing
    reads as grandfathered — and without an explicit start-date check a 1.0.0
    build would refuse the fourth project even though 1.0.0 promised no limit.
    """
    monkeypatch.setattr(config, "VERSION", PRE_CAP)
    verdict = entitlements.check_add_project(99, db_paths=[])
    assert verdict.allowed is True
    assert verdict.code == "cap_inactive"
    assert entitlements.check_cross_project(db_paths=[]).allowed is True


def test_cap_bites_once_the_shipping_version_reaches_it(monkeypatch):
    monkeypatch.setattr(config, "VERSION", ARMED)
    assert entitlements.check_add_project(2, db_paths=[]).allowed is True
    refused = entitlements.check_add_project(3, db_paths=[])
    assert refused.allowed is False
    assert refused.code == "project_limit"
    # The refusal has to name the limit and the way out — a message that only
    # says "not allowed" leaves the user with nothing to do (PR #315).
    assert "3" in refused.reason and "Pro" in refused.reason


def test_grandfathered_install_keeps_everything_when_armed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VERSION", ARMED)
    old = _library(tmp_path / "old" / "project.db", PRE_CAP)
    assert entitlements.check_add_project(99, db_paths=[old]).code == "grandfathered"
    assert entitlements.check_cross_project(db_paths=[old]).allowed is True


# ── Pro entitlement ───────────────────────────────────────────────────────────

def test_licence_file_unlocks_both_features(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VERSION", ARMED)
    licence = tmp_path / "pro-license.json"
    licence.write_text(
        json.dumps({"licensee": "Studio A", "key": "ARKIV-PRO-0001"}), encoding="utf-8"
    )
    monkeypatch.setenv("ARKIV_PRO_LICENSE", str(licence))
    assert entitlements.has_pro() is True
    assert entitlements.check_add_project(99, db_paths=[]).code == "pro"
    assert entitlements.check_cross_project(db_paths=[]).allowed is True


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",                       # corrupt
        json.dumps([1, 2, 3]),             # right file, wrong shape
        json.dumps({"licensee": "Studio"}),  # named but keyless
        json.dumps({"key": "ARKIV-PRO-1"}),  # keyed but nameless
        json.dumps({"licensee": " ", "key": " "}),  # whitespace is not a name
    ],
)
def test_unusable_licence_file_does_not_unlock(monkeypatch, tmp_path, payload):
    licence = tmp_path / "pro-license.json"
    licence.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("ARKIV_PRO_LICENSE", str(licence))
    assert entitlements.has_pro() is False


def test_status_reports_whether_the_gate_is_even_armed(monkeypatch):
    monkeypatch.setattr(config, "VERSION", PRE_CAP)
    assert entitlements.status(0, db_paths=[])["armed"] is False
    monkeypatch.setattr(config, "VERSION", ARMED)
    armed = entitlements.status(0, db_paths=[])
    assert armed["armed"] is True
    assert armed["free_project_limit"] == entitlements.FREE_PROJECT_LIMIT
    assert armed["cap_version"] == entitlements.CAP_VERSION


# ── through the registry ──────────────────────────────────────────────────────

def _fresh_project(tmp_path, name, version=ARMED):
    root = tmp_path / name
    _library(root / ".arkiv" / "project.db", version)
    return root


def test_registry_refuses_the_fourth_project_when_armed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    monkeypatch.setattr(config, "VERSION", ARMED)
    projects = importlib.import_module("projects")

    for index in range(entitlements.FREE_PROJECT_LIMIT):
        name = "p{0}".format(index)
        projects.add_project(name, str(_fresh_project(tmp_path, name)))

    with pytest.raises(projects.ProjectEntitlementError) as excinfo:
        projects.add_project("p3", str(_fresh_project(tmp_path, "p3")))
    assert excinfo.value.code == "project_limit"
    # Still a RegistryError, so every existing handler keeps working.
    assert isinstance(excinfo.value, projects.RegistryError)


def test_re_registering_an_existing_name_at_the_cap_is_not_refused(tmp_path, monkeypatch):
    """Renaming/relocating a project you already own is not "one more project".

    Gating the replace path too would leave a capped user unable to fix a moved
    library — a restriction the terms never claimed and the user cannot escape.
    """
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    monkeypatch.setattr(config, "VERSION", ARMED)
    projects = importlib.import_module("projects")

    for index in range(entitlements.FREE_PROJECT_LIMIT):
        name = "p{0}".format(index)
        projects.add_project(name, str(_fresh_project(tmp_path, name)))

    moved = _fresh_project(tmp_path, "p0-moved")
    replaced = projects.add_project("p0", str(moved))
    assert replaced.path == moved
    assert len(projects.list_registry_projects()) == entitlements.FREE_PROJECT_LIMIT


def test_an_old_library_lifts_the_registry_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    monkeypatch.setattr(config, "VERSION", ARMED)
    projects = importlib.import_module("projects")

    projects.add_project("legacy", str(_fresh_project(tmp_path, "legacy", PRE_CAP)))
    for index in range(entitlements.FREE_PROJECT_LIMIT + 2):
        name = "n{0}".format(index)
        projects.add_project(name, str(_fresh_project(tmp_path, name)))

    assert len(projects.list_registry_projects()) == entitlements.FREE_PROJECT_LIMIT + 3


# ── through the bins store ────────────────────────────────────────────────────

def test_widening_a_bin_to_a_second_project_needs_entitlement(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "bins.json"))
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    monkeypatch.setattr(config, "VERSION", ARMED)
    bins = importlib.import_module("bins")

    created = bins.create_bin("cut-01")
    bins.add_items(created.id, [{"project_name": "alpha", "media_id": "1"}])

    # Same project again: still a single-project bin, still free.
    bins.add_items(created.id, [{"project_name": "alpha", "media_id": "2"}])

    with pytest.raises(bins.BinEntitlementError) as excinfo:
        bins.add_items(created.id, [{"project_name": "beta", "media_id": "9"}])
    assert excinfo.value.code == "cross_project"
    assert isinstance(excinfo.value, bins.BinsError)

    # The refusal must not have half-applied: a partially widened bin would be
    # the worst outcome, since the user was told it failed.
    after = bins.get_bin(created.id)
    assert {item.project_name for item in after.items} == {"alpha"}


def test_a_bin_that_already_spans_projects_stays_usable(tmp_path, monkeypatch):
    """Existing cross-project data is not retroactively frozen.

    A bin built while the install was entitled (or before the cap existed) must
    keep accepting items from the projects it already holds — revoking that
    would break data the user already has, which is exactly what the
    grandfather promise exists to prevent.
    """
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "bins.json"))
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    bins = importlib.import_module("bins")

    # Built while unarmed (pre-cap build), spanning two projects.
    monkeypatch.setattr(config, "VERSION", PRE_CAP)
    created = bins.create_bin("legacy-cut")
    bins.add_items(
        created.id,
        [
            {"project_name": "alpha", "media_id": "1"},
            {"project_name": "beta", "media_id": "2"},
        ],
    )

    # Now armed and unentitled: adding within the existing project set is fine.
    monkeypatch.setattr(config, "VERSION", ARMED)
    bins.add_items(created.id, [{"project_name": "beta", "media_id": "3"}])
    after = bins.get_bin(created.id)
    assert len(after.items) == 3

    # ...but widening it to a THIRD project is still a new aggregation.
    with pytest.raises(bins.BinEntitlementError):
        bins.add_items(created.id, [{"project_name": "gamma", "media_id": "4"}])
