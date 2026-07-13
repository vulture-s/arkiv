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

    python export.py chapters <media_id> [--format youtube|ffmetadata] [--out]
        chapter markers from the clip's scene frames (ProChapter-style).

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


def export_srt(media_id: int, max_units: float = 14.0) -> str:
    """Laid-out SRT for one clip, using the Phase 12.5 subtitle engine.

    Uses segment-aligned timestamps (segments_json) when present; falls back to
    a single full-duration cue from the transcript otherwise.
    """
    import subtitle

    rec = db.get_record_by_id(media_id)
    if not rec:
        raise KeyError("media id {0} not found".format(media_id))
    segments = []
    seg_json = rec.get("segments_json")
    if seg_json:
        try:
            parsed = json.loads(seg_json)
        except (ValueError, TypeError):
            parsed = None
        # Only a list of segment dicts is usable; anything else falls back to
        # the transcript path rather than crashing in segments_to_srt (Codex).
        if isinstance(parsed, list):
            segments = [s for s in parsed if isinstance(s, dict)]
    if not segments:
        transcript = (rec.get("transcript") or "").strip()
        if not transcript:
            return ""
        segments = [{"start": 0.0, "end": rec.get("duration_s") or 0.0, "text": transcript}]
    return subtitle.segments_to_srt(segments, max_units=max_units)


def _fmt_ts(seconds: float) -> str:
    """Seconds → `MM:SS` (or `H:MM:SS` past an hour). YouTube-chapter style."""
    s = int(max(0.0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return "{0}:{1:02d}:{2:02d}".format(h, m, sec)
    return "{0:02d}:{1:02d}".format(m, sec)


def _chapter_title(desc: Optional[str], idx: int, max_len: int = 60) -> str:
    """One-line chapter title from a frame's vision description.

    Collapses whitespace, clips to the first sentence (CJK 。！？ or ASCII . ! ?)
    when that lands within max_len, then hard-truncates. Falls back to a numbered
    'Chapter N' when the frame has no usable description.
    """
    if not desc or not desc.strip():
        return "Chapter {0}".format(idx)
    flat = " ".join(desc.split())
    cut = len(flat)
    for term in ("。", "！", "？"):
        i = flat.find(term)
        if 0 <= i < cut:
            cut = i + 1  # keep the CJK terminator
    for term in (". ", "! ", "? "):
        i = flat.find(term)
        if 0 <= i < cut:
            cut = i + 1  # keep the '.', drop the space
    flat = flat[:cut].strip()
    if len(flat) > max_len:
        flat = flat[:max_len].rstrip() + "…"
    return flat or "Chapter {0}".format(idx)


def build_chapters(media_id: int, fmt: str = "youtube") -> str:
    """Chapter markers for one clip from its sampled frames.

    For long clips arkiv's frame timestamps are scene-change points (see
    frames._extract_scene_persistent), so they double as natural chapter
    boundaries; short clips get evenly-spaced markers. Titles come from each
    frame's vision description.

    fmt="youtube"     → `MM:SS Title` lines (first marker forced to 0:00, as
                        YouTube requires).
    fmt="ffmetadata"  → ffmpeg metadata file; embed with
                        `ffmpeg -i in.mp4 -i chapters.txt -map_metadata 1 -codec copy out.mp4`.
    """
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise KeyError("media id {0} not found".format(media_id))
    duration = float(rec.get("duration_s") or 0.0)

    seen = set()
    ordered: List[List] = []  # [timestamp_s, description]
    for fr in sorted(db.get_frames(media_id), key=lambda f: f.get("timestamp_s") or 0.0):
        ts = float(fr.get("timestamp_s") or 0.0)
        bucket = int(ts)  # collapse sub-second duplicates
        if bucket in seen:
            continue
        seen.add(bucket)
        ordered.append([ts, fr.get("description")])
    if not ordered:
        return ""

    titled = [(ts, _chapter_title(desc, i + 1)) for i, (ts, desc) in enumerate(ordered)]

    if fmt == "youtube":
        if titled[0][0] > 0.5:  # YouTube requires a 0:00 chapter
            titled.insert(0, (0.0, "Intro"))
        # YouTube silently ignores the whole chapter list unless every chapter is
        # >= 10s after the previous one — drop too-close scene markers (keep the
        # first). ffmetadata has no such rule, so this only applies here.
        min_gap = 10.0
        kept = []
        for ts, title in titled:
            if not kept or ts - kept[-1][0] >= min_gap:
                kept.append((ts, title))
        return "\n".join("{0} {1}".format(_fmt_ts(ts), title) for ts, title in kept)

    if fmt == "ffmetadata":
        lines = [";FFMETADATA1"]
        for i, (ts, title) in enumerate(titled):
            start_ms = int(ts * 1000)
            nxt = titled[i + 1][0] if i + 1 < len(titled) else (duration or ts + 1.0)
            end_ms = int(nxt * 1000)
            if end_ms <= start_ms:
                end_ms = start_ms + 1000
            esc = title
            for a, b in (("\\", "\\\\"), ("=", "\\="), (";", "\\;"), ("#", "\\#"), ("\n", " ")):
                esc = esc.replace(a, b)
            lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                      "START={0}".format(start_ms), "END={0}".format(end_ms),
                      "title={0}".format(esc)]
        return "\n".join(lines)

    raise ValueError("unknown chapter format: {0}".format(fmt))


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

    p_srt = sub.add_parser("srt", help="A single clip's laid-out SRT (Phase 12.5 engine)")
    p_srt.add_argument("media_id", type=int)
    p_srt.add_argument("--max-cjk", type=float, default=14.0, help="Max CJK units per line (default 14)")
    p_srt.add_argument("--out", default=None, help="Output file (default: stdout)")

    p_ch = sub.add_parser("chapters", help="A single clip's chapter markers from scene frames")
    p_ch.add_argument("media_id", type=int)
    p_ch.add_argument("--format", choices=["youtube", "ffmetadata"], default="youtube",
                      help="youtube = 'MM:SS Title' lines; ffmetadata = ffmpeg chapter file")
    p_ch.add_argument("--out", default=None, help="Output file (default: stdout)")

    args = parser.parse_args(argv)
    if args.db:
        db.set_db_path(Path(args.db))  # R5-23 (#54): via SSOT accessor, not a raw rebind

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
    elif args.cmd == "srt":
        try:
            _emit(export_srt(args.media_id, args.max_cjk), args.out)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 1
    elif args.cmd == "chapters":
        try:
            _emit(build_chapters(args.media_id, args.format), args.out)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
