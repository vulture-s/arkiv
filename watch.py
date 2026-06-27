#!/usr/bin/env python3
"""arkiv — Folder Watcher (Phase 11.2)

Event-driven auto-ingest. Watches one or more roots (an inbox folder and/or
mounted card volumes) and ingests new media as it lands.

    python watch.py ~/Movies/rushes
    python watch.py /Volumes/SDCARD ~/.arkiv/inbox --debounce 5

Design (borrowed from the EDITH watcher + Phase 11 safety notes):
  * Event source is `watchdog` (FSEvents on macOS) when installed, else a
    polling fallback — both feed the same StabilityTracker, so behaviour is
    identical bar latency. No hard new dependency: a box without watchdog
    (e.g. the NAS) still works by polling.
  * Stability/debounce: a file is ingested only once its (size, mtime) has held
    steady for `debounce_s` — never mid-copy (the old code's "mtime < 5s" guess
    missed slow network copies).
  * Dedup: files already in the DB (by resolved path) are skipped; a file is
    ingested at most once per signature.
  * Unmount-abort: if a watched root disappears (card pulled), the watcher stops
    that root cleanly rather than ingesting a half-copied / vanishing file.

The StabilityTracker + Watcher.tick logic is pure and unit-tested; the watchdog
observer + run loop are the thin I/O shell (verified on-device).
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts",  # video
    ".insv", ".360",  # 360 rigs (Insta360 / GoPro Max)
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg",  # audio
}

Signature = Tuple[int, float]  # (size_bytes, mtime)


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTS


def file_signature(path: Path) -> Optional[Signature]:
    """(size, mtime) or None if the file can't be stat'd (gone / permission)."""
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime)
    except OSError:
        return None


class StabilityTracker:
    """Holds candidate files until their (size, mtime) signature stops changing
    for `debounce_s`, so a file still being copied is never ingested mid-write.

    Pure + clock-injected — the whole point is to unit-test the timing without
    real sleeps or real copies.
    """

    def __init__(self, debounce_s: float = 5.0):
        self.debounce_s = debounce_s
        # path -> (signature, first_seen_at_with_this_signature)
        self._seen: Dict[str, Tuple[Signature, float]] = {}

    def observe(self, path: str, sig: Optional[Signature], now: float) -> bool:
        """Record an observation. Returns True once the file has been stable for
        debounce_s (i.e. it's safe to ingest). A vanished file (sig None) is
        forgotten and never ready."""
        if sig is None:
            self._seen.pop(path, None)
            return False
        prev = self._seen.get(path)
        if prev is None or prev[0] != sig:
            # New file, or it changed since last look → (re)start the timer.
            self._seen[path] = (sig, now)
            first_seen = now
        else:
            first_seen = prev[1]
        # Ready once it has held this signature for debounce_s (so debounce_s=0
        # is ready on first sight).
        return (now - first_seen) >= self.debounce_s

    def forget(self, path: str) -> None:
        self._seen.pop(path, None)

    def pending(self) -> List[str]:
        return list(self._seen.keys())


# ── subprocess dispatch (kept from the polling watcher) ──────────────────────

def run_tree(cmd: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run that kills the whole process tree on timeout.

    audit H8: plain subprocess.run(timeout=) only kills the direct child;
    ffmpeg/whisper grandchildren orphan and (on Windows) hold the pipes open so
    communicate() hangs. POSIX: new session + killpg. Windows: taskkill /T /F.
    """
    kwargs: dict = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        raise subprocess.TimeoutExpired(list(cmd), timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)


def ingest_file(path: Path, timeout: float = 600.0) -> bool:
    """Ingest one file via a subprocess `ingest.py --dir <file>`.

    Subprocess (not in-process) so a crash/timeout in ffmpeg/whisper can't take
    the long-lived watcher down with it. In-process dispatch (microtask B8, to
    avoid model reheat between files) needs the per-file orchestration extracted
    from ingest.main — left as a follow-up; correctness first.
    """
    script = Path(__file__).parent / "ingest.py"
    try:
        result = run_tree([sys.executable, str(script), "--dir", str(path)], timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  ✗ {path.name}: timeout (>{int(timeout)}s)")
        return False
    if result.returncode == 0:
        print(f"  ✓ {path.name}")
        return True
    print(f"  ✗ {path.name}: {(result.stderr or '')[:200]}")
    return False


# ── watcher core (testable) ──────────────────────────────────────────────────

class Watcher:
    """Ties roots → candidate files → stability → dispatch. `tick()` is the pure
    polling step (also reused to drain candidates queued by watchdog events)."""

    def __init__(
        self,
        roots: Sequence[Path],
        debounce_s: float = 5.0,
        dispatch: Callable[[Path], bool] = ingest_file,
        clock: Callable[[], float] = time.time,
    ):
        self.roots = [Path(r) for r in roots]
        self.tracker = StabilityTracker(debounce_s)
        self.dispatch = dispatch
        self.clock = clock
        self.known: Set[str] = set()         # already-ingested resolved paths
        self._candidates: Set[str] = set()    # paths flagged by watchdog events

    def load_known(self) -> None:
        """Seed `known` from the DB so we never re-ingest existing media."""
        try:
            import db
            with db.get_conn() as conn:
                rows = conn.execute("SELECT path FROM media").fetchall()
                self.known = {db.resolve_path(r["path"]) for r in rows}
            print(f"  {len(self.known)} files already in DB")
        except Exception as e:  # noqa: BLE001 — warn loudly (audit L9), don't crash
            print(f"  WARN: could not load known files ({e.__class__.__name__}: {e}); "
                  f"starting empty — existing files may be re-processed")

    def roots_alive(self) -> List[Path]:
        """Roots that still exist. A disappeared root = card unmounted."""
        return [r for r in self.roots if r.exists()]

    def _scan(self, roots: Iterable[Path]) -> List[Path]:
        found: List[Path] = []
        for root in roots:
            try:
                for f in root.rglob("*"):
                    if f.is_file() and is_media(f):
                        found.append(f)
            except OSError:
                continue  # root vanished mid-scan (unmount) — skip it
        return found

    def note_event(self, path: Path) -> None:
        """Record a path flagged by a watchdog event (drained on the next tick)."""
        if is_media(path):
            self._candidates.add(str(path))

    def tick(self, now: Optional[float] = None, candidates: Optional[Iterable[Path]] = None) -> List[Path]:
        """One observation + dispatch step. In polling mode `candidates` is None
        → scan the live roots. In event mode the loop passes the queued paths.
        Returns the files dispatched this tick."""
        if now is None:
            now = self.clock()
        if candidates is None:
            paths = self._scan(self.roots_alive())
        else:
            paths = [Path(p) for p in candidates]

        dispatched: List[Path] = []
        for p in paths:
            key = str(p)
            if key in self.known:
                self.tracker.forget(key)
                continue
            sig = file_signature(p)
            if not self.tracker.observe(key, sig, now):
                continue
            # Stable → ingest. Re-check it still exists (unmount race).
            if not p.exists():
                self.tracker.forget(key)
                continue
            ok = self.dispatch(p)
            self.tracker.forget(key)
            if ok:
                self.known.add(key)
                dispatched.append(p)
        return dispatched

    # --- run loop (I/O shell; verified on-device, not in unit tests) ---

    def run(self, poll_interval: float = 5.0, use_watchdog: bool = True) -> None:
        observer = self._start_watchdog() if use_watchdog else None
        mode = "watchdog (FSEvents)" if observer else "polling"
        print(f"arkiv watcher — {mode} | roots: {', '.join(str(r) for r in self.roots)}")
        try:
            while True:
                alive = self.roots_alive()
                if not alive:
                    print("  all watched roots gone (unmounted) — stopping")
                    break
                # Drain watchdog-queued candidates, then a full safety scan.
                queued = list(self._candidates)
                self._candidates.clear()
                if queued:
                    self.tick(candidates=queued)
                self.tick()
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("\n  stopped")
        finally:
            if observer is not None:
                observer.stop()
                observer.join(timeout=5)

    def _start_watchdog(self):
        """Start a watchdog observer feeding note_event, or None if unavailable."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except Exception:
            return None

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    watcher.note_event(Path(event.src_path))

            def on_moved(self, event):
                if not event.is_directory:
                    watcher.note_event(Path(event.dest_path))

        obs = Observer()
        for root in self.roots_alive():
            obs.schedule(_Handler(), str(root), recursive=True)
        obs.daemon = True
        obs.start()
        return obs


def main():
    parser = argparse.ArgumentParser(description="arkiv folder watcher (Phase 11.2)")
    parser.add_argument("roots", nargs="+", help="One or more directories / mount points to watch")
    parser.add_argument("--debounce", type=float, default=5.0,
                        help="Seconds a file's size+mtime must hold steady before ingest (default: 5)")
    parser.add_argument("--poll-interval", type=float, default=5.0,
                        help="Safety re-scan interval in seconds (default: 5)")
    parser.add_argument("--no-watchdog", action="store_true",
                        help="Force the polling fallback even if watchdog is installed")
    parser.add_argument("--once", action="store_true", help="One tick then exit (no loop)")
    args = parser.parse_args()

    roots = [Path(r).expanduser().resolve() for r in args.roots]
    missing = [r for r in roots if not r.is_dir()]
    if missing:
        print("Error: not a directory: " + ", ".join(str(m) for m in missing))
        sys.exit(1)

    w = Watcher(roots, debounce_s=args.debounce)
    w.load_known()
    if args.once:
        dispatched = w.tick()
        print(f"  dispatched {len(dispatched)} file(s)")
        return
    w.run(poll_interval=args.poll_interval, use_watchdog=not args.no_watchdog)


if __name__ == "__main__":
    main()
