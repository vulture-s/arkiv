"""The product version is written in five files. They must all agree.

Two prior fixes tried to stop this drifting and both undershot. #244 corrected a
tag history that had run 0.2.0 -> 0.10.0 while `Cargo.lock`'s root entry stayed
behind; #311 corrected three files at release time and named the `Cargo.lock`
root entry as the one that had caused the earlier drift. Neither covered
`config.py` or `frontend/src/lib/version.js`, so by v0.12.1 those two still read
`0.10.0` — two minor releases behind, and both of them user-visible: the SPA
renders `version.js` in the UI, and `config.py:VERSION` is what `GET /api/version`
and `GET /api/health` report to anyone diagnosing a build.

A release checklist that has now failed twice is not a checklist problem. This
pins the invariant instead: every location that states the version states the
same one.

Adding a sixth location is fine — add it to VERSION_SOURCES so it is covered
from the start rather than discovered two releases later.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SEMVER = r"\d+\.\d+\.\d+"


def _tauri_conf() -> str:
    data = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    return data["version"]


def _cargo_toml() -> str:
    text = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    m = re.search(rf'^version\s*=\s*"({SEMVER})"', text, re.M)
    assert m, "src-tauri/Cargo.toml has no top-level version"
    return m.group(1)


def _cargo_lock_root() -> str:
    """The root package entry — the exact field #311 identified as the drift source."""
    text = (ROOT / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
    m = re.search(rf'^name = "arkiv"\nversion = "({SEMVER})"', text, re.M)
    assert m, "src-tauri/Cargo.lock has no root `arkiv` package entry"
    return m.group(1)


def _config_py() -> str:
    text = (ROOT / "config.py").read_text(encoding="utf-8")
    m = re.search(rf'^VERSION\s*=\s*"({SEMVER})"', text, re.M)
    assert m, "config.py has no VERSION"
    return m.group(1)


def _frontend_version_js() -> str:
    text = (ROOT / "frontend" / "src" / "lib" / "version.js").read_text(encoding="utf-8")
    m = re.search(rf"^export const VERSION\s*=\s*'v({SEMVER})'", text, re.M)
    assert m, "frontend/src/lib/version.js has no exported VERSION"
    return m.group(1)


VERSION_SOURCES = {
    "src-tauri/tauri.conf.json": _tauri_conf,
    "src-tauri/Cargo.toml": _cargo_toml,
    "src-tauri/Cargo.lock (root arkiv entry)": _cargo_lock_root,
    "config.py:VERSION": _config_py,
    "frontend/src/lib/version.js": _frontend_version_js,
}


def test_every_version_location_agrees():
    found = {label: read() for label, read in VERSION_SOURCES.items()}
    distinct = set(found.values())
    assert len(distinct) == 1, (
        "product version disagrees across files:\n"
        + "\n".join(f"  {label:<42} {value}" for label, value in found.items())
        + "\n\nBump every entry in VERSION_SOURCES together. The two that drift are "
          "config.py and frontend/src/lib/version.js, because release tooling only "
          "touches the three under src-tauri/."
    )


@pytest.mark.parametrize("label", sorted(VERSION_SOURCES))
def test_each_location_is_parseable(label):
    """A version that cannot be read is drift waiting to happen.

    Without this, renaming the constant or reformatting the file would make the
    agreement test above skip that location silently rather than fail.
    """
    value = VERSION_SOURCES[label]()
    assert re.fullmatch(SEMVER, value), f"{label} produced an unparseable version: {value!r}"
