from __future__ import annotations
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
import subprocess
import sys
import time
from pathlib import Path

MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",  # video
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg",  # audio
}


def find_new_files(watch_dir: Path, known: set[str]) -> list[Path]:
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


def ingest_file(filepath: Path) -> bool:
    """Run ingest.py on a single file."""
    script = Path(__file__).parent / "ingest.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(filepath)],
            capture_output=True,
            text=True,
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

    known: set[str] = set()

    # Load already-ingested files from DB
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute("SELECT path FROM media").fetchall()
            known = {r["path"] for r in rows}
        print(f"  {len(known)} files already in DB")
    except Exception:
        pass

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
