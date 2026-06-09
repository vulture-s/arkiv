from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import shutil
from datetime import date as _date
from io import StringIO
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import config
import db


CAMERA_REPORT_COLS = [
    "Filename",
    "Reel",
    "Scene",
    "Take",
    "TC-in",
    "TC-out",
    "Duration",
    "Camera",
    "Lens",
    "Codec",
    "ISO",
    "WB",
    "ND",
    "Shutter",
    "Aperture",
    "Focal",
    "Focus",
    "Notes",
    "Rating",
    "FPS",
]

_CODEC_BY_EXT = {
    ".mp4": "MP4",
    ".mov": "MOV",
    ".mxf": "MXF",
    ".avi": "AVI",
    ".mkv": "MKV",
}

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text and text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _default_output_path(date_text: str) -> Path:
    return config.PROJECT_ROOT / "reports" / ("camera-report-%s.csv" % date_text)


def _parse_date(date_text: str) -> _date:
    return _date.fromisoformat(date_text)


def _format_duration(seconds: Optional[float]) -> str:
    total = int(round(float(seconds or 0.0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return "%02d:%02d:%02d" % (hours, minutes, secs)


def _is_drop_frame_fps(fps: float) -> bool:
    return abs(fps - 29.97) < 0.02 or abs(fps - 59.94) < 0.02


def _timecode_to_frames(tc_text: Optional[str], fps: float) -> int:
    if not tc_text:
        return 0
    raw = str(tc_text).strip()
    if not raw:
        return 0
    drop_frame = ";" in raw
    parts = raw.replace(";", ":").split(":")
    if len(parts) != 4:
        return 0
    try:
        hh, mm, ss, ff = [int(part) for part in parts]
    except ValueError:
        return 0
    nominal_fps = int(round(fps)) or 30
    base_frames = ((hh * 3600) + (mm * 60) + ss) * nominal_fps + ff
    if not drop_frame or not _is_drop_frame_fps(fps):
        return base_frames
    drop_frames = 2 if nominal_fps == 30 else 4 if nominal_fps == 60 else 0
    if drop_frames == 0:
        return base_frames
    total_minutes = hh * 60 + mm
    dropped = drop_frames * (total_minutes - (total_minutes // 10))
    return base_frames - dropped


def _frames_to_timecode(frames: int, fps: float, drop_frame: bool) -> str:
    nominal_fps = int(round(fps)) or 30
    if not drop_frame or not _is_drop_frame_fps(fps):
        total_seconds, ff = divmod(max(frames, 0), nominal_fps)
        hh, remainder = divmod(total_seconds, 3600)
        mm, ss = divmod(remainder, 60)
        return "%02d:%02d:%02d:%02d" % (hh, mm, ss, ff)

    drop_frames = 2 if nominal_fps == 30 else 4 if nominal_fps == 60 else 0
    if drop_frames == 0:
        total_seconds, ff = divmod(max(frames, 0), nominal_fps)
        hh, remainder = divmod(total_seconds, 3600)
        mm, ss = divmod(remainder, 60)
        return "%02d:%02d:%02d:%02d" % (hh, mm, ss, ff)

    frames_per_10_minutes = nominal_fps * 60 * 10 - drop_frames * 9
    frames_per_minute = nominal_fps * 60 - drop_frames
    frames = max(frames, 0)
    ten_chunks, remainder = divmod(frames, frames_per_10_minutes)
    minutes = ten_chunks * 10

    if remainder >= drop_frames:
        remainder += drop_frames * 9
        extra_minutes, remainder = divmod(remainder, frames_per_minute)
        minutes += extra_minutes
    hours, minutes = divmod(minutes, 60)
    seconds, ff = divmod(remainder, nominal_fps)
    return "%02d:%02d:%02d;%02d" % (hours, minutes, seconds, ff)


def _format_timecode_out(start_tc: Optional[str], duration_s: Optional[float], fps: float, tc_format: str) -> str:
    if not start_tc:
        return ""
    frames = _timecode_to_frames(start_tc, fps)
    frames += int(round(float(duration_s or 0.0) * fps))
    return _frames_to_timecode(frames, fps, tc_format == "df")


def _first_regex_match(pattern_text: Optional[str], filename: str) -> str:
    if not pattern_text:
        return ""
    try:
        pattern = re.compile(pattern_text)
    except re.error:
        return ""
    match = pattern.search(filename)
    if not match:
        return ""
    if match.groups():
        for group in match.groups():
            if group is not None and str(group).strip():
                return str(group)
    return match.group(0)


def _probe_codec(media_path: Optional[str]) -> str:
    if not media_path:
        return ""
    probe = config.FFPROBE_PATH
    if not probe:
        return ""
    path_text = str(media_path)
    if not path_text:
        return ""
    try:
        proc = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nk=1:nw=1",
                path_text,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    codec_name = (proc.stdout or "").strip()
    return codec_name.upper() if codec_name else ""


def _codec_from_ext(ext: Optional[str], media_path: Optional[str] = None) -> str:
    if not ext:
        return _probe_codec(media_path)
    normalized = str(ext).strip().lower()
    if not normalized:
        return _probe_codec(media_path)
    if not normalized.startswith("."):
        normalized = "." + normalized
    mapped = _CODEC_BY_EXT.get(normalized)
    if mapped:
        return mapped
    probed = _probe_codec(media_path)
    return probed or normalized.lstrip(".").upper()


def _camera_label(row: sqlite3.Row) -> str:
    parts = []
    make = row["camera_make"] if "camera_make" in row.keys() else None
    model = row["camera_model"] if "camera_model" in row.keys() else None
    if make:
        parts.append(str(make).strip())
    if model:
        parts.append(str(model).strip())
    return " ".join(part for part in parts if part)


def _lens_label(row: sqlite3.Row) -> str:
    lens = row["lens_model"] if "lens_model" in row.keys() else None
    return str(lens).strip() if lens else ""


def _note_label(row: sqlite3.Row) -> str:
    parts = []
    note = row["rating_note"] if "rating_note" in row.keys() else None
    if note and str(note).strip():
        parts.append(str(note).strip())
    frame_tags = row["frame_tags"] if "frame_tags" in row.keys() else None
    if frame_tags:
        try:
            parsed = json.loads(frame_tags)
        except Exception:
            parsed = []
        if isinstance(parsed, list) and parsed:
            first = parsed[0]
            if isinstance(first, dict):
                desc = first.get("description")
                if desc and str(desc).strip():
                    parts.append(str(desc).strip())
    return " | ".join(parts)


def _resolve_reel(row: sqlite3.Row) -> str:
    reel = row["reel_name"] if "reel_name" in row.keys() else None
    if reel and str(reel).strip():
        return str(reel).strip()
    filename = str(row["filename"] or "")
    stem = Path(filename).stem
    if stem:
        return stem[:8]
    path_value = str(row["path"] or "")
    if path_value:
        parent = Path(path_value).parent.name
        if parent:
            return parent
    return ""


def _rating_label(row: sqlite3.Row) -> str:
    rating = row["rating"] if "rating" in row.keys() else None
    if not rating:
        return ""
    text = str(rating).strip().lower()
    if text in ("good", "ng", "review"):
        return text.upper()
    return str(rating).strip().upper()


def _summary_totals(rows: Sequence[sqlite3.Row]) -> Tuple[int, float, int, int, int]:
    total_clips = len(rows)
    total_duration = 0.0
    good = 0
    ng = 0
    review = 0
    for row in rows:
        duration = row["duration_s"] if "duration_s" in row.keys() else None
        total_duration += float(duration or 0.0)
        rating = str(row["rating"]).strip().lower() if row["rating"] else ""
        if rating == "good":
            good += 1
        elif rating == "ng":
            ng += 1
        elif rating == "review":
            review += 1
    return total_clips, total_duration, good, ng, review


def _unique_preserve(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _load_rows(date_text: str) -> List[sqlite3.Row]:
    sql = """
        SELECT filename, path, reel_name, start_tc, duration_s, camera_make, camera_model,
               lens_model, ext, iso, NULL AS white_balance, shutter_speed, aperture, focal_length,
               focus_score, rating_note, rating, fps, frame_tags, processed_at
          FROM media
         WHERE DATE(processed_at) = ?
         ORDER BY reel_name, filename
    """
    with db.get_conn() as conn:
        rows = conn.execute(sql, (date_text,)).fetchall()
    return rows


def build_camera_report_rows(
    date_text: str,
    project: Optional[str] = None,
    dp: Optional[str] = None,
    include_summary: bool = True,
    tc_format: str = "ndf",
    scene_pattern: Optional[str] = None,
    take_pattern: Optional[str] = None,
):
    _parse_date(date_text)
    rows = _load_rows(date_text)
    if not rows:
        raise LookupError("no data for date")

    resolved_rows = []
    for row in rows:
        filename = str(row["filename"] or "")
        reel = _resolve_reel(row)
        scene = _first_regex_match(scene_pattern, filename)
        take = _first_regex_match(take_pattern, filename)
        fps = float(row["fps"] or 0.0)
        duration = row["duration_s"] if "duration_s" in row.keys() else None
        resolved_rows.append([
            _csv_safe(filename),
            _csv_safe(reel),
            _csv_safe(scene),
            _csv_safe(take),
            _csv_safe(row["start_tc"] or ""),
            _csv_safe(_format_timecode_out(row["start_tc"], duration, fps, tc_format)),
            _csv_safe(_format_duration(duration)),
            _csv_safe(_camera_label(row)),
            _csv_safe(_lens_label(row)),
            _csv_safe(_codec_from_ext(row["ext"] if "ext" in row.keys() else None, row["path"])),
            _csv_safe(row["iso"] if "iso" in row.keys() else None),
            _csv_safe(row["white_balance"] if "white_balance" in row.keys() else None),
            _csv_safe(""),
            _csv_safe(row["shutter_speed"] if "shutter_speed" in row.keys() else None),
            _csv_safe(row["aperture"] if "aperture" in row.keys() else None),
            _csv_safe(row["focal_length"] if "focal_length" in row.keys() else None),
            _csv_safe(row["focus_score"] if "focus_score" in row.keys() else None),
            _csv_safe(_note_label(row)),
            _csv_safe(_rating_label(row)),
            _csv_safe(fps if row["fps"] is not None else ""),
        ])

    total_clips, total_duration, good, ng, review = _summary_totals(rows)
    reel_list = _unique_preserve([_resolve_reel(row) for row in rows])
    camera_list = _unique_preserve([_camera_label(row) for row in rows])
    header_rows = [
        ["Project", _csv_safe(project or "")],
        ["Date", _csv_safe(date_text)],
        ["DP", _csv_safe(dp or "")],
        ["Cameras", _csv_safe(" + ".join(camera_list))],
        ["Total Reels", _csv_safe(len(reel_list))],
    ]
    footer_rows = [
        ["Total Clips", _csv_safe(total_clips)],
        ["Total Duration", _csv_safe(_format_duration(total_duration))],
        ["GOOD", _csv_safe(good)],
        ["NG", _csv_safe(ng)],
        ["REVIEW", _csv_safe(review)],
        ["Reel List", _csv_safe(" ".join(reel_list))],
    ]

    output_rows = []
    if include_summary:
        output_rows.extend(header_rows)
        output_rows.append([])
    output_rows.append(CAMERA_REPORT_COLS)
    output_rows.extend(resolved_rows)
    if include_summary:
        output_rows.append([])
        output_rows.extend(footer_rows)
    return output_rows


def render_camera_report_csv(
    date_text: str,
    project: Optional[str] = None,
    dp: Optional[str] = None,
    include_summary: bool = True,
    tc_format: str = "ndf",
    scene_pattern: Optional[str] = None,
    take_pattern: Optional[str] = None,
):
    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in build_camera_report_rows(
        date_text=date_text,
        project=project,
        dp=dp,
        include_summary=include_summary,
        tc_format=tc_format,
        scene_pattern=scene_pattern,
        take_pattern=take_pattern,
    ):
        writer.writerow(row)
    return buf.getvalue()


def write_camera_report(
    date_text: str,
    output: Optional[Path] = None,
    project: Optional[str] = None,
    dp: Optional[str] = None,
    include_summary: bool = True,
    tc_format: str = "ndf",
    scene_pattern: Optional[str] = None,
    take_pattern: Optional[str] = None,
):
    dest = Path(output) if output is not None else _default_output_path(date_text)
    dest = dest.expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    csv_text = render_camera_report_csv(
        date_text=date_text,
        project=project,
        dp=dp,
        include_summary=include_summary,
        tc_format=tc_format,
        scene_pattern=scene_pattern,
        take_pattern=take_pattern,
    )
    with dest.open("w", encoding="utf-8", newline="") as fh:
        fh.write(csv_text)
    return dest


def build_parser():
    parser = argparse.ArgumentParser(description="Generate arkiv camera report CSV")
    parser.add_argument("--date", required=True, help="Processed date in YYYY-MM-DD")
    parser.add_argument("--project", default="", help="Project name for the header block")
    parser.add_argument("--dp", default="", help="DP name for the header block")
    parser.add_argument(
        "--output",
        default="",
        help="CSV output path (default: $PROJECT_ROOT/reports/camera-report-<date>.csv)",
    )
    parser.add_argument(
        "--include-summary",
        action="store_true",
        default=True,
        help="Include the metadata header block and summary footer",
    )
    parser.add_argument(
        "--no-include-summary",
        action="store_false",
        dest="include_summary",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--tc-format",
        choices=("ndf", "df"),
        default="ndf",
        help="Timecode output format",
    )
    parser.add_argument("--scene-pattern", default="", help="Regex for scene extraction from filename")
    parser.add_argument("--take-pattern", default="", help="Regex for take extraction from filename")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output = Path(args.output).expanduser() if args.output else None
        dest = write_camera_report(
            date_text=args.date,
            output=output,
            project=args.project,
            dp=args.dp,
            include_summary=args.include_summary,
            tc_format=args.tc_format,
            scene_pattern=args.scene_pattern or None,
            take_pattern=args.take_pattern or None,
        )
    except LookupError:
        return 1
    except (OSError, sqlite3.Error, ValueError):
        return 3
    print(str(dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
