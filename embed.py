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

import db
import vectordb as vdb


def get_all_records() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM media ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_indexed_media_ids(col) -> set[str]:
    """Return set of media_ids already in ChromaDB."""
    result = col.get(include=["metadatas"])
    return {m["media_id"] for m in result["metadatas"]}


def run_embed(rebuild: bool = False):
    print(f"{'Rebuilding' if rebuild else 'Updating'} vector index...")
    col = vdb.get_collection(reset=rebuild)

    records = get_all_records()
    if not records:
        print("No records in SQLite. Run ingest.py first.")
        sys.exit(1)

    indexed_ids = set() if rebuild else get_indexed_media_ids(col)
    to_process = [r for r in records if str(r["id"]) not in indexed_ids]

    print(f"Total records: {len(records)} | Already indexed: {len(indexed_ids)} | To process: {len(to_process)}")

    if not to_process:
        print("Index is up to date.")
        return

    total_chunks = 0
    for i, rec in enumerate(to_process, 1):
        print(f"[{i}/{len(to_process)}] {rec['filename']}", end="", flush=True)
        try:
            n = vdb.upsert_record(col, rec)
            total_chunks += n
            print(f" → {n} chunk(s) ✓")
        except Exception as e:
            print(f" [ERROR: {e}]")

    print(f"\nDone. {total_chunks} total chunks in collection '{vdb.COLLECTION_NAME}'.")
    print(f"ChromaDB path: {vdb.CHROMA_PATH}")


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

    run_embed(rebuild=args.rebuild)

    if args.search:
        print()
        run_search(args.search)


if __name__ == "__main__":
    main()
