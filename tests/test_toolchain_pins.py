"""Every toolchain version has ONE source, and every consumer reads it.

    Python    .python-version       ← CI/release setup-python, Dockerfile base
    Node      .nvmrc                ← CI/release/site setup-node, Dockerfile base
    Rust      rust-toolchain.toml   ← CI/release dtolnay `toolchain:` inputs
    packages  constraints.txt       ← every `pip install -r requirements…`

Before this, CI tested Python on a hand-copied dependency list while installers
used requirements.txt, Docker built the UI on Node 25 and CI on Node 20, and Rust
floated on `stable`. The copied list is how requirements.txt went uninstallable
on 3.9 without CI noticing: the 3.9 leg never read it.
"""
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

import importlib.util

_spec = importlib.util.spec_from_file_location("arkiv_doctor", ROOT / "scripts" / "doctor.py")
doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doctor)


def _steps():
    for wf in WORKFLOWS:
        data = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job_name, job in (data.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                yield wf.name, job_name, job, step


def _uses(step, action):
    return (step.get("uses") or "").split("@")[0] == action


# ── Python ────────────────────────────────────────────────────────────────────

def test_setup_python_reads_python_version_file_or_the_matrix():
    seen = 0
    for wf, job_name, job, step in _steps():
        if not _uses(step, "actions/setup-python"):
            continue
        seen += 1
        w = step.get("with") or {}
        if "python-version" in w:
            assert w["python-version"] == "${{ matrix.python-version }}", (
                "{0}:{1} hard-codes python-version {2!r} — use "
                "python-version-file: .python-version".format(wf, job_name, w["python-version"]))
        else:
            assert w.get("python-version-file") == ".python-version", (wf, job_name, w)
    assert seen, "no setup-python steps found — did the workflows move?"


def test_python_matrix_covers_the_pin_and_the_floor():
    pinned = doctor.pinned_python()
    for wf, job_name, job, step in _steps():
        w = step.get("with") or {}
        if _uses(step, "actions/setup-python") and w.get("python-version") == "${{ matrix.python-version }}":
            matrix = [str(v) for v in job["strategy"]["matrix"]["python-version"]]
            assert pinned in matrix and doctor.FLOOR_PYTHON in matrix, (wf, job_name, matrix)


def test_dockerfile_python_base_matches_pin():
    m = re.search(r"^FROM python:(\d+\.\d+)-slim@sha256:", (ROOT / "Dockerfile").read_text(), re.M)
    assert m and m.group(1) == doctor.pinned_python()


# ── Node ──────────────────────────────────────────────────────────────────────

def test_setup_node_reads_nvmrc():
    seen = 0
    for wf, job_name, job, step in _steps():
        if _uses(step, "actions/setup-node"):
            seen += 1
            w = step.get("with") or {}
            assert "node-version" not in w and w.get("node-version-file") == ".nvmrc", (wf, job_name, w)
    assert seen


def test_dockerfile_node_base_matches_pin():
    m = re.search(r"^FROM node:(\d+)-slim@sha256:", (ROOT / "Dockerfile").read_text(), re.M)
    assert m and m.group(1) == doctor.pinned_node_major()


# ── Rust ──────────────────────────────────────────────────────────────────────

def test_rust_toolchain_inputs_match_rust_toolchain_toml():
    pinned = doctor.pinned_rust()
    seen = 0
    for wf, job_name, job, step in _steps():
        uses = step.get("uses") or ""
        if not uses.startswith("dtolnay/rust-toolchain"):
            continue
        seen += 1
        # @stable / @nightly select the toolchain by ref and ignore the file.
        assert uses == "dtolnay/rust-toolchain@master", (wf, job_name, uses)
        assert str((step.get("with") or {}).get("toolchain")) == pinned, (wf, job_name, step)
    assert seen


# ── packages ──────────────────────────────────────────────────────────────────

_PIP_REQ = re.compile(r"pip(?:\.exe)?\s+install\b[^\n]*?-r\s+requirements[\w-]*\.txt[^\n]*")


# Prose that quotes the command in backticks (`pip install -r requirements.txt`
# resolves …) is describing the unconstrained case, not running it.
_QUOTED = re.compile(r"requirements[\w-]*\.txt`")


def _install_lines():
    files = WORKFLOWS + [ROOT / "Dockerfile", ROOT / "src-tauri" / "assemble-backend-win.ps1"]
    for f in files:
        for m in _PIP_REQ.finditer(f.read_text(encoding="utf-8")):
            if not _QUOTED.search(m.group(0)):
                yield f.name, m.group(0)


def test_every_requirements_install_is_constrained():
    lines = list(_install_lines())
    assert len(lines) >= 6, lines  # 3 CI test legs + 2 release legs + Docker (+ ps1 docs)
    loose = [(f, l) for f, l in lines if "-c constraints.txt" not in l]
    assert not loose, "pip install -r without -c constraints.txt: {0}".format(loose)


def test_the_detector_catches_an_unconstrained_install():
    assert _PIP_REQ.search("          pip install -r requirements-dev.txt\n")
    assert not _PIP_REQ.search("pip-audit -r requirements.txt")
    # a backtick-quoted mention in a comment is prose, not an install
    assert _QUOTED.search("card `pip install -r requirements.txt` resolves torch")


def test_lock_script_and_constraints_header_agree():
    """constraints.txt's header records the command that produced it; if the lock
    script changes, the file must be regenerated with the new command."""
    script = (ROOT / "scripts" / "lock.sh").read_text()
    cmd = re.search(r"exec (uv pip compile[^\n]*\\\n[^\n]*)", script).group(1)
    cmd = re.sub(r"\s*\\\n\s*", " ", cmd).replace(' -q "$@"', "").strip()
    header = (ROOT / "constraints.txt").read_text().splitlines()[1].lstrip("# ").strip()
    assert header == cmd, (header, cmd)


def _requirement_names(path):
    names = set()
    for raw in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.add(re.split(r"[<>=!~;\s\[]", line, 1)[0].lower().replace("_", "-"))
    return names


def test_every_direct_requirement_is_pinned():
    pinned = {n.lower().replace("_", "-") for n, _, _ in
              doctor.parse_constraints((ROOT / "constraints.txt").read_text(encoding="utf-8"))}
    for f in ("requirements.txt", "requirements-dev.txt", "requirements-ci.txt"):
        missing = _requirement_names(f) - pinned
        assert not missing, "{0}: not in constraints.txt — run scripts/lock.sh: {1}".format(f, missing)


# ── doctor ────────────────────────────────────────────────────────────────────

def test_doctor_parses_constraint_lines_with_markers():
    text = ("# header\nnumpy==2.0.2 ; python_full_version < '3.10'\n"
            "numpy==2.5.4 ; python_full_version >= '3.12'\n    # via x\nfastapi==0.143.3\n")
    assert doctor.parse_constraints(text) == [
        ("numpy", "2.0.2", "python_full_version < '3.10'"),
        ("numpy", "2.5.4", "python_full_version >= '3.12'"),
        ("fastapi", "0.143.3", ""),
    ]


def test_doctor_reads_the_pins():
    assert re.fullmatch(r"3\.\d+", doctor.pinned_python())
    assert doctor.pinned_node_major().isdigit()
    assert re.fullmatch(r"\d+\.\d+\.\d+", doctor.pinned_rust())
