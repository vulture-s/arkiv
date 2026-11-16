#!/usr/bin/env python3
"""Block build output and oversized blobs from entering git history.

Why this exists: the six largest blobs in arkiv's history (23-29 MB each) are
all `src-tauri/target/debug/...` compiler output that was committed once and
then removed. Removing a file does not remove the blob, so every clone still
downloads them. History is deliberately NOT rewritten; this guard only stops
the next one.

What it checks — every blob that is NEW relative to a base ref, across every
commit in the range (not just the final tree). The repo allows merge commits,
so a big file added in commit 1 and deleted in commit 2 of the same PR would
still land in main's history; a final-tree-only check would miss it.

  1. Path lies under a build-output directory (see BUILD_DIR_PATTERNS) or has
     an installer/binary-artifact extension (BUILD_FILE_SUFFIXES).
  2. Blob size exceeds the limit (default 5 MiB, override with --max-bytes or
     ARKIV_MAX_BLOB_BYTES).

Threshold rationale (measured 2026-11-16 on main @ 162f7f8): the largest
tracked file is site/public/video/arkiv-ui-tour.mp4 at 2.7 MB; next are
models/silero_vad.onnx 2.2 MB and a 1.9 MB design PNG. 5 MiB leaves ~1.9x
headroom over the largest legitimate asset while sitting 4-6x below the
23-29 MB compiler blobs this is meant to stop. A legitimate asset above 5 MiB
should be a release attachment or an external download, not a git blob.

Usage:
  python scripts/check_large_files.py --base origin/main      # PR range
  python scripts/check_large_files.py --all                   # whole tracked tree

Exit 0 = clean, 1 = violations, 2 = usage / git error.
Stdlib only; Python 3.8+ (NAS floor).
"""
import argparse
import os
import subprocess
import sys

DEFAULT_MAX_BYTES = 5 * 1024 * 1024

# Directory segments that only ever hold generated output. A path matches if
# it starts with an anchored prefix, or contains an unanchored segment anywhere.
ANCHORED_BUILD_PREFIXES = (
    "src-tauri/target/",
    "src-tauri/backend/python/",
    "src-tauri/backend/site-packages/",
    "src-tauri/backend/src/",
    "frontend/dist/",
    "site/dist/",
    "site/.astro/",
    "dist/",
    "build/",
)
ANY_DEPTH_BUILD_SEGMENTS = (
    "node_modules",
    "__pycache__",
    ".venv",
    ".venv-pack",
)
# Packaged installers / native binaries: always a build product here.
BUILD_FILE_SUFFIXES = (
    ".dmg",
    ".msi",
    ".exe",
    ".appimage",
    ".deb",
    ".rpm",
    ".pkg",
    ".dylib",
    ".dll",
    ".so",
    ".whl",
    ".pyc",
)


def build_output_reason(path):
    """Return a human reason if `path` is build output, else None."""
    for prefix in ANCHORED_BUILD_PREFIXES:
        if path.startswith(prefix):
            return "under build-output dir " + prefix
    segments = path.split("/")[:-1]
    for seg in ANY_DEPTH_BUILD_SEGMENTS:
        if seg in segments:
            return "under build-output dir " + seg + "/"
    lower = path.lower()
    for suffix in BUILD_FILE_SUFFIXES:
        if lower.endswith(suffix):
            return "build artifact extension " + suffix
    return None


def _git(args, cwd, input_bytes=None):
    proc = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "git " + " ".join(args) + " failed: " + proc.stderr.decode("utf-8", "replace").strip()
        )
    return proc.stdout


def new_blobs(cwd, base, head):
    """Yield (path, size) for every blob reachable from head but not from base."""
    # "oid path" for trees/blobs, bare "oid" for commits.
    named = _git(["rev-list", "--objects", head, "--not", base], cwd).decode("utf-8", "replace")
    oids = []
    path_of = {}
    for line in named.splitlines():
        parts = line.split(" ", 1)
        if not parts[0]:
            continue
        oids.append(parts[0].encode())
        if len(parts) == 2 and parts[1]:
            path_of.setdefault(parts[0], parts[1])
    if not oids:
        return []
    batch = _git(
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        cwd,
        input_bytes=b"\n".join(oids) + b"\n",
    ).decode("utf-8", "replace")
    result = []
    for line in batch.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        oid, _, size = parts
        result.append((path_of.get(oid, oid), int(size)))
    return result


def tracked_blobs(cwd):
    """Yield (path, size) for every file in the HEAD tree."""
    out = _git(["ls-tree", "-r", "-l", "-z", "HEAD"], cwd).decode("utf-8", "replace")
    result = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        fields = meta.split()
        if len(fields) == 4 and fields[1] == "blob":
            result.append((path, int(fields[3])))
    return result


def find_violations(blobs, max_bytes):
    violations = []
    for path, size in sorted(set(blobs)):
        reason = build_output_reason(path)
        if reason:
            violations.append((path, size, reason))
        elif size > max_bytes:
            violations.append(
                (path, size, "size %.2f MiB > limit %.2f MiB" % (size / 1048576.0, max_bytes / 1048576.0))
            )
    return violations


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base", help="base ref; checks blobs new in HEAD relative to it")
    mode.add_argument("--all", action="store_true", help="check every file in HEAD's tree")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument(
        "--max-bytes",
        type=int,
        default=int(os.environ.get("ARKIV_MAX_BLOB_BYTES", DEFAULT_MAX_BYTES)),
    )
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)

    try:
        if args.all:
            blobs = tracked_blobs(args.repo)
            scope = "tracked tree at HEAD"
        else:
            blobs = new_blobs(args.repo, args.base, args.head)
            scope = "blobs new in %s vs %s" % (args.head, args.base)
    except RuntimeError as exc:
        print("check_large_files: " + str(exc), file=sys.stderr)
        return 2

    violations = find_violations(blobs, args.max_bytes)
    if not violations:
        print("check_large_files: OK — %d blob(s) checked (%s)" % (len(set(blobs)), scope))
        return 0
    for path, size, reason in violations:
        print("::error file=%s::%s (%d bytes)" % (path, reason, size))
    print("")
    print("check_large_files: %d violation(s) in %s." % (len(violations), scope))
    print("Build output belongs in .gitignore; large assets belong in a release")
    print("attachment or external download. Removing the file in a later commit")
    print("does NOT help — the blob stays in history. Rewrite the branch instead.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
