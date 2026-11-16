"""scripts/check_large_files.py — build output / oversized blobs must not enter history.

Each case builds a throwaway git repo, so the guard is exercised against real
`git rev-list --objects` output rather than a mocked file list.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_large_files.py"

_spec = importlib.util.spec_from_file_location("check_large_files", SCRIPT)
clf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clf)

LIMIT = 1024  # small limit keeps the fixtures tiny; logic is size-agnostic


def _git(repo, *args):
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
    )
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.decode()


def _commit(repo, files, msg="c", remove=()):
    for rel, data in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        _git(repo, "add", "-f", rel)
    for rel in remove:
        _git(repo, "rm", "-q", rel)
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, {"README.md": b"hi\n"}, "base")
    _git(tmp_path, "branch", "base")
    return tmp_path


def _run(repo, *extra):
    return clf.main(["--repo", str(repo), "--max-bytes", str(LIMIT), *extra])


def test_clean_change_passes(repo):
    _commit(repo, {"app.py": b"x = 1\n"})
    assert _run(repo, "--base", "base") == 0


def test_oversized_blob_fails(repo, capsys):
    _commit(repo, {"assets/big.bin": b"\0" * (LIMIT + 1)})
    assert _run(repo, "--base", "base") == 1
    assert "assets/big.bin" in capsys.readouterr().out


def test_blob_at_limit_passes(repo):
    _commit(repo, {"assets/edge.bin": b"\0" * LIMIT})
    assert _run(repo, "--base", "base") == 0


def test_tauri_target_fails_even_when_small(repo, capsys):
    _commit(repo, {"src-tauri/target/debug/arkiv": b"elf"})
    assert _run(repo, "--base", "base") == 1
    assert "src-tauri/target/" in capsys.readouterr().out


@pytest.mark.parametrize("path", [
    "frontend/node_modules/pkg/index.js",
    "site/node_modules/pkg/index.js",
    "frontend/dist/index.html",
    "site/dist/index.html",
    "src-tauri/backend/site-packages/x.py",
    "routers/__pycache__/a.cpython-312.pyc",
    "release/arkiv_1.9.0_aarch64.dmg",
    "release/arkiv_1.9.0_x64-setup.exe",
])
def test_build_output_paths_fail(repo, path):
    _commit(repo, {path: b"x"})
    assert _run(repo, "--base", "base") == 1


def test_big_file_added_then_removed_in_same_range_still_fails(repo, capsys):
    # Merge commits are allowed on this repo, so intermediate commits reach main.
    _commit(repo, {"oops.bin": b"\0" * (LIMIT * 4)}, "add")
    _commit(repo, {}, "remove", remove=["oops.bin"])
    assert _run(repo, "--base", "base") == 1
    assert "oops.bin" in capsys.readouterr().out


def test_preexisting_big_blob_on_base_is_not_reblamed(repo):
    _commit(repo, {"legacy.bin": b"\0" * (LIMIT * 4)}, "legacy")
    _git(repo, "branch", "-f", "base")
    _commit(repo, {"app.py": b"x = 1\n"})
    assert _run(repo, "--base", "base") == 0


def test_lookalike_paths_are_not_build_output():
    # Unanchored segment match must not swallow names that merely contain the word.
    for path in ("docs/build-notes.md", "site/src/pages/dist.astro",
                 "scripts/build_sample_prebuilt.py", "src-tauri/gen/schemas/x.json",
                 "src-tauri/backend/.gitkeep"):
        assert clf.build_output_reason(path) is None, path


def test_current_tree_is_clean():
    """The real repo's tracked tree passes at the default limit (baseline)."""
    assert clf.main(["--repo", str(ROOT), "--all"]) == 0
