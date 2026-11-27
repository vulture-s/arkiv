#!/usr/bin/env python3
"""Print the constraints.txt pins that apply to one target platform.

constraints.txt is a *universal* lock: uv forks a pin per marker
(`pillow==12.3.0 ; python_full_version == '3.10.*'`,
`pillow==13.0.0 ; python_full_version >= '3.11'`, `torch==… ; sys_platform == 'darwin'`).
pip-audit evaluates those markers against the interpreter it runs on, so on the
ubuntu CI runner it would silently skip every darwin-only or win32-only pin —
exactly the ones the release legs ship. This script evaluates the markers for a
*named* target instead, so the audit can check what each release actually installs:

    python scripts/pins_for.py darwin > /tmp/pins-darwin.txt
    pip-audit -r /tmp/pins-darwin.txt --no-deps --disable-pip

Targets are the shipped runtime (Python 3.12, see .python-version) on macOS and
Windows. Exit 2 if a target yields no pins (a broken lock must not audit as clean).
"""
import sys
from pathlib import Path

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parent.parent

_PY = "3.12.0"
TARGETS = {
    "darwin": {
        "sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix",
        "platform_machine": "arm64",
    },
    "win32": {
        "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
        "platform_machine": "AMD64",
    },
}


def env_for(target):
    env = {
        "python_version": ".".join(_PY.split(".")[:2]),
        "python_full_version": _PY,
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "platform_release": "", "platform_version": "", "implementation_version": _PY,
        "extra": "",
    }
    env.update(TARGETS[target])
    return env


def pins_for(text, target):
    env = env_for(target)
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        req, _, marker = line.partition(";")
        req = req.strip()
        if "==" not in req:
            continue
        if marker.strip() and not Marker(marker.strip()).evaluate(env):
            continue
        out.append(req)
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in TARGETS:
        print("usage: pins_for.py {%s}" % "|".join(sorted(TARGETS)), file=sys.stderr)
        return 2
    pins = pins_for((ROOT / "constraints.txt").read_text(encoding="utf-8"), argv[0])
    if not pins:
        print("no pins for %s — refusing to emit an empty (clean-looking) set" % argv[0], file=sys.stderr)
        return 2
    print("\n".join(pins))
    return 0


if __name__ == "__main__":
    sys.exit(main())
