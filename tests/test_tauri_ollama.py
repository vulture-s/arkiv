"""The packaged app never started Ollama, and could not find ffmpeg.

Two failures with the same shape: both work perfectly from a dev shell and both
are invisible until someone runs the actual `.app`.

* `main.rs` contained the string "ollama" **zero times**. Vision tagging, chat and
  embeddings each soft-fail by design — a clip simply comes back with no
  description — so inside the bundle they all degraded silently.
* A Finder-launched app inherits a minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`),
  not the shell's. Homebrew is not on it, so `faster-whisper` shelling out to a
  bare `ffmpeg` dies with `[Errno 2]` and takes the whole batch with it.

These are source assertions, not behaviour tests — the Rust shell has no test
harness and the failure only reproduces inside a real bundle. They exist so the
next person to touch `main.rs` cannot delete the fix without noticing. `cargo
check` on both macOS and Windows runners is what proves it compiles.
"""
from __future__ import annotations

import re
from pathlib import Path

_MAIN_RS = Path(__file__).resolve().parent.parent / "src-tauri" / "src" / "main.rs"
_CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _src():
    return _MAIN_RS.read_text(encoding="utf-8")


def test_the_app_starts_ollama_when_nothing_is_serving():
    src = _src()
    assert "fn spawn_ollama_if_needed" in src
    assert "spawn_ollama_if_needed(" in src.split("fn spawn_ollama_if_needed", 1)[1], (
        "defined but never called"
    )


def test_it_probes_before_starting_one():
    """Starting a second daemon on a machine that already runs Ollama would fight
    the user's own install for the port."""
    body = _src().split("fn spawn_ollama_if_needed", 1)[1]
    assert "port_open(OLLAMA_PORT)" in body.split("\n}", 1)[0]


def test_only_an_ollama_we_started_is_killed_on_exit():
    """Killing a daemon we did not start would take down whatever else the user
    has pointed at it."""
    src = _src()
    assert "struct Ollama(Mutex<Option<Child>>)" in src
    assert "try_state::<Ollama>()" in src
    # The probe returns None when one is already running, so the state holds
    # nothing and the exit path has nothing to kill.
    body = _src().split("fn spawn_ollama_if_needed", 1)[1].split("\n}", 1)[0]
    assert "return None" in body


def test_the_backend_is_given_an_augmented_path():
    src = _src()
    assert "fn augmented_path" in src
    assert re.search(r'\.env\("PATH", augmented_path\(\)\)', src), (
        "the backend still inherits the Finder PATH"
    )


def test_homebrew_is_on_that_path():
    """The specific directory whose absence broke ffmpeg on every Apple Silicon
    install of the packaged app."""
    assert "/opt/homebrew/bin" in _src()


def test_the_existing_path_still_wins():
    """Prepending would shadow a binary the user deliberately put earlier on their
    own PATH — e.g. a pinned ffmpeg build."""
    body = _src().split("fn augmented_path", 1)[1].split("\n}", 1)[0]
    assert "let mut out = current.clone();" in body


def test_the_windows_only_code_is_actually_compiled_somewhere():
    """main.rs carries `#[cfg(windows)]` blocks that the macOS runner does not
    compile at all. Without a Windows job, a type error there first appears during
    a release build — after the tag is pushed."""
    assert "#[cfg(windows)]" in _src()
    ci = _CI.read_text(encoding="utf-8")
    assert "tauri-check-windows" in ci
    assert "runs-on: windows-latest" in ci
