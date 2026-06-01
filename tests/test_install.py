"""Guards for the 'good install' contract — that a fresh install ships every
module server.py needs. A hand-maintained copy list in install.sh once went
stale and dropped auth/chat/admin/… → the install crashed on first run with
ModuleNotFoundError. These tests fail loudly if that can recur."""
import ast
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _imported_top_level(py_file: Path) -> set:
    """Top-level module names this file imports (absolute imports only)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
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


def test_install_copies_python_modules_via_glob_not_a_stale_list():
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    # the local-copy path must glob *.py so it can't drift behind new modules
    assert 'cp "$SRC"/*.py' in install, "install.sh must copy all *.py via glob"


def test_no_unresolved_import_in_install_closure():
    """Walk the import closure from server.py over repo-root modules. Every
    imported name must resolve to a repo-root *.py (shipped by the glob copy) OR
    an available package/stdlib. A renamed/deleted first-party module surfaces
    here as unresolved — the exact ModuleNotFoundError a fresh install would hit.

    Runs in-process so it inherits conftest's dependency stubs (unlike a clean
    subprocess, which would false-fail on missing heavy deps)."""
    local = {p.stem for p in REPO_ROOT.glob("*.py")}
    seen, stack, missing = set(), ["server"], []
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        f = REPO_ROOT / f"{mod}.py"
        if not f.is_file():
            continue
        for name in _imported_top_level(f):
            if name in local:
                stack.append(name)  # recurse into first-party modules
            elif not _resolvable(name):
                missing.append((mod, name))
    assert not missing, f"unresolved imports a fresh install would crash on: {missing}"
