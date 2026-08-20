"""Free-tier limits, grandfathering, and Pro add-on entitlement.

Single choke-point for every "is this allowed on the free tier?" question. The
published terms (`docs/pro-addon-license.md`, and the product page) promise
exactly two things beyond the free core:

    Projects                       free: up to 3      Pro: unlimited
    Cross-project aggregation      free: —            Pro: yes
    (search and collections spanning projects)

and one guarantee about the transition:

    "The free allowance will apply to NEW INSTALLATIONS from a future release
     onward. Libraries already in use before that release keep unlimited
     projects permanently."

That promise is why this module exists as its own leaf rather than as three
`if` statements at the call sites: the grandfathering rule has to be answered
identically everywhere, and it is the kind of rule that silently rots when
copy-pasted (see `mediatypes.py` for the same lesson learned the hard way).

## This is a goodwill promise, not DRM

`db.py`'s `first_seen_version` comment already states the posture and this
module keeps it: the anchor row is trivially editable, the licence file is
unsigned, and none of it is obfuscated. The licence is enforced by its terms,
not by the database. What the code owes the user is that the *honest* cases
come out right — not that the dishonest ones are impossible.

The practical consequence is a hard rule followed throughout this module:

    **Every uncertainty resolves to "allowed".**

A missing anchor, an unreadable DB, a corrupt registry, a malformed version
string — all of them mean "we cannot prove this install is new", and the only
acceptable way to be wrong here is in the user's favour. Failing the other way
would revoke the exemption from exactly the users who have held it longest,
which is the one outcome the published promise forbids.

## Why the cap version is a constant, not `config.VERSION`

`CAP_VERSION` is the release in which the cap first takes effect, and it is the
permanent grandfathering boundary. It must stay pinned to that release forever
— reading it from `config.VERSION` would move the boundary on every subsequent
release and quietly revoke every exemption granted so far.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import List

import config


# ── the published tier ────────────────────────────────────────────────────────

# `docs/pro-addon-license.md`: "Projects — Free core: Up to 3".
FREE_PROJECT_LIMIT = 3

# The release in which the cap first bites. Libraries first seen under any
# EARLIER version — or under no recorded version at all — are exempt forever.
# Changing this value after 1.1.0 ships would retroactively revoke exemptions;
# it is a historical fact from that point on, not a tunable.
CAP_VERSION = "1.1.0"


class Verdict(object):
    """Outcome of an entitlement check.

    Carries `reason` even when allowed, because the call sites need to explain
    themselves either way: a refusal has to say what is missing (PR #315 — a
    control that just goes grey is a control that lies about why), and an
    allow-by-grandfather is worth surfacing so a user who expects the cap is not
    left wondering whether it silently failed.
    """

    __slots__ = ("allowed", "code", "reason")

    def __init__(self, allowed, code, reason):
        self.allowed = bool(allowed)
        self.code = code
        self.reason = reason

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Verdict(allowed={0!r}, code={1!r}, reason={2!r})".format(
            self.allowed, self.code, self.reason
        )


# ── version comparison ────────────────────────────────────────────────────────

def parse_version(text):
    """`"1.10.2"` -> `(1, 10, 2)`, or None when it is not a usable version.

    Tuple comparison, never string comparison: `"1.10.0" < "1.9.0"` is True
    lexically and false in fact, and that single mistake would hand the cap to
    every user on a double-digit minor release.

    Trailing pre-release/build suffixes (`"1.1.0-rc1"`, `"1.1.0+win"`) are
    reduced to their numeric core rather than rejected — a build that stamped a
    suffixed version is still evidence of when the library was in use, and
    rejecting it would push a real, dated library into the "unknown" bucket.
    """
    if text is None:
        return None
    head = str(text).strip()
    if not head:
        return None
    for separator in ("-", "+", " "):
        head = head.split(separator, 1)[0]
    parts = head.split(".")
    numbers = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    if not numbers:
        return None
    # Pad so (1, 1) and (1, 1, 0) compare equal rather than short-tuple-less-than.
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])


def cap_is_active():
    """Is the cap in force in THIS build at all?

    False on every build older than `CAP_VERSION`. The terms say the allowance
    applies "from a future release onward", so a build that predates that
    release must not enforce it — no matter what the registry happens to look
    like.

    Without this, the gate bites early in a case that is easy to miss: an
    install that registered several project roots but has never ingested into
    them has no `project.db` files, so there is no anchor anywhere, so nothing
    reads as grandfathered — and a 1.0.0 build would refuse the fourth project
    even though 1.0.0 promised no limit at all. The grandfather machinery
    answers "is this install old?"; only this function answers "is the rule even
    in effect yet?", and conflating the two enforces a rule before its own
    start date.

    It also means merging the gate does not arm it. Arming happens in the
    release that sets `config.VERSION` to `CAP_VERSION`, which is deliberate:
    the boundary is a published date-like promise, not a merge time.
    """
    return not predates_cap(config.VERSION)


def predates_cap(version_text):
    """True when a library stamped `version_text` is grandfathered.

    None / unparseable / absent all return True. See the module docstring: an
    unreadable stamp is not evidence that the library is new, and the published
    promise makes "we cannot tell" mean "exempt".
    """
    parsed = parse_version(version_text)
    if parsed is None:
        return True
    cap = parse_version(CAP_VERSION)
    if cap is None:  # pragma: no cover - CAP_VERSION is a literal we control
        return True
    return parsed < cap


# ── reading the anchor out of an arbitrary project DB ─────────────────────────

def read_library_origin(db_path):
    """`first_seen_version` from a project DB, or None when there is no answer.

    Opened read-only via a `mode=ro` URI, mirroring `projects._media_row_count`:
    probing somebody's library for a licensing question must never create a stub
    file nor leave -wal/-shm side files next to their corpus. A licensing check
    that mutates the thing it inspects is not acceptable at any severity.

    `db.get_library_origin()` answers the same question for the CURRENT install
    only (it goes through `config.DB_PATH`). The cap is a machine-level question
    over every registered project, so it needs the path-taking form as well.
    """
    try:
        path = Path(db_path)
        if not path.exists():
            return None
        uri = path.resolve(strict=False).as_uri() + "?mode=ro"
    except (OSError, ValueError):
        return None
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        row = conn.execute(
            "SELECT value FROM library_meta WHERE key='first_seen_version'"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    if not row:
        return None
    return row[0]


# ── Pro entitlement ───────────────────────────────────────────────────────────

def _license_file_path():
    return Path(
        os.getenv("ARKIV_PRO_LICENSE", str(Path.home() / ".arkiv" / "pro-license.json"))
    ).expanduser()


def _pro_from_license_file():
    """True when a readable licence record names a licensee and a key.

    Deliberately unsigned and unverified. The add-on is sold as a named,
    perpetual licence recorded in the public licensee registry; the file exists
    so an install can tell the user (and this code) which licence it is running
    under, not to make forgery hard. Forgery is already trivial by editing this
    module, and pretending otherwise would only cost honest users a support
    ticket when their licence file fails to parse on a plane.
    """
    path = _license_file_path()
    try:
        if not path.exists():
            return False
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        # Unreadable or corrupt: not proof of entitlement, but also not an
        # error worth raising into an unrelated user action.
        return False
    if not isinstance(data, dict):
        return False
    return bool(str(data.get("licensee", "")).strip()) and bool(
        str(data.get("key", "")).strip()
    )


def _pro_from_addon_module():
    """True when the closed-source add-on is installed and reports a licence.

    The add-on is a separate distribution and is NOT in this repo, so this is an
    interface definition as much as a check: if `arkiv_pro` is importable, core
    asks it via `has_valid_license()` when that hook exists, and otherwise
    treats its mere presence as the answer. Presence-as-answer is the lenient
    branch on purpose — a user who paid and installed the component should not
    be gated by core's expectations about the component's internals.
    """
    try:
        import arkiv_pro  # type: ignore
    except Exception:
        # ImportError in the normal case; anything else means a broken add-on
        # install, which must not take an unrelated user action down with it.
        return False
    hook = getattr(arkiv_pro, "has_valid_license", None)
    if hook is None:
        return True
    try:
        return bool(hook())
    except Exception:
        return False


def has_pro():
    """True when this install is entitled to the Pro features.

    Two independent routes, either one sufficient (Hevin 2026-08-19): the add-on
    module being importable, or a licence file being present. Two routes because
    each covers the other's failure: a user who installed the component but
    never placed a licence file still works, and a user whose component is not
    on this machine's import path (a packaged .app, a different venv) can still
    be recognised.
    """
    return _pro_from_addon_module() or _pro_from_license_file()


# ── the machine-level grandfather question ────────────────────────────────────

def install_is_grandfathered(db_paths):
    """True when this install was already in use before `CAP_VERSION`.

    The unit is the INSTALL, not the individual library, because that is what
    the published terms promise: "the free allowance will apply to NEW
    INSTALLATIONS from a future release onward". An install holding any library
    that predates the cap is by definition not a new installation.

    An install with no libraries at all has no evidence of prior use and is
    therefore NOT grandfathered — that is the genuinely fresh install the cap is
    aimed at. This is the one place where absence of evidence is read as
    evidence of absence, and it is safe because it costs such a user nothing:
    they have zero projects, so the cap of three cannot bite them today, and by
    the time it could they will have been stamped with the current version.
    """
    for db_path in db_paths or []:
        origin = read_library_origin(db_path)
        if origin is None:
            # Either the DB predates the anchor entirely, or it is unreadable.
            # Both mean "cannot prove this is new" — but only count it when the
            # library actually exists, so that a registry entry pointing at a
            # deleted/unmounted project does not manufacture an exemption out of
            # nothing. (An unmounted NAS project is exactly the case that would
            # otherwise flip an install to exempt on a bad-mount day and back
            # again the next.)
            try:
                if Path(db_path).exists():
                    return True
            except OSError:
                continue
            continue
        if predates_cap(origin):
            return True
    return False


# ── the three published gates ─────────────────────────────────────────────────

def check_add_project(existing_count, db_paths=None, pro=None, grandfathered=None):
    """May this install register one more project?

    `existing_count` is the number already registered. `pro` / `grandfathered`
    are injectable so call sites that already computed them (and every test) do
    not pay for repeated DB probes — but both default to being worked out here,
    so a call site can never accidentally skip the check by omitting an argument.
    """
    if not cap_is_active():
        return Verdict(
            True,
            "cap_inactive",
            "This build predates the {0} free-tier allowance; projects are "
            "unlimited.".format(CAP_VERSION),
        )
    if pro is None:
        pro = has_pro()
    if pro:
        return Verdict(True, "pro", "Pro add-on licensed — projects are unlimited.")
    if grandfathered is None:
        grandfathered = install_is_grandfathered(db_paths or [])
    if grandfathered:
        return Verdict(
            True,
            "grandfathered",
            "This installation was in use before {0}, so its project limit is "
            "permanently lifted.".format(CAP_VERSION),
        )
    if existing_count < FREE_PROJECT_LIMIT:
        return Verdict(
            True,
            "within_free_tier",
            "{0} of {1} free projects used.".format(existing_count, FREE_PROJECT_LIMIT),
        )
    return Verdict(
        False,
        "project_limit",
        "The free tier allows {0} projects and this installation already has "
        "{1}. Remove a project, or license the Pro add-on for unlimited "
        "projects.".format(FREE_PROJECT_LIMIT, existing_count),
    )


def check_cross_project(db_paths=None, pro=None, grandfathered=None):
    """May this install aggregate across projects (search / collections)?

    Same exemption rules as the project cap. The published zh product page says
    of cross-project search: "新安裝不含" — NEW installs do not include it —
    which grants existing installs the same permanent keep that the projects row
    grants. The English terms used to spell the keep out for projects only;
    treating cross-project the same way is the reading that keeps both promises
    rather than the one that breaks the zh page, and
    `docs/pro-addon-license.md` was tightened in the same change to say so in
    both languages rather than leave the code and the terms disagreeing.
    """
    if not cap_is_active():
        return Verdict(
            True,
            "cap_inactive",
            "This build predates the {0} free-tier allowance; cross-project "
            "aggregation is unrestricted.".format(CAP_VERSION),
        )
    if pro is None:
        pro = has_pro()
    if pro:
        return Verdict(True, "pro", "Pro add-on licensed — cross-project aggregation is available.")
    if grandfathered is None:
        grandfathered = install_is_grandfathered(db_paths or [])
    if grandfathered:
        return Verdict(
            True,
            "grandfathered",
            "This installation was in use before {0}, so cross-project "
            "aggregation stays available.".format(CAP_VERSION),
        )
    return Verdict(
        False,
        "cross_project",
        "Cross-project search and collections are part of the Pro add-on. Each "
        "project can still be searched on its own.",
    )


def status(existing_count, db_paths=None):
    """Everything the UI needs to describe the current tier, in one probe.

    One function so the frontend never has to assemble the tier from three
    separate calls that could disagree mid-flight.
    """
    pro = has_pro()
    grandfathered = install_is_grandfathered(db_paths or []) if not pro else False
    add = check_add_project(existing_count, pro=pro, grandfathered=grandfathered)
    cross = check_cross_project(pro=pro, grandfathered=grandfathered)
    return {
        # Reported explicitly so an inert gate is visible rather than silent. A
        # build that carries the code but predates CAP_VERSION allows
        # everything, and "allowed" alone cannot be told apart from "entitled" —
        # which is precisely how a shipped-but-dead gate goes unnoticed.
        "armed": cap_is_active(),
        "pro": pro,
        "grandfathered": grandfathered,
        "cap_version": CAP_VERSION,
        "free_project_limit": FREE_PROJECT_LIMIT,
        "projects_used": existing_count,
        "can_add_project": add.allowed,
        "add_project_reason": add.reason,
        "cross_project": cross.allowed,
        "cross_project_reason": cross.reason,
    }
