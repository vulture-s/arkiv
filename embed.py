#!/usr/bin/env python3
"""
Local Media Asset Manager — Phase 2: Build Vector Index
Reads SQLite records → chunks transcripts → embeds via Ollama → stores in ChromaDB

Usage:
    python embed.py              # incremental (skip already-indexed)
    python embed.py --rebuild    # drop and rebuild entire index
    python embed.py --search "drone footage aerial"  # quick CLI search test
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import db
import vectordb as vdb


def _now_iso() -> str:
    """UTC ISO timestamp for media.embedded_at (matches processed_at style)."""
    return datetime.now(timezone.utc).isoformat()


# audit H12: after this many consecutive per-record failures, assume a systemic
# outage (Ollama/Chroma down) and stop early instead of burning through the
# whole library printing the same error.
FAIL_FAST_CONSECUTIVE = 10


def get_all_media_ids() -> List[int]:
    """IDs only — reconcile/diff doesn't need the heavy transcript/JSON columns
    (audit M25)."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id FROM media ORDER BY id").fetchall()
        return [r[0] for r in rows]


def get_records_by_ids(ids: List[int]) -> List[Dict]:
    """Fetch full rows only for the records that actually need (re)embedding
    (audit M25). Chunked IN-queries to stay under SQLite's variable limit."""
    if not ids:
        return []
    records: List[Dict] = []
    with db.get_conn() as conn:
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT * FROM media WHERE id IN ({placeholders}) ORDER BY id",
                batch,
            ).fetchall()
            records.extend(dict(r) for r in rows)
    return records


def get_indexed_media_ids(col) -> set[str]:
    """media_ids already in ChromaDB, derived from chunk-id PREFIXES.

    fable-audit round-5 #31: pulling include=["metadatas"] fetched every chunk's
    full metadata (hundreds of MB for 150-250k chunks) on EVERY reconcile — including
    no-op watch runs — just to build an id set. Chunk ids are '{media_id}_t{i}' /
    '{media_id}_f0' (vectordb.build/upsert) and media ids are integers (no '_'), so
    the prefix before the first '_' is the media_id. include=[] returns only the ids.
    Returned as strings, matching the reconcile diff's str(id) comparisons."""
    result = col.get(include=[])
    ids = result.get("ids") or []
    return {cid.split("_", 1)[0] for cid in ids}


def get_content_signatures() -> Dict[str, Tuple[str, str]]:
    """{str(media_id): (stored_embed_hash, current_content_hash)} for every media.

    current_content_hash = vectordb.content_hash over the row's CURRENT source text
    (build_doc_text). Comparing it to the stored media.embed_hash is how run_embed
    detects a row whose description/transcript changed AFTER it was embedded — the
    "向量索引靜默過期" bug — without depending on the caller passing force_ids.

    Pulls ONLY the columns build_doc_text needs (filename, transcript, frame_tags) +
    embed_hash — deliberately NOT the heavy words_json/segments_json (audit M25: don't
    load the whole library's heavy columns just to diff freshness)."""
    sigs: Dict[str, Tuple[str, str]] = {}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, transcript, frame_tags, embed_hash FROM media"
        ).fetchall()
    for r in rows:
        rec = {
            "filename": r["filename"],
            "transcript": r["transcript"],
            "frame_tags": r["frame_tags"],
        }
        sigs[str(r["id"])] = (r["embed_hash"], vdb.content_hash(rec))
    return sigs


def run_embed(rebuild: bool = False, force_ids=None, prune: bool = True) -> dict:
    """Embed pending records. Returns stats {"ok": n, "errors": n, "aborted": bool}
    so the CLI can map failures to exit codes (audit H12) without sys.exit()-ing
    out from under the in-process caller in ingest.py.

    Raises vdb.EmbeddingDimensionMismatch instead of exiting: the CLI converts it
    to exit 2, while ingest's per-call ``except Exception`` catches and reports it
    (previously sys.exit(1) here killed the whole ingest process).
    """
    print(f"{'Rebuilding' if rebuild else 'Updating'} vector index...")
    col = vdb.get_collection(reset=rebuild)

    # audit M25: reconcile only needs ids — don't load every transcript /
    # words_json / frame_tags column for the whole library on each run.
    all_ids = get_all_media_ids()
    if not all_ids:
        print("No records in SQLite. Run ingest.py first.")
        sys.exit(1)

    force_ids = {str(i) for i in (force_ids or set())}
    indexed_ids = set() if rebuild else get_indexed_media_ids(col)

    # Reconcile: drop Chroma entries whose media_id no longer exists in SQLite,
    # so deleted clips stop surfacing in search (H5).
    if prune and not rebuild:
        db_ids = {str(i) for i in all_ids}
        orphans = indexed_ids - db_ids
        for oid in orphans:
            vdb.delete_media(col, oid)
        if orphans:
            print(f"Pruned {len(orphans)} orphaned media id(s) from index.")

    # Freshness by CONTENT, not just presence (fix: 向量索引靜默過期). The old test
    # "not indexed OR forced" skipped any already-indexed row whose description was
    # written AFTER it was embedded (the only writer, `--vision-only`, never passed
    # force_ids) → stale forever, silently. Now compare each row's current source-text
    # hash to the one stamped at last embed (media.embed_hash via get_content_signatures).
    signatures = {} if rebuild else get_content_signatures()
    new_ids, stale_ids, unverified_ids, forced, unchanged = [], [], [], [], 0
    to_process_ids: List = []
    for mid in all_ids:
        smid = str(mid)
        if rebuild:
            to_process_ids.append(mid)
            continue
        stored, current = signatures.get(smid, (None, None))
        if smid not in indexed_ids:
            new_ids.append(mid)                 # never embedded
        elif smid in force_ids:
            forced.append(mid)                  # caller forced (e.g. --refresh)
        elif stored is None:
            unverified_ids.append(mid)          # legacy row w/o embed_hash → can't verify → re-embed
        elif stored != current:
            stale_ids.append(mid)               # description/transcript changed after embed
        else:
            unchanged += 1                      # content-verified identical → skip
            continue
        to_process_ids.append(mid)

    # ③ report so "0" that means "checked, nothing stale" is distinguishable from
    # "0" that means "never looked". stale + unverified = staleness this run caught.
    stale_detected = len(stale_ids) + len(unverified_ids)
    print(
        f"Total: {len(all_ids)} | indexed: {len(indexed_ids)} | new: {len(new_ids)} | "
        f"stale: {len(stale_ids)} | unverified: {len(unverified_ids)} | forced: {len(forced)} | "
        f"content-unchanged: {unchanged} → to embed: {len(to_process_ids)} "
        f"(stale detected: {stale_detected})"
    )
    if unverified_ids:
        print(f"⚠ {len(unverified_ids)} media without embed_hash (legacy/unverified) — "
              f"freshness NOT confirmable, re-embedding to be safe (not treated as up-to-date).")

    if not to_process_ids:
        # Only claim up-to-date when we actually compared content this run.
        if rebuild:
            print("Nothing to embed.")
        else:
            print(f"Index is up to date (content-verified {unchanged}/{len(all_ids)}).")
        return {"ok": 0, "errors": 0, "aborted": False,
                "new": 0, "stale": 0, "unverified": 0, "forced": 0, "stale_detected": 0}

    # audit M25: full rows fetched only for the work set.
    to_process = get_records_by_ids(to_process_ids)

    total_chunks = 0
    ok = 0
    errors = 0          # audit H12: count failures instead of printing-and-forgetting
    consecutive = 0
    aborted = False
    for i, rec in enumerate(to_process, 1):
        print(f"[{i}/{len(to_process)}] {rec['filename']}", end="", flush=True)
        try:
            n = vdb.upsert_record(col, rec)
            # Stamp what we just embedded so a later run can tell this row is fresh
            # (content-verified) vs stale — the crux of the fix. Same text upsert
            # indexed (build_doc_text). Best-effort: a failed stamp only costs a
            # redundant re-embed next run, never a missed staleness.
            try:
                db.set_embed_state(rec["id"], vdb.content_hash(rec), _now_iso())
            except Exception as se:
                print(f" [warn: embed_hash stamp failed: {se}]", end="")
            total_chunks += n
            ok += 1
            consecutive = 0
            print(f" -> {n} chunk(s) OK")
        except vdb.EmbeddingDimensionMismatch:
            # Whole index is incompatible — every row will fail. Propagate so the
            # caller decides (CLI: exit 2; ingest: caught + reported, ingest itself
            # keeps its own exit status).
            print()
            raise
        except Exception as e:
            errors += 1
            consecutive += 1
            print(f" [ERROR: {e}]")
            if consecutive >= FAIL_FAST_CONSECUTIVE:
                # audit H12: N identical-class failures in a row ≈ Ollama/Chroma
                # outage; stop early instead of failing the whole library one by one.
                remaining = len(to_process) - i
                print(f"\n[ABORT] {consecutive} consecutive failures — embedding backend "
                      f"likely down; stopping early ({remaining} record(s) not attempted).")
                aborted = True
                break

    status = "Done." if not errors else f"Done with {errors} error(s) ({ok} ok)."
    print(f"\n{status} {total_chunks} total chunks in collection '{vdb.COLLECTION_NAME}'.")
    print(f"ChromaDB path: {vdb.CHROMA_PATH}")
    return {"ok": ok, "errors": errors, "aborted": aborted,
            "new": len(new_ids), "stale": len(stale_ids),
            "unverified": len(unverified_ids), "forced": len(forced),
            "stale_detected": stale_detected}


def run_search(query: str):
    print(f"Query: \"{query}\"\n")
    results = vdb.search(query, n_results=5)
    if not results:
        print("No results.")
        return
    for i, r in enumerate(results, 1):
        print(f"{i}. [{r['score']:.3f}] {r['filename']}  ({r['duration_s']:.0f}s, {r['lang'] or '?'})")
        print(f"   {r['excerpt'][:200]}")
        print(f"   {r['path']}\n")


def main():
    parser = argparse.ArgumentParser(description="Build vector index for Media Asset Manager")
    parser.add_argument("--rebuild", action="store_true", help="Drop and rebuild entire ChromaDB index")
    parser.add_argument("--search", metavar="QUERY", help="Quick semantic search test after build")
    args = parser.parse_args()

    # audit H12: real exit codes — all-failed exit 2, partial failure exit 1
    # (mirrors ingest Phase 8.0e), instead of "Done." + exit 0 with Ollama down.
    try:
        stats = run_embed(rebuild=args.rebuild)
    except vdb.EmbeddingDimensionMismatch as e:
        print(f"[ABORT] {e}")
        sys.exit(2)
    if stats["errors"]:
        sys.exit(2 if stats["ok"] == 0 else 1)

    if args.search:
        print()
        run_search(args.search)


if __name__ == "__main__":
    main()
