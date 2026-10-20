#!/usr/bin/env python3
"""Check this machine's toolchain against the repo's pins — and optionally fix it.

    python scripts/doctor.py          # report; exit 1 if anything is off
    python scripts/doctor.py --fix    # create/sync .venv with uv, npm ci

What it compares (each pin lives in exactly one file):

    Python    .python-version        (3.9 is also supported: the NAS floor)
    Node      .nvmrc                  (major version)
    Rust      rust-toolchain.toml     (only if rustc is installed — desktop builds)
    uv        present                 (installs and locks; see scripts/lock.sh)
    ffmpeg    present                 (runtime dependency, not pip-installable)
    packages  constraints.txt         (the venv's installed versions)

Stdlib only and 3.9-compatible: it has to run before anything is installed.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
FLOOR_PYTHON = "3.9"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").strip()


def pinned_python() -> str:
    return _read(".python-version")


def pinned_node_major() -> str:
    return _read(".nvmrc").lstrip("v").split(".")[0]


def pinned_rust() -> str:
    m = re.search(r'^channel\s*=\s*"([^"]+)"', _read("rust-toolchain.toml"), re.M)
    if not m:
        raise ValueError("rust-toolchain.toml has no channel")
    return m.group(1)


def _run(cmd: List[str]) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or out.stderr).strip()


def _version_in(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
    return m.group(1) if m else None


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def parse_constraints(text: str) -> List[Tuple[str, str, str]]:
    """[(name, version, marker)] from a uv/pip-compile constraints file."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)\s*(?:;\s*(.*))?$", line.strip())
        if m:
            out.append((m.group(1), m.group(2), (m.group(3) or "").strip()))
    return out


_PROBE = r"""
import json, sys
from importlib import metadata
try:
    from packaging.markers import Marker
except ImportError:
    from pip._vendor.packaging.markers import Marker
pins = json.load(sys.stdin)
bad = []
for name, version, marker in pins:
    if marker and not Marker(marker).evaluate():
        continue
    try:
        have = metadata.version(name)
    except metadata.PackageNotFoundError:
        have = None
    # +local labels (torch 2.14.1+cpu) satisfy ==2.14.1, as pip itself treats them
    if have is None or have.split("+")[0] != version:
        bad.append([name, version, have])
print(json.dumps(bad))
"""


def package_drift(python: Path) -> Optional[List[Tuple[str, str, Optional[str]]]]:
    """Packages in `python`'s environment that differ from constraints.txt.

    Only pins whose marker applies to that interpreter count. A pin installed at
    another version, or not installed at all, is listed; --fix converges both."""
    import json
    pins = parse_constraints(_read("constraints.txt"))
    try:
        out = subprocess.run([str(python), "-c", _PROBE], input=json.dumps(pins),
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [tuple(x) for x in json.loads(out.stdout)]


def check() -> int:
    problems = 0

    def line(ok: Optional[bool], label: str, detail: str) -> None:
        nonlocal problems
        mark = {True: "ok  ", False: "FAIL", None: "skip"}[ok]
        if ok is False:
            problems += 1
        print("  [{0}] {1:<9} {2}".format(mark, label, detail))

    print("arkiv toolchain doctor ({0})".format(ROOT))

    want_py = pinned_python()
    py = _venv_python() if _venv_python().exists() else Path(sys.executable)
    have_py = _version_in(_run([str(py), "--version"]))
    ok = bool(have_py) and (have_py.startswith(want_py + ".") or have_py == want_py
                            or have_py.startswith(FLOOR_PYTHON + "."))
    line(ok, "python", "{0} at {1} (pinned {2}; {3} floor also ok)".format(
        have_py, py, want_py, FLOOR_PYTHON))

    want_node = pinned_node_major()
    have_node = _version_in(_run(["node", "--version"]))
    line(None if have_node is None else have_node.split(".")[0] == want_node, "node",
         "{0} (pinned {1}.x){2}".format(have_node, want_node,
                                        "" if have_node else " — not installed; needed for the UI"))

    want_rust = pinned_rust()
    have_rust = _version_in(_run(["rustc", "--version"]))
    if have_rust is None:
        line(None, "rust", "not installed (only needed for desktop builds; pinned {0})".format(want_rust))
    else:
        # rustup honours rust-toolchain.toml in this tree, so `rustc` here IS the
        # pinned one if rustup is in use; a mismatch means a non-rustup install.
        line(have_rust == want_rust, "rust", "{0} (pinned {1})".format(have_rust, want_rust))

    have_uv = _version_in(_run(["uv", "--version"]))
    line(have_uv is not None, "uv", have_uv or "not installed — brew install uv / pipx install uv")

    have_ff = shutil.which("ffmpeg") and shutil.which("ffprobe")
    line(bool(have_ff), "ffmpeg", "found" if have_ff else "ffmpeg/ffprobe not on PATH")

    if not _venv_python().exists():
        line(False, "packages", "no .venv — run with --fix")
    else:
        drift = package_drift(_venv_python())
        if drift is None:
            line(False, "packages", "could not inspect .venv")
        elif drift:
            shown = ", ".join("{0} {1}≠{2}".format(n, h or "missing", v) for n, v, h in drift[:6])
            more = " (+{0} more)".format(len(drift) - 6) if len(drift) > 6 else ""
            line(False, "packages", "{0} differ from constraints.txt: {1}{2}".format(
                len(drift), shown, more))
        else:
            line(True, "packages", ".venv matches constraints.txt")

    print("{0} problem(s)".format(problems) if problems else "all good")
    return 1 if problems else 0


def fix() -> int:
    if not shutil.which("uv"):
        print("uv is required for --fix: brew install uv  (or pipx install uv)")
        return 1
    want_py = pinned_python()
    steps = []
    if not _venv_python().exists():
        steps.append(["uv", "venv", str(VENV), "--python", want_py])
    steps.append(["uv", "pip", "install", "--python", str(_venv_python()),
                  "-r", "requirements.txt", "-r", "requirements-dev.txt",
                  "-c", "constraints.txt"])
    if shutil.which("npm"):
        steps.append(["npm", "ci", "--prefix", "frontend", "--no-audit", "--no-fund"])
    for cmd in steps:
        print("$ " + " ".join(cmd))
        if subprocess.run(cmd, cwd=str(ROOT)).returncode != 0:
            return 1
    return check()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fix", action="store_true",
                    help="create/sync .venv from constraints.txt with uv, and npm ci the frontend")
    args = ap.parse_args(argv)
    return fix() if args.fix else check()


if __name__ == "__main__":
    sys.exit(main())
