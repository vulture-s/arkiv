"""exposure_report.py — brand / object exposure report over vision descriptions.

The question it answers: "a sponsored shoot produced 200 clips — how many of
them show our product, for how many seconds in total, and in what scenes?"

How a hit is decided
--------------------
A scene hits when any of the given terms appears in that scene's keyframe
vision output: the frame `description` or its vision `tags`. Matching is
case-insensitive, width-insensitive (NFKC: full-width ＦＵＲＵＴＥＣＨ == FURUTECH)
and script-insensitive for Chinese (both sides are folded to Simplified via
zh_convert, so 古河 matches 古河 and a Simplified-stored 电源线 matches 電源線).
Without the opencc wheel the script fold is a no-op and that leg degrades to an
exact-script match — the report says so in `method.script_fold`.

How exposure seconds are counted
--------------------------------
Scenes are cut exactly as the scene API cuts them — scenes._build_scenes: one
scene per persisted keyframe, end = the next keyframe's start, the last scene
ending at media.duration_s. A hit scene contributes its whole duration_s.
Exposure is therefore "time spent in scenes whose keyframe shows the term",
NOT a per-frame on-screen measurement: a product visible for 2 s inside a 20 s
scene counts 20 s if the keyframe caught it, and 0 s if it did not.

That is the honest limit, carried in every report (`method.note`): vision tags
are a VLM's description of one keyframe per scene. It misses things and it
mislabels things. Clips with no vision analysis at all cannot hit and are
counted separately (`clips_unanalysed`) so a low number is not mistaken for
"absent".

Scope
-----
`filters` takes the search box's key:value syntax (search_syntax: tag:,
camera:, lang:, rating:, type:, shot:) and narrows the clips considered. Free
text is refused there — it would be ambiguous whether it is a filter or a term.

Leaf module: db + mediatypes + scenes + search_syntax + zh_convert + pathres. No server
import, so both the HTTP route and the stdio MCP server can use it.

CLI:
  python exposure_report.py "古河" "FURUTECH" --filters "shot:2026-10" [--format md|json]
      [--group-by date|camera] [--output report.md]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence

import db
import mediatypes
import scenes
import search_syntax
import zh_convert
from pathres import _display_path

METHOD_NOTE = (
    "依 vision 描述比對，非逐格偵測：每個 scene 只看一張關鍵影格的 VLM 描述與標籤，"
    "會漏判也會誤判；曝光秒數＝命中 scene 的整段長度（scene 切法同 get_scenes），"
    "不是產品實際在畫面上的秒數。"
)
METHOD_NOTE_EN = (
    "Matched against vision descriptions, not frame-by-frame detection: each scene "
    "is judged from one keyframe's VLM description/tags, which can miss or mislabel; "
    "exposure seconds = the full duration of every hit scene (same scene cut as "
    "get_scenes), not measured on-screen time."
)

SNIPPET_CHARS = 120
GROUP_BY = ("date", "camera")
_UNKNOWN = "(unknown)"


class ReportError(ValueError):
    """Bad input (no terms, free text in filters, bad filter value, bad group_by)."""


# ── matching ──────────────────────────────────────────────────────────────────
def _fold(text: Optional[str]) -> str:
    """Case-, width- and (with opencc) script-insensitive form used on BOTH sides."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = zh_convert.to_simplified(s) or s
    return s.casefold()


def _frame_tags(raw: Any) -> List[str]:
    """frames.tags is a comma-joined string (ingest.py writes ",".join); tolerate a
    JSON list too, for rows written by other tools."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    text = str(raw).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except ValueError:
            pass
    return [t.strip() for t in text.split(",") if t.strip()]


def _normalise_terms(terms: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for t in terms or []:
        t = (t or "").strip()
        if t and _fold(t) not in seen:
            seen.add(_fold(t))
            out.append(t)
    return out


def _snippet(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS - 1] + "…"


# ── scope ─────────────────────────────────────────────────────────────────────
def _scope_ids(conn: sqlite3.Connection, filters: Optional[str]) -> Optional[set]:
    if not (filters or "").strip():
        return None
    try:
        text, conditions = search_syntax.parse(filters)
    except search_syntax.QueryError as exc:
        raise ReportError("filters: {0}".format(exc))
    if text.strip():
        raise ReportError(
            "filters takes key:value only (tag:/camera:/lang:/rating:/type:/shot:); "
            "got free text {0!r} — put search terms in `terms`".format(text))
    if not conditions:
        return None
    return search_syntax.matching_ids(conn, conditions)


def _media_rows(conn: sqlite3.Connection, ids: Optional[set]) -> List[sqlite3.Row]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(media)").fetchall()}

    def col(name: str) -> str:
        return name if name in cols else "NULL AS {0}".format(name)

    # Audio files have no picture, so they are out of scope rather than
    # "unanalysed" — counting them there would inflate the can't-tell number.
    sql = ("SELECT id, filename, path, duration_s, {0}, {1}, {2} FROM media "
           "WHERE ext IS NULL OR LOWER(ext) NOT IN {3}").format(
        col("shot_date"), col("camera_make"), col("camera_model"),
        mediatypes.sql_in_literal(mediatypes.AUDIO_EXT))
    rows = conn.execute(sql + " ORDER BY id").fetchall()
    if ids is not None:
        rows = [r for r in rows if r["id"] in ids]
    return rows


def _frames_by_media(conn: sqlite3.Connection, ids: Sequence[int]) -> Dict[int, List[Dict[str, Any]]]:
    out: Dict[int, List[Dict[str, Any]]] = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):  # stay under SQLite's host-parameter cap
        chunk = ids[i:i + 500]
        q = ("SELECT * FROM frames WHERE media_id IN ({0}) "
             "ORDER BY media_id, frame_index").format(",".join("?" * len(chunk)))
        for r in conn.execute(q, chunk).fetchall():
            out.setdefault(r["media_id"], []).append(dict(r))
    return out


def _camera_label(row: sqlite3.Row) -> str:
    parts = [str(row[k]).strip() for k in ("camera_make", "camera_model") if row[k]]
    label = " ".join(p for p in parts if p)
    return label or _UNKNOWN


# ── core ──────────────────────────────────────────────────────────────────────
def build_exposure_report(
    terms: Sequence[str],
    filters: Optional[str] = None,
    group_by: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    _conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """The report as a dict (the JSON shape every surface returns).

    `limit`/`offset` page the per-clip `clips` list only; every total, the
    per-term breakdown and the groups are always computed over ALL hit clips.
    """
    terms = _normalise_terms(terms)
    if not terms:
        raise ReportError("at least one term is required")
    if group_by is not None and group_by not in GROUP_BY:
        raise ReportError("group_by takes {0}".format(" / ".join(GROUP_BY)))
    offset = max(0, int(offset or 0))
    folded_terms = [(t, _fold(t)) for t in terms]

    def _run(conn: sqlite3.Connection) -> Dict[str, Any]:
        ids = _scope_ids(conn, filters)
        media = _media_rows(conn, ids)
        frames = _frames_by_media(conn, [m["id"] for m in media])

        per_term = {t: {"clips": 0, "scenes": 0, "exposure_s": 0.0} for t in terms}
        clips: List[Dict[str, Any]] = []
        unanalysed = 0
        for m in media:
            fr = frames.get(m["id"], [])
            if not any((f.get("description") or "").strip() or _frame_tags(f.get("tags"))
                       for f in fr):
                unanalysed += 1
                continue
            built = scenes._build_scenes(fr, scenes._media_duration_s(dict(m)))
            hits = []
            clip_terms = set()
            for sc, f in zip(built, fr):
                tags = _frame_tags(f.get("tags"))
                hay_desc = _fold(f.get("description"))
                hay_tags = [_fold(t) for t in tags]
                matched = [t for t, ft in folded_terms
                           if ft in hay_desc or any(ft in ht for ht in hay_tags)]
                if not matched:
                    continue
                for t in matched:
                    per_term[t]["scenes"] += 1
                    per_term[t]["exposure_s"] += sc["duration_s"]
                    clip_terms.add(t)
                hits.append({
                    "scene_index": sc["scene_index"],
                    "start_s": sc["start_s"],
                    "end_s": sc["end_s"],
                    "duration_s": sc["duration_s"],
                    "matched_terms": matched,
                    "content_type": sc["content_type"],
                    "description": _snippet(f.get("description") or ""),
                    "tags": tags,
                })
            if not hits:
                continue
            for t in clip_terms:
                per_term[t]["clips"] += 1
            clips.append({
                "media_id": m["id"],
                "filename": m["filename"],
                "path": _display_path(m["path"]) if m["path"] else None,
                "shot_date": m["shot_date"],
                "camera": _camera_label(m),
                "media_duration_s": float(m["duration_s"] or 0.0),
                "hit_scenes": len(hits),
                "exposure_s": sum(h["duration_s"] for h in hits),
                "scenes": hits,
            })

        report: Dict[str, Any] = {
            "terms": terms,
            "filters": (filters or "").strip() or None,
            "method": {
                "basis": "vision_description",
                "fields": ["frames.description", "frames.tags"],
                "duration": "sum of hit scenes' duration_s (scenes._build_scenes cut)",
                "script_fold": "opencc" if zh_convert.opencc_available() else "off (exact script)",
                "note": METHOD_NOTE,
                "note_en": METHOD_NOTE_EN,
            },
            "clips_in_scope": len(media),
            "clips_unanalysed": unanalysed,
            "clips_hit": len(clips),
            "scenes_hit": sum(c["hit_scenes"] for c in clips),
            "exposure_s": sum(c["exposure_s"] for c in clips),
            "by_term": per_term,
        }
        if group_by:
            key = "shot_date" if group_by == "date" else "camera"
            groups: Dict[str, Dict[str, Any]] = {}
            for c in clips:
                g = groups.setdefault(c[key] or _UNKNOWN,
                                      {"clips": 0, "scenes": 0, "exposure_s": 0.0})
                g["clips"] += 1
                g["scenes"] += c["hit_scenes"]
                g["exposure_s"] += c["exposure_s"]
            report["group_by"] = group_by
            report["groups"] = [dict(key=k, **v) for k, v in sorted(groups.items())]
        page = clips[offset:] if limit is None else clips[offset:offset + max(0, int(limit))]
        report["clips_total"] = len(clips)
        report["offset"] = offset
        report["clips"] = page
        return report

    if _conn is not None:
        return _run(_conn)
    with db.get_conn() as conn:
        return _run(conn)


# ── rendering ─────────────────────────────────────────────────────────────────
def _fmt_s(seconds: float) -> str:
    total = float(seconds or 0.0)
    m, s = divmod(total, 60)
    h, m = divmod(int(m), 60)
    if h:
        return "{0}:{1:02d}:{2:04.1f}".format(h, m, s)
    return "{0}:{1:04.1f}".format(m, s)


def _md_cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Dict[str, Any]) -> str:
    lines = ["# 曝光報告：{0}".format(" / ".join(report["terms"])), ""]
    lines.append("> ⚠️ {0}".format(report["method"]["note"]))
    if not report["method"]["script_fold"].startswith("opencc"):
        lines.append("> ⚠️ opencc 未安裝：繁簡不互通，只比對同一字形。")
    lines.append("")
    if report.get("filters"):
        lines.append("- 範圍：`{0}`".format(report["filters"]))
    lines += [
        "- 範圍內素材：{0} 支（其中 {1} 支沒有 vision 分析，無法判定）".format(
            report["clips_in_scope"], report["clips_unanalysed"]),
        "- 命中：**{0} 支／{1} 個 scene**".format(report["clips_hit"], report["scenes_hit"]),
        "- 總曝光：**{0}**（{1:.1f} 秒）".format(_fmt_s(report["exposure_s"]), report["exposure_s"]),
        "",
        "| 詞 | 支數 | scene 數 | 曝光秒數 |",
        "|---|---:|---:|---:|",
    ]
    for t, v in report["by_term"].items():
        lines.append("| {0} | {1} | {2} | {3:.1f} |".format(
            _md_cell(t), v["clips"], v["scenes"], v["exposure_s"]))
    if report.get("groups") is not None:
        label = "拍攝日期" if report["group_by"] == "date" else "相機"
        lines += ["", "## 依{0}".format(label), "",
                  "| {0} | 支數 | scene 數 | 曝光秒數 |".format(label), "|---|---:|---:|---:|"]
        for g in report["groups"]:
            lines.append("| {0} | {1} | {2} | {3:.1f} |".format(
                _md_cell(g["key"]), g["clips"], g["scenes"], g["exposure_s"]))
    lines += ["", "## 依素材", ""]
    if not report["clips"]:
        lines.append("（沒有命中）")
    for c in report["clips"]:
        lines.append("### {0} — {1} 個 scene，{2:.1f} 秒".format(
            _md_cell(c["filename"]), c["hit_scenes"], c["exposure_s"]))
        meta = [x for x in (c.get("shot_date"), c.get("camera")) if x and x != _UNKNOWN]
        if meta:
            lines.append("_{0}_".format(" · ".join(meta)))
        lines += ["", "| 時間 | 長度 | 詞 | 類型 | 描述 |", "|---|---:|---|---|---|"]
        for h in c["scenes"]:
            lines.append("| {0}–{1} | {2:.1f}s | {3} | {4} | {5} |".format(
                _fmt_s(h["start_s"]), _fmt_s(h["end_s"]), h["duration_s"],
                _md_cell(", ".join(h["matched_terms"])), _md_cell(h["content_type"] or ""),
                _md_cell(h["description"])))
        lines.append("")
    shown = report["offset"] + len(report["clips"])
    if shown < report["clips_total"] or report["offset"]:
        lines.append("（明細顯示第 {0}–{1} 支，共 {2} 支）".format(
            report["offset"] + 1, shown, report["clips_total"]))
    return "\n".join(lines).rstrip() + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Brand/object exposure report from vision descriptions "
                    "(matched against keyframe descriptions, not frame-by-frame detection).")
    p.add_argument("terms", nargs="+", help="brand / product / object terms (any one hits)")
    p.add_argument("--filters", default="",
                   help='search-box filters narrowing the clips, e.g. "shot:2026-10 camera:fx3"')
    p.add_argument("--group-by", choices=GROUP_BY, default=None)
    p.add_argument("--format", choices=("md", "json"), default="md")
    p.add_argument("--limit", type=int, default=None, help="cap the per-clip detail list")
    p.add_argument("--output", default="", help="write to this file instead of stdout")
    p.add_argument("--db", default="", help="library database (default: the configured one)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.db:
        db.set_db_path(args.db)
    try:
        report = build_exposure_report(args.terms, filters=args.filters or None,
                                       group_by=args.group_by, limit=args.limit)
    except ReportError as exc:
        print("error: {0}".format(exc), file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print("error: database: {0}".format(exc), file=sys.stderr)
        return 3
    text = (json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.format == "json" else render_markdown(report))
    if args.output:
        # tmp + replace: an interrupted run never leaves a half-written report
        # where the previous one was (tests/test_bug_class_guards.py class C).
        tmp = "{0}.tmp".format(args.output)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, args.output)
        print(args.output)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
