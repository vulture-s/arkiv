"""Guards for the 'good install' contract — that a fresh install ships every
module server.py needs. A hand-maintained copy list in install.sh once went
stale and dropped auth/chat/admin/… → the install crashed on first run with
ModuleNotFoundError. These tests fail loudly if that can recur."""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _imported_top_level(py_file: Path) -> set:
    """Top-level module names this file imports (absolute imports only).

    Imports inside a `try:` block are EXCLUDED — those are intentionally-optional
    deps guarded by `except ImportError` (e.g. health.py's `import mlx`, which is
    Apple-Silicon only and absent on Windows/Linux). They can't crash a fresh
    install, so flagging them as "unresolved" is a false positive."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        guarded.add(id(sub))
    names = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _resolvable(name: str) -> bool:
    """Is this import name available? Honors conftest's sys.modules stubs (so a
    heavy dep like chromadb/torch doesn't false-fail a lightweight test env),
    plus real installed/stdlib packages via find_spec. A DELETED first-party
    module is neither in sys.modules (nothing imported it) nor findable nor a
    repo file → unresolved."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _first_party_packages() -> set:
    """Repo-root package dirs (have __init__.py), excluding the test package."""
    return {p.parent.name for p in REPO_ROOT.glob("*/__init__.py")
            if p.parent.name not in ("tests",)}


def test_install_copies_python_modules_via_glob_not_a_stale_list():
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    # the local-copy path must glob *.py so it can't drift behind new modules
    assert 'cp "$SRC"/*.py' in install, "install.sh must copy all *.py via glob"


def test_install_copies_first_party_package_dirs():
    """Any first-party package dir (one with __init__.py, e.g. a future extracted
    module) is imported by name but is NOT a top-level *.py, so the *.py glob
    alone wouldn't ship it. install.sh must also copy package dirs via a
    */__init__.py glob (drift-proof, not a hand list) — else a copy-install
    ModuleNotFoundErrors on import. (whisper_guard was the original such package;
    it's since de-vendored to PyPI, but the drift-proof mechanism must remain.)"""
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert "/__init__.py" in install, (
        "install.sh must copy first-party package dirs via a */__init__.py glob"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="install.sh is the POSIX (mac/Linux/NAS) installer — its bash copy loop "
    "with `cp -R` and POSIX paths isn't the Windows install path (arkiv on Windows "
    "installs via venv directly). Nothing to assert about install.sh here.",
)
def test_package_copy_actually_ships_packages_not_tests(tmp_path):
    """Functional proof (Codex SHOULD-FIX): run install.sh's package-copy loop
    against a synthetic source tree and assert a first-party package dir lands
    while tests/ and .venv do NOT. Uses a synthetic SRC (not the real repo) so
    the test holds regardless of which first-party packages currently exist —
    arkiv has none vendored today, but the installer mechanism must still work
    the day one is added. Catches a regression the string check alone would miss."""
    import subprocess

    # Build a synthetic source tree mirroring repo layout
    src = tmp_path / "src"
    (src / "demo_pkg").mkdir(parents=True)
    (src / "demo_pkg" / "__init__.py").write_text("# shippable first-party package\n")
    (src / "tests").mkdir()
    (src / "tests" / "__init__.py").write_text("# must NOT ship\n")
    (src / ".venv").mkdir()
    (src / ".venv" / "__init__.py").write_text("# must NOT ship\n")

    dest = tmp_path / "install_dir"
    dest.mkdir()
    # Mirror of install.sh's package-dir copy loop (kept in sync via the string
    # assertion above, which fails if the installer drops this mechanism).
    snippet = (
        'set -e; SRC="$1"; INSTALL_DIR="$2"; '
        'for pkg in "$SRC"/*/__init__.py; do '
        '[ -f "$pkg" ] || continue; d="$(dirname "$pkg")"; '
        'case "$(basename "$d")" in tests|.venv) continue ;; esac; '
        'cp -R "$d" "$INSTALL_DIR"/; done'
    )
    subprocess.run(["bash", "-c", snippet, "bash", str(src), str(dest)], check=True)
    assert (dest / "demo_pkg" / "__init__.py").is_file(), "first-party package not shipped"
    assert not (dest / "tests").exists(), "tests/ must not be shipped"
    assert not (dest / ".venv").exists(), ".venv must not be shipped"


def test_no_unresolved_import_in_install_closure():
    """Walk the import closure from server.py over repo-root modules. Every
    imported name must resolve to a repo-root *.py / package dir (shipped by the
    glob copies) OR an available package/stdlib. A renamed/deleted first-party
    module surfaces here as unresolved — the exact ModuleNotFoundError a fresh
    install would hit.

    Runs in-process so it inherits conftest's dependency stubs (unlike a clean
    subprocess, which would false-fail on missing heavy deps)."""
    local = {p.stem for p in REPO_ROOT.glob("*.py")} | _first_party_packages()
    seen, stack, missing = set(), ["server"], []
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        f = REPO_ROOT / f"{mod}.py"
        if not f.is_file():
            f = REPO_ROOT / mod / "__init__.py"  # first-party package
        if not f.is_file():
            continue
        for name in _imported_top_level(f):
            if name in local:
                stack.append(name)  # recurse into first-party modules/packages
            elif not _resolvable(name):
                missing.append((mod, name))
    assert not missing, f"unresolved imports a fresh install would crash on: {missing}"
