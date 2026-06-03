"""export.py — Phase 12 corpus/JSONL export CLI.

Turns an arkiv library's transcripts + vision metadata into the two formats a
LoRA / RAG pipeline actually wants:

    python export.py corpus [--lang zh] [--out corpus.txt]
        merged plain-text corpus — transcripts only, blank-line separated, no
        JSON residue. For continued-pretraining / LoRA.

    python export.py jsonl [--lang zh] [--out chunks.jsonl]
        one JSON object per media on its own line: {id, text, metadata}. For
        RAG ingestion.

    python export.py txt <media_id> [--out clip.txt]
        a single clip's transcript.

Reads the DB directly via db.get_conn() (same as ingest.py) so it stays free of
the FastAPI server import. Honours --db / ARKIV_DB_PATH.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import db


def _frame_descriptions(frame_tags_value) -> List[str]:
    """Pull human descriptions out of the frame_tags JSON list.

    Production frame_tags is a JSON list of dicts each carrying `description`
    (vision.py). Tolerates None / malformed / legacy shapes by returning [].
    """
    if not frame_tags_value:
        return []
    try:
        data = json.loads(frame_tags_value)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            desc = item.get("description")
            if isinstance(desc, str) and desc.strip():
                out.append(desc.strip())
    return out


def _iter_media(conn, lang: Optional[str] = None) -> Iterator[Dict]:
    """Yield full media rows (ordered by id), optionally filtered by language."""
    sql = "SELECT * FROM media"
    params: List = []
    if lang:
        sql += " WHERE lang = ?"
        params.append(lang)
    sql += " ORDER BY id"
    for row in conn.execute(sql, params):
        yield dict(row)


def _tags_for(conn, media_id: int) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM tags WHERE media_id = ? ORDER BY name", (media_id,)
    ).fetchall()
    return [r["name"] for r in rows]


def build_corpus(lang: Optional[str] = None) -> str:
    """Concatenated transcripts (blank-line separated). Plain text only."""
    parts: List[str] = []
    with db.get_conn() as conn:
        for rec in _iter_media(conn, lang):
            text = (rec.get("transcript") or "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def build_jsonl_lines(lang: Optional[str] = None) -> List[str]:
    """One compact JSON object per media with a transcript: {id, text, metadata}."""
    lines: List[str] = []
    with db.get_conn() as conn:
        for rec in _iter_media(conn, lang):
            text = (rec.get("transcript") or "").strip()
            if not text:
                continue  # nothing to retrieve on
            obj = {
                "id": rec.get("id"),
                "text": text,
                "metadata": {
                    "filename": rec.get("filename"),
                    "lang": rec.get("lang"),
                    "duration_s": rec.get("duration_s"),
                    "fps": rec.get("fps"),
                    "content_type": rec.get("content_type"),
                    "camera_make": rec.get("camera_make"),
                    "camera_model": rec.get("camera_model"),
                    "rating": rec.get("rating"),
                    "tags": _tags_for(conn, rec["id"]),
                    "frame_descriptions": _frame_descriptions(rec.get("frame_tags")),
                },
            }
            lines.append(json.dumps(obj, ensure_ascii=False))
    return lines


def export_txt(media_id: int) -> str:
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise KeyError("media id {0} not found".format(media_id))
    return (rec.get("transcript") or "").strip()


def _emit(content: str, out: Optional[str]) -> None:
    if out:
        Path(out).expanduser().write_text(content, encoding="utf-8")
        print("Wrote {0} ({1} bytes)".format(out, len(content.encode("utf-8"))))
    else:
        sys.stdout.write(content)
        if content and not content.endswith("\n"):
            sys.stdout.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export arkiv transcripts/metadata for LoRA/RAG")
    parser.add_argument("--db", default="", help="Path to SQLite DB (default: config DB_PATH)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_corpus = sub.add_parser("corpus", help="Merged plain-text corpus (transcripts only)")
    p_corpus.add_argument("--lang", default=None, help="Filter by language (e.g. zh)")
    p_corpus.add_argument("--out", default=None, help="Output file (default: stdout)")

    p_jsonl = sub.add_parser("jsonl", help="RAG chunks: one JSON object per media")
    p_jsonl.add_argument("--lang", default=None, help="Filter by language (e.g. zh)")
    p_jsonl.add_argument("--out", default=None, help="Output file (default: stdout)")

    p_txt = sub.add_parser("txt", help="A single clip's transcript")
    p_txt.add_argument("media_id", type=int)
    p_txt.add_argument("--out", default=None, help="Output file (default: stdout)")

    args = parser.parse_args(argv)
    if args.db:
        db.DB_PATH = Path(args.db)

    if args.cmd == "corpus":
        _emit(build_corpus(args.lang), args.out)
    elif args.cmd == "jsonl":
        _emit("\n".join(build_jsonl_lines(args.lang)), args.out)
    elif args.cmd == "txt":
        try:
            _emit(export_txt(args.media_id), args.out)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
