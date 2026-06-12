#!/usr/bin/env python3
"""
arkiv — Folder Watcher
Monitors a directory for new media files and auto-ingests them.

Usage:
    python watch.py /path/to/footage
    python watch.py ~/Movies/rushes --interval 10
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Sequence, Set

MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts",  # video
    ".insv", ".360",  # 360 rigs (Insta360 / GoPro Max)
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg",  # audio
}


def find_new_files(watch_dir: Path, known: Set[str]) -> List[Path]:
    """Find media files not yet processed."""
    new = []
    for f in watch_dir.rglob("*"):
        if f.suffix.lower() in MEDIA_EXTS and str(f) not in known:
            # Skip files still being written (modified in last 5s)
            try:
                if time.time() - f.stat().st_mtime < 5:
                    continue
            except OSError:
                continue
            new.append(f)
    return sorted(new)


def run_tree(cmd: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run-like wrapper that kills the *whole process tree* on timeout.

    audit H8: plain subprocess.run(timeout=...) only kills the direct child;
    grandchildren (ffmpeg/whisper spawned by ingest.py) become orphans and keep
    running — and on Windows they hold the stdout/stderr pipes open, so
    communicate() hangs forever. POSIX: new session + killpg. Windows:
    taskkill /T /F (needs verification on PC — cannot be exercised on mac).
    """
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
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
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
            proc.kill()  # belt-and-suspenders if taskkill failed
        # Tree is dead, so draining the pipes terminates promptly; bound it anyway.
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        raise subprocess.TimeoutExpired(list(cmd), timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)


def ingest_file(filepath: Path) -> bool:
    """Run ingest.py on a single file."""
    script = Path(__file__).parent / "ingest.py"
    try:
        # audit H8: use run_tree so a timeout reaps ffmpeg/whisper grandchildren too
        result = run_tree(
            [sys.executable, str(script), "--dir", str(filepath)],
            timeout=600,  # 10 min per file
        )
        if result.returncode == 0:
            print(f"  ✓ {filepath.name}")
            return True
        else:
            print(f"  ✗ {filepath.name}: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ✗ {filepath.name}: timeout (>10min)")
        return False


def main():
    parser = argparse.ArgumentParser(description="arkiv folder watcher")
    parser.add_argument("directory", type=str, help="Directory to watch")
    parser.add_argument("--interval", type=int, default=30, help="Check interval in seconds (default: 30)")
    parser.add_argument("--once", action="store_true", help="Scan once and exit (no loop)")
    args = parser.parse_args()

    watch_dir = Path(args.directory).expanduser().resolve()
    if not watch_dir.is_dir():
        print(f"Error: {watch_dir} is not a directory")
        sys.exit(1)

    print(f"arkiv watcher — monitoring {watch_dir}")
    print(f"  Interval: {args.interval}s | Extensions: {', '.join(sorted(MEDIA_EXTS))}")
    print("")

    known = set()

    # Load already-ingested files from DB
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute("SELECT path FROM media").fetchall()
            known = {db.resolve_path(r["path"]) for r in rows}
        print(f"  {len(known)} files already in DB")
    except Exception as e:
        # audit L9: don't swallow DB load failure silently — without `known`
        # the watcher re-ingests everything it sees; warn loudly up front.
        print(f"  WARN: could not load known files from DB ({e.__class__.__name__}: {e}); "
              f"starting with empty set — already-ingested files may be re-processed")

    while True:
        new_files = find_new_files(watch_dir, known)
        if new_files:
            print(f"\n[{time.strftime('%H:%M:%S')}] Found {len(new_files)} new file(s):")
            for f in new_files:
                ok = ingest_file(f)
                if ok:
                    known.add(str(f))

        if args.once:
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
