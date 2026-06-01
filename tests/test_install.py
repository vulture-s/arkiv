"""Guards for the 'good install' contract — that a fresh install ships every
module server.py needs. A hand-maintained copy list in install.sh once went
stale and dropped auth/chat/admin/… → the install crashed on first run with
ModuleNotFoundError. These tests fail loudly if that can recur."""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _first_party_imports(py_file: Path) -> set:
    """Top-level modules imported by a file that resolve to a repo-root *.py."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if (REPO_ROOT / f"{n}.py").is_file()}


def test_install_copies_python_modules_via_glob_not_a_stale_list():
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    # the local-copy path must glob *.py so it can't drift behind new modules
    assert 'cp "$SRC"/*.py' in install, "install.sh must copy all *.py via glob"


def test_every_first_party_module_server_imports_exists():
    """If server.py imports a local module, the file must exist (catches a
    rename/delete that a glob-copy would silently miss)."""
    missing = [m for m in _first_party_imports(REPO_ROOT / "server.py")
               if not (REPO_ROOT / f"{m}.py").is_file()]
    assert not missing, f"server.py imports modules with no file: {missing}"


def test_first_party_import_closure_is_self_contained():
    """Transitively: every repo-root module reachable from server.py only imports
    other repo-root modules that also exist — no dangling local import anywhere in
    the dependency closure that a fresh install would ship."""
    seen, stack = set(), ["server"]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        f = REPO_ROOT / f"{mod}.py"
        if not f.is_file():
            continue
        for dep in _first_party_imports(f):
            assert (REPO_ROOT / f"{dep}.py").is_file(), f"{mod} imports missing {dep}"
            stack.append(dep)
