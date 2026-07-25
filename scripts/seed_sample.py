#!/usr/bin/env python3
"""Seed a tiny sample library so a fresh arkiv shows the search-awe on first run —
before you've ingested any of your own footage.

Ingests the four bundled CC-BY Blender open-movie clips (sample/clips/) into the
current project root, then you can immediately try semantic search. Idempotent:
re-running skips clips already indexed. Remove them anytime via the UI.

    python scripts/seed_sample.py

Needs Ollama running with the models pulled (see docs/quickstart-mac.md) — the
same prerequisites as any ingest. Licenses/attribution: sample/LICENSES.md.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLIPS_DIR = REPO / "sample" / "clips"

# The queries these clips make searchable — printed so a first-time user knows
# exactly what to type to feel the magic immediately.
DEMO_QUERIES = "llama · 駝羊 · cat · city"


def _already_indexed():
    """Return the set of sample filenames already in the DB (empty if no DB/table)."""
    try:
        import db

        with db.get_conn() as conn:
            rows = conn.execute("SELECT filename FROM media").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def main():
    clips = sorted(CLIPS_DIR.glob("*.mp4"))
    if not clips:
        print("No sample clips found in {0}".format(CLIPS_DIR), file=sys.stderr)
        return 1

    known = _already_indexed()
    todo = [c for c in clips if c.name not in known]
    if not todo:
        print("Sample library already loaded. Try searching:  " + DEMO_QUERIES)
        return 0

    print("Loading {0} sample clips (CC-BY Blender open movies)…".format(len(todo)))
    print("(needs Ollama running + models pulled — see docs/quickstart-mac.md)\n")
    cmd = [
        sys.executable,
        str(REPO / "ingest.py"),
        "--files",
        *[str(c) for c in todo],
        "--language",
        "en",
    ]
    try:
        subprocess.run(cmd, check=True, cwd=str(REPO))
    except subprocess.CalledProcessError as e:
        print(
            "\nSample ingest failed (exit {0}). Is Ollama running? "
            "Run `python health.py` to check.".format(e.returncode),
            file=sys.stderr,
        )
        return e.returncode

    print("\n✓ Sample library ready. Try searching:  " + DEMO_QUERIES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
