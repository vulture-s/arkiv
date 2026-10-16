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

import mediatypes

# R5-24: single source of truth in mediatypes.py (video + 360 rigs + audio).
MEDIA_EXTS = mediatypes.MEDIA_EXT

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


# ── subprocess dispatch ──────────────────────────────────────────────────────
# run_tree (process-tree-safe subprocess.run) moved to the shared proctree module
# (fable-audit round-5 #2) so the /api/ingest + reingest HTTP routes reuse the same
# tree-kill instead of the plain subprocess.run that orphaned their ffmpeg children.
from proctree import run_tree  # noqa: F401,E402 (re-exported for existing callers)


def ingest_file(path: Path, timeout: float = 600.0) -> bool:
    """Ingest one file via a subprocess `ingest.py --dir <file>`.

    Subprocess (not in-process) so a crash/timeout in ffmpeg/whisper can't take
    the long-lived watcher down with it. Several files that become ready together
    go through `ingest_batch` instead (one subprocess, one model warm-up); this
    single-file path is what a batch falls back to when it has to attribute a
    failure to one file.
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
    # Every non-zero code is "not cleanly in the library" and goes through the
    # same back-off / quarantine path in Watcher.tick — the watcher itself never
    # stops on it. What differs is only what we can say about it: exit 7
    # (ingest.EXIT_RECONCILE_MISMATCH, issue #393) is reported on STDOUT by the
    # `Reconcile:` block with an empty stderr, so printing stderr alone would log
    # a bare "✗ name:" with no reason at all.
    print(f"  ✗ {path.name}: exit {result.returncode}"
          f"{' (reconcile mismatch — not in the library)' if result.returncode == 7 else ''}")
    detail = _failure_detail(result)
    if detail:
        print(f"      {detail}")
    return False


def _path_in_library(path: Path) -> bool:
    import db
    return db.path_is_indexed(str(path))


def ingest_batch(
    paths: Sequence[Path],
    timeout: float = 600.0,
    single: Optional[Callable[[Path], bool]] = None,
    in_library: Optional[Callable[[Path], bool]] = None,
) -> Dict[str, bool]:
    """Ingest several files in ONE subprocess (`ingest.py --files a b c`) so the
    whisper / embedding models are loaded once for the batch instead of once per
    file (roadmap B8). Returns {str(path): ok}.

    Still a subprocess, deliberately: the timeout + process-group kill, the
    memory hand-back at exit and the "a crash cannot take the watcher down"
    guarantee all stay. The budget is `timeout` per file, summed.

    Exit 0 is ingest's promise that every queued file is in the library (#393),
    so it is trusted as-is. Anything else — partial failure, reconcile mismatch,
    a crash, a timeout — is attributed per file: whatever is in the library now
    counts as done, and ONLY the rest is retried one by one through `single`
    (the old per-file path). That retry is the point: a batch that died on one
    poison file must not charge its batch-mates a failure, or a single corrupt
    clip would push healthy ones into back-off / quarantine with it.
    """
    if single is None:
        single = ingest_file
    if in_library is None:
        in_library = _path_in_library
    paths = list(paths)
    if len(paths) == 1:
        return {str(paths[0]): single(paths[0])}
    script = Path(__file__).parent / "ingest.py"
    cmd = [sys.executable, str(script), "--files"] + [str(p) for p in paths]
    budget = timeout * len(paths)
    print(f"  ⇢ batch of {len(paths)} file(s) in one ingest run")
    rc: Optional[int]
    try:
        result = run_tree(cmd, timeout=budget)
        rc = result.returncode
    except subprocess.TimeoutExpired:
        result, rc = None, None
    if rc == 0:
        for p in paths:
            print(f"  ✓ {p.name}")
        return {str(p): True for p in paths}

    why = f"timeout (>{int(budget)}s)" if rc is None else f"exit {rc}"
    out: Dict[str, bool] = {}
    leftover: List[Path] = []
    for p in paths:
        try:
            done = in_library(p)
        except Exception as e:  # noqa: BLE001 — can't tell → retry it alone
            print(f"  WARN: could not check {p.name} in DB ({e.__class__.__name__}: {e})")
            done = False
        if done:
            out[str(p)] = True
            print(f"  ✓ {p.name}")
        else:
            leftover.append(p)
    print(f"  ✗ batch {why}: {len(leftover)} of {len(paths)} not in the library"
          f"{' — retrying each on its own' if leftover else ''}")
    if result is not None:
        detail = _failure_detail(result)
        if detail:
            print(f"      {detail}")
    for p in leftover:
        out[str(p)] = single(p)
    return out


def _failure_detail(result) -> str:
    """One line of why: stderr tail if there is one, else the `Reconcile:` lines
    from stdout (where #393's mismatch report goes). Pure — testable."""
    err = (result.stderr or "").strip()
    if err:
        return err[-200:]
    out = (result.stdout or "").splitlines()
    for i, line in enumerate(out):
        if line.startswith("Reconcile:"):
            return " | ".join(l.strip() for l in out[i:i + 4] if l.strip())[:300]
    return ""


# ── watcher core (testable) ──────────────────────────────────────────────────

class Watcher:
    """Ties roots → candidate files → stability → dispatch. `tick()` is the pure
    polling step (also reused to drain candidates queued by watchdog events)."""

    # fable-audit round-5 #5: a file whose dispatch keeps failing (corrupt .MP4)
    # was retried every tick forever — whisper warm-up + log spam + RAM pressure,
    # 24/7. Back off exponentially and quarantine after N failures; a signature
    # change (the file was re-copied / fixed) clears the record for a fresh attempt.
    QUARANTINE_AFTER = 3
    BASE_BACKOFF_S = 30.0
    MAX_BACKOFF_S = 3600.0

    def __init__(
        self,
        roots: Sequence[Path],
        debounce_s: float = 5.0,
        dispatch: Callable[[Path], bool] = ingest_file,
        clock: Callable[[], float] = time.time,
        dispatch_batch: Optional[Callable[[List[Path]], Dict[str, bool]]] = None,
        max_batch: int = 8,
        batch_hold_s: float = 30.0,
    ):
        self.roots = [Path(r) for r in roots]
        self.tracker = StabilityTracker(debounce_s)
        self.dispatch = dispatch
        self.clock = clock
        # B8: files that are ready in the same tick go to ONE ingest run. Only on
        # by default with the real per-file dispatch — a caller injecting its own
        # `dispatch` (tests, embedders) keeps exact one-call-per-file behaviour
        # unless it passes a batch callable too.
        if dispatch_batch is None and dispatch is ingest_file:
            dispatch_batch = ingest_batch
        self.dispatch_batch = dispatch_batch
        self.max_batch = max(1, int(max_batch))
        # While other files are still being copied in, hold ready files (up to
        # batch_hold_s) so a card dump lands as a few batches rather than N runs
        # of one. 0 = never hold. Nothing pending → no hold, no added latency.
        self.batch_hold_s = batch_hold_s
        self._ready_since: Dict[str, float] = {}
        self.known: Set[str] = set()         # already-ingested resolved paths
        self._candidates: Set[str] = set()    # paths flagged by watchdog events
        # path -> (fail_count, next_retry_at, signature_at_failure)
        self._failures: Dict[str, Tuple[int, float, Signature]] = {}

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

        ready: List[Tuple[Path, Signature]] = []
        for p in paths:
            key = str(p)
            if key in self.known:
                self.tracker.forget(key)
                self._ready_since.pop(key, None)
                continue
            sig = file_signature(p)
            if not self.tracker.observe(key, sig, now):
                self._ready_since.pop(key, None)
                continue
            # Stable → ingest. Re-check it still exists (unmount race).
            if not p.exists():
                self.tracker.forget(key)
                self._ready_since.pop(key, None)
                continue
            # Back-off / quarantine gate (round-5 #5): a file that keeps failing
            # dispatch must not be retried every tick. A signature change means the
            # file was re-copied/fixed → clear the record and try fresh.
            fail = self._failures.get(key)
            if fail is not None:
                fcount, retry_at, fsig = fail
                if fsig == sig:
                    if fcount >= self.QUARANTINE_AFTER:
                        continue  # quarantined until the file changes
                    if now < retry_at:
                        continue  # still inside the back-off window
                else:
                    self._failures.pop(key, None)
            self._ready_since.setdefault(key, now)
            ready.append((p, sig))

        if not ready or self._should_hold(ready, now):
            return []

        dispatched: List[Path] = []
        for i in range(0, len(ready), self.max_batch):
            chunk = ready[i:i + self.max_batch]
            if self.dispatch_batch is not None and len(chunk) > 1:
                results = self.dispatch_batch([p for p, _ in chunk])
            else:
                results = {str(p): self.dispatch(p) for p, _ in chunk}
            for p, sig in chunk:
                key = str(p)
                self.tracker.forget(key)
                self._ready_since.pop(key, None)
                if results.get(key, False):
                    self.known.add(key)
                    self._failures.pop(key, None)
                    dispatched.append(p)
                else:
                    prev = self._failures.get(key)
                    fcount = prev[0] + 1 if (prev and prev[2] == sig) else 1
                    backoff = min(self.MAX_BACKOFF_S, self.BASE_BACKOFF_S * (2 ** (fcount - 1)))
                    self._failures[key] = (fcount, now + backoff, sig)
        return dispatched

    def _should_hold(self, ready: List[Tuple[Path, Signature]], now: float) -> bool:
        """Defer dispatch while more files are still arriving, so they share one
        ingest run. Never holds when batching is off, when a full batch is
        already waiting, or once the oldest ready file has waited batch_hold_s."""
        if self.dispatch_batch is None or self.batch_hold_s <= 0 or self.max_batch <= 1:
            return False
        if len(ready) >= self.max_batch:
            return False
        oldest = min(self._ready_since[str(p)] for p, _ in ready)
        if now - oldest >= self.batch_hold_s:
            return False
        ready_keys = {str(p) for p, _ in ready}
        # A file in back-off / quarantine is not "arriving" (it would hold every
        # batch for the full window), and neither is a tracked path that no
        # longer exists — the scan never re-observes a deleted file, so the
        # tracker would otherwise keep it forever.
        for k in self.tracker.pending():
            if k in ready_keys or k in self.known or k in self._failures:
                continue
            if Path(k).exists():
                return True
        return False

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
    parser.add_argument("--max-batch", type=int, default=8,
                        help="Max files per ingest run; files ready together share one model warm-up (default: 8, 1 = one run per file)")
    parser.add_argument("--batch-hold", type=float, default=30.0,
                        help="Seconds to hold ready files while others are still copying in, so they batch (default: 30, 0 = never hold)")
    args = parser.parse_args()

    roots = [Path(r).expanduser().resolve() for r in args.roots]
    missing = [r for r in roots if not r.is_dir()]
    if missing:
        print("Error: not a directory: " + ", ".join(str(m) for m in missing))
        sys.exit(1)

    w = Watcher(roots, debounce_s=args.debounce, max_batch=args.max_batch,
                # --once has no next tick to release a held file into.
                batch_hold_s=0.0 if args.once else args.batch_hold)
    w.load_known()
    if args.once:
        dispatched = w.tick()
        print(f"  dispatched {len(dispatched)} file(s)")
        return
    w.run(poll_interval=args.poll_interval, use_watchdog=not args.no_watchdog)


if __name__ == "__main__":
    main()
