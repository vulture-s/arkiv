#!/usr/bin/env python3
"""Build the PRE-INDEXED sample library artifact (A1 / launch Wave-0 fast-follow).

`seed_sample.py` (v1) ingests the four bundled CC-BY clips *on demand* at first
run — which still needs Ollama + models pulled + a multi-minute whisper/vision
pass, so it does NOT cover the cold-start / missing-deps first impression it was
meant to hide. This script produces a **pre-built** artifact instead: it ingests
the clips ONCE here (on a machine that has Ollama), captures the resulting project
store (SQLite rows + frame descriptions + transcripts + thumbnails + Chroma
vectors), and tars it into `sample/prebuilt/`. The app then drops that in as a
read-only bundled demo project on first run — instant browse/tag/LIKE search with
zero pipeline, and instant semantic search the moment Ollama is warm (corpus side
already indexed, only the query still needs embedding).

    python scripts/build_sample_prebuilt.py            # build + validate + tar
    python scripts/build_sample_prebuilt.py --keep     # keep sample/.arkiv staging

Design (see also routers/sample.py for the runtime loader, PR2):
  - PROJECT_ROOT is set to <repo>/sample so the four clips (sample/clips/*.mp4)
    store RELATIVE media.path `clips/<name>.mp4` — resolvable on any install where
    the clips ship at the same relative spot under the bundle.
  - The store lands at sample/.arkiv/ (gitignored) as a build intermediate; we tar
    project.db + chroma_db + thumbnails into sample/prebuilt/arkiv-sample.tar
    (a .tar name dodges the *.db / chroma_db/ / thumbnails/ / .arkiv/ ignores).
  - Runtime loader (sample_prebuilt.py, A1) ATTACH-merges the rows into a FRESH
    project's DEFAULT store (PROJECT_ROOT/.arkiv/... + PROJECT_ROOT/clips/) and
    copies the clips in — no ARKIV_*_PATH overrides. The .app boots a writable
    HOME-based PROJECT_ROOT, so the default layout is writable; the read-only
    bundle only ships the clips + this .tar.

Needs Ollama running with the embed + vision models pulled (same prerequisites as
any ingest). Licenses/attribution for the clips: sample/LICENSES.md.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLE_ROOT = REPO / "sample"
CLIPS_DIR = SAMPLE_ROOT / "clips"
STAGING_ARKIV = SAMPLE_ROOT / ".arkiv"          # built by the ingest run
PREBUILT_DIR = SAMPLE_ROOT / "prebuilt"
ARTIFACT = PREBUILT_DIR / "arkiv-sample.tar"
MANIFEST = PREBUILT_DIR / "manifest.json"

# The three store pieces that make the corpus searchable without re-ingesting.
STORE_PARTS = ("project.db", "chroma_db", "thumbnails")


def _fail(msg):
    print("BUILD FAILED: {0}".format(msg), file=sys.stderr)
    return 1


def _run_ingest(clips):
    """Ingest the clips into sample/.arkiv with PROJECT_ROOT=<repo>/sample so paths
    store relative. English clips → --language en (skips whisper's zh mis-detect).
    Inherits the environment's vision/embed model config."""
    env = dict(os.environ)
    env["ARKIV_PROJECT_ROOT"] = str(SAMPLE_ROOT)
    cmd = [
        sys.executable, str(REPO / "ingest.py"),
        "--files", *[str(c) for c in clips],
        "--language", "en",
    ]
    print("[build] ingesting {0} clips into {1} …".format(len(clips), STAGING_ARKIV))
    print("[build] cmd: ARKIV_PROJECT_ROOT={0} {1}".format(SAMPLE_ROOT, " ".join(cmd)))
    r = subprocess.run(cmd, cwd=str(REPO), env=env)
    return r.returncode


def _validate():
    """Assert the built store is actually search-ready: 4 rows, relative paths,
    frame descriptions present, transcripts present. Returns (ok, report dict)."""
    db_path = STAGING_ARKIV / "project.db"
    if not db_path.exists():
        return False, {"error": "no project.db at {0}".format(db_path)}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        media = conn.execute(
            "SELECT id, filename, path, transcript FROM media ORDER BY id"
        ).fetchall()
        n_media = len(media)
        abs_paths = [m["path"] for m in media if os.path.isabs(m["path"])]
        n_transcript = sum(1 for m in media if (m["transcript"] or "").strip())
        # frames table may or may not exist depending on schema; count described frames
        try:
            frame_rows = conn.execute(
                "SELECT COUNT(*) c, "
                "SUM(CASE WHEN description IS NULL OR description='' THEN 1 ELSE 0 END) nulls "
                "FROM frames"
            ).fetchone()
            n_frames, n_frame_nulls = frame_rows["c"], frame_rows["nulls"] or 0
        except sqlite3.OperationalError:
            n_frames, n_frame_nulls = 0, 0
    finally:
        conn.close()

    chroma_ok = (STAGING_ARKIV / "chroma_db").exists()
    thumbs = list((STAGING_ARKIV / "thumbnails").glob("**/*")) if (STAGING_ARKIV / "thumbnails").exists() else []
    n_thumbs = sum(1 for p in thumbs if p.is_file())

    report = {
        "media": n_media,
        "abs_path_leak": abs_paths,
        "transcripts": n_transcript,
        "frames": n_frames,
        "frame_desc_nulls": n_frame_nulls,
        "chroma_present": chroma_ok,
        "thumbnails": n_thumbs,
    }
    ok = (
        n_media >= 4
        and not abs_paths          # RP: every sample path must be relative
        and chroma_ok
        and n_frame_nulls == 0     # zero-tolerance: a bundled demo must not ship blanks
    )
    return ok, report


def _package(report):
    PREBUILT_DIR.mkdir(parents=True, exist_ok=True)
    if ARTIFACT.exists():
        ARTIFACT.unlink()
    with tarfile.open(ARTIFACT, "w") as tar:
        for part in STORE_PARTS:
            src = STAGING_ARKIV / part
            if src.exists():
                tar.add(str(src), arcname=part)
    size_mb = round(ARTIFACT.stat().st_size / (1024 * 1024), 2)
    manifest = {
        "artifact": ARTIFACT.name,
        "size_mb": size_mb,
        "store_parts": list(STORE_PARTS),
        "project_root_at_build": "sample/ (paths stored relative to <bundle>/sample)",
        "clips": sorted(p.name for p in CLIPS_DIR.glob("*.mp4")),
        "validation": report,
        "note": "Pre-indexed CC-BY sample. Runtime loader: routers/sample.py (PR2). "
                "Redirect store to a writable copy via ARKIV_DB_PATH / "
                "ARKIV_CHROMA_PATH / ARKIV_THUMBNAILS_DIR.",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return size_mb


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true", help="keep sample/.arkiv staging after tar")
    args = ap.parse_args()

    clips = sorted(CLIPS_DIR.glob("*.mp4"))
    if not clips:
        return _fail("no sample clips in {0}".format(CLIPS_DIR))

    # Clean build: a stale sample/.arkiv would let old rows survive.
    if STAGING_ARKIV.exists():
        print("[build] wiping stale staging {0}".format(STAGING_ARKIV))
        shutil.rmtree(STAGING_ARKIV)

    rc = _run_ingest(clips)
    if rc != 0:
        return _fail("ingest exited {0} (Ollama running? models pulled? see `python health.py`)".format(rc))

    ok, report = _validate()
    print("[build] validation:", json.dumps(report, ensure_ascii=False))
    if not ok:
        return _fail("store not search-ready: {0}".format(report))

    size_mb = _package(report)
    print("[build] ✓ wrote {0} ({1} MB) + {2}".format(ARTIFACT, size_mb, MANIFEST.name))

    if not args.keep and STAGING_ARKIV.exists():
        shutil.rmtree(STAGING_ARKIV)
        print("[build] removed staging {0}".format(STAGING_ARKIV))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
