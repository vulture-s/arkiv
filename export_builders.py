"""Export-format builders for the API layer.

R5-25 / round-5 #51: the APIRouter split is blocked by ~50 cross-group helpers —
a naive cut has each router do `from server import _build_metadata_csv`, and
since `server` imports the routers, that's a partially-initialized-module
ImportError. The fix is to extract the shared, server-state-free helpers into
leaf service modules that the routers (and server) import. This is the
export-format cluster: the pure builders/serializers that turn a media record
into CSV / EDL / FCPXML / SRT / VTT text (plus the timecode + framerate math they
share).

Every function here is pure except `_build_metadata_csv`, which reads the DB via
`db` — itself a leaf module (`db.py` does not import `server`), so there is no
cycle. Depends only on `json` + `db` + stdlib. server.py re-exports these names
for backward compat (existing call sites + tests referencing
`server._build_metadata_csv` etc. keep working unchanged).

NOTE: `_attachment_headers` (Content-Disposition builder) and `_log_safe`
(terminal-log sanitiser) deliberately stay in server.py — they are HTTP-response
/ logging concerns, not export-format serialisers.
"""
import json

import db


# ── CSV (DaVinci Resolve metadata) ───────────────────────────────────────────

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Defuse CSV formula injection (Excel/Sheets execute leading =/+/-/@/TAB/CR).
    DaVinci 不執行公式，但 user 在 Excel preview 會中招。Prefix 一個 single quote
    是 Excel/Sheets 標準 escape — DaVinci import 時會把整段當成字串收進 metadata。
    """
    if not value:
        return value
    if value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def _parse_frame_tags(frame_tags_value):
    """Decode frame_tags JSON list into structured fields used by CSV export.

    Real-world DB（example: 某商業案素材庫 427 rows）每筆 frame_tags 是 vision pipeline
    寫入的 JSON list，每個 frame 都是 dict：
      {description, tags, content_type, focus_score, exposure, stability,
       audio_quality, atmosphere, energy, edit_position, edit_reason}

    7.6b 第一版用 .split('\\n')[0] 把整段 JSON 當 plain text，DaVinci
    Description 就會看到 raw JSON 字串（audit Batch E F1 critical fix）。

    Returns (first_description, all_descriptions, vision_tags, content_type,
             atmosphere, energy, edit_position) — 後 4 個欄是「第一個非空 frame
    的值」用來補 media-level columns 為 NULL 的庫（Phase 8.2 hoist 還沒跑）。
    Legacy 純文字 frame_tags（早期 schema）→ 退化成 ('first line', [first line], [], …Nones)。
    """
    if not frame_tags_value:
        return ("", [], [], None, None, None, None)
    try:
        ft = json.loads(frame_tags_value)
    except (ValueError, TypeError):
        # legacy plain-text frame_tags — split on newlines so Scene retains 全段，
        # Description 取第一行（保持 7.6b 第一版的 plain-text 行為）
        lines = [ln.strip() for ln in str(frame_tags_value).splitlines() if ln.strip()]
        first = lines[0] if lines else ""
        return (first[:200], lines, [], None, None, None, None)
    if not isinstance(ft, list):
        return ("", [], [], None, None, None, None)

    descriptions = []
    tags_set = []
    seen_tags = set()
    content_type = None
    atmosphere = None
    energy = None
    edit_position = None
    for frame in ft:
        if not isinstance(frame, dict):
            continue
        d = frame.get("description")
        if isinstance(d, str) and d.strip():
            descriptions.append(d.strip())
        t = frame.get("tags")
        if isinstance(t, list):
            for tag in t:
                if isinstance(tag, str) and tag.strip() and tag not in seen_tags:
                    tags_set.append(tag.strip())
                    seen_tags.add(tag)
        # First non-empty wins for these — frames after the first usually agree
        if not content_type and isinstance(frame.get("content_type"), str):
            content_type = frame["content_type"]
        if not atmosphere and isinstance(frame.get("atmosphere"), str):
            atmosphere = frame["atmosphere"]
        if not energy and isinstance(frame.get("energy"), str):
            energy = frame["energy"]
        if not edit_position and isinstance(frame.get("edit_position"), str):
            edit_position = frame["edit_position"]

    first_desc = descriptions[0][:200] if descriptions else ""
    return (first_desc, descriptions, tags_set, content_type, atmosphere, energy, edit_position)


def _build_metadata_csv(media_ids=None) -> str:
    """Build the DaVinci Resolve metadata CSV body. Shared by GET (blob download
    for browser) and POST -to (Tauri native save dialog).

    media_ids: Optional iterable of media ids — when provided, only those rows
    are exported (audit Batch E F5: plugin import 後 download CSV 應該只含剛
    import 的 N 個 clip，不是整庫 dump 出去 — 既改善 UX 也避免不相關 transcript
    被一起 share 出去)。None = 整庫匯出（既有 Web UI 行為）。"""
    import csv
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["File Name", "Description", "Keywords", "Comments", "Scene"])

    sql = ("SELECT id, filename, transcript, frame_tags, content_type, "
           "atmosphere, energy, edit_position FROM media")
    params: list = []
    if media_ids is not None:
        ids = [int(i) for i in media_ids]
        if not ids:
            return buf.getvalue()  # empty → header only, no rows
        sql += " WHERE id IN (" + ",".join("?" * len(ids)) + ")"
        params.extend(ids)
    sql += " ORDER BY id"

    with db.get_conn() as conn:
        media_rows = conn.execute(sql, params).fetchall()
        for row in media_rows:
            tag_rows = conn.execute(
                "SELECT name FROM tags WHERE media_id=? ORDER BY name", (row["id"],)
            ).fetchall()
            tags = [t["name"] for t in tag_rows]

            # Vision JSON parsing — real source for Description / Scene + fallback for
            # media-level NULL content_type / atmosphere / energy / edit_position.
            (vision_desc, all_descs, vision_tags,
             ct_json, atmo_json, energy_json, edit_json) = _parse_frame_tags(row["frame_tags"])

            # Description: vision first-frame description, fallback transcript prefix
            desc = vision_desc or (row["transcript"].strip()[:200] if row["transcript"] else "")

            # Keywords: manual tags + vision tags + content_type. Dedup case-insensitively
            # because tags 強制 lower (db.py:340) 但 vision/content_type 帶大寫（"B-Roll"）。
            keywords = list(tags)
            seen_lower = {k.lower() for k in keywords}
            for vt in vision_tags:
                if vt.lower() not in seen_lower:
                    keywords.append(vt)
                    seen_lower.add(vt.lower())
            ct_value = row["content_type"] or ct_json
            if ct_value and ct_value.lower() not in seen_lower:
                keywords.append(ct_value)
            keyword_str = "; ".join(keywords)

            # Comments: media-level cols win, else fallback to JSON-derived
            atmo = row["atmosphere"] or atmo_json
            energy = row["energy"] or energy_json
            edit_pos = row["edit_position"] or edit_json
            comment_parts = []
            if atmo:
                comment_parts.append(f"atmosphere:{atmo}")
            if energy:
                comment_parts.append(f"energy:{energy}")
            if edit_pos:
                comment_parts.append(f"edit:{edit_pos}")
            comments = " | ".join(comment_parts)

            # Scene: 把所有 frame description 合成一段（DaVinci Smart Bin 可 contains 搜）
            scene = " | ".join(all_descs)

            writer.writerow([
                _csv_safe(row["filename"]),
                _csv_safe(desc),
                _csv_safe(keyword_str),
                _csv_safe(comments),
                _csv_safe(scene),
            ])

    return buf.getvalue()


# ── EDL / timeline (CMX3600) ─────────────────────────────────────────────────

def _edl_reel(rec, stem):
    # CMX3600 reel: ASCII only, 8 chars, no control chars (would inject EDL lines).
    # Treat blank/whitespace-only reel_name as missing → stem fallback.
    raw = rec.get("reel_name")
    value = raw.strip() if isinstance(raw, str) and raw.strip() else stem
    # Strip ASCII control chars (0x00-0x1F + 0x7F) BEFORE ASCII conversion —
    # encode("ascii", "replace") would happily pass \r\n through, letting a
    # poisoned reel_name like "A001\r\nFCM: NONAME" inject EDL header lines.
    value = "".join(c for c in value if 0x20 <= ord(c) < 0x7F or ord(c) >= 0x80)
    value = value.encode("ascii", "replace").decode("ascii")
    return value[:8].ljust(8)


def _media_streams(rec: dict):
    """(has_video, has_audio) for a record, from its probed streams. A clip with
    width/height is video; has_audio flags an audio track. Used so timeline
    exports describe audio-only clips correctly instead of claiming video."""
    has_video = bool(rec.get("width") or rec.get("height"))
    has_audio = bool(rec.get("has_audio"))
    # Degenerate row with neither flag → assume video so we still emit something.
    if not has_video and not has_audio:
        has_video = True
    return has_video, has_audio


def _edl_comment(text: str) -> str:
    """Sanitize a string for an EDL comment line. Strips ASCII control chars
    (incl. CR/LF) so a filename like "shot\\nFCM: ..." can't inject extra EDL
    lines, while keeping printable ASCII and non-ASCII (CJK filenames)."""
    if not text:
        return ""
    return "".join(c for c in text if 0x20 <= ord(c) < 0x7F or ord(c) >= 0x80)


def _subtitle_ts(seconds: float, sep: str = ",") -> str:
    """Subtitle timecode (SRT/VTT): HH:MM:SS,mmm (sep ',' for SRT, '.' for VTT)."""
    seconds = max(0.0, seconds)  # negative TC would render garbage frames
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _subtitle_text(text: str) -> str:
    """Sanitize a subtitle cue body: collapse newlines/blank lines (which would
    start a new cue) onto one line and neutralize a literal `-->` so transcript
    text can't inject a fake cue boundary/timecode."""
    if not text:
        return ""
    one_line = " ".join(text.split())  # collapses any \n / \r / blank runs
    return one_line.replace("-->", "->")


def _edl_timecode(seconds: float, fps: float, drop_frame: bool = False) -> str:
    """EDL timecode: HH:MM:SS:FF (NDF) or HH:MM:SS;FF (DF)."""
    if fps <= 0:
        fps = 30.0
    int_fps = round(fps)
    total_frames = round(seconds * fps)

    if drop_frame and int_fps in (30, 60):
        # Drop-frame: skip frame 0,1 (30p) or 0,1,2,3 (60p) each minute except every 10th
        d = 2 if int_fps == 30 else 4
        frames_per_min = int_fps * 60 - d
        frames_per_10min = frames_per_min * 10 + d

        tens = total_frames // frames_per_10min
        rem = total_frames % frames_per_10min

        if rem < int_fps * 60:
            adjusted = total_frames + d * 9 * tens
        else:
            adjusted = total_frames + d * 9 * tens + d * ((rem - int_fps * 60) // frames_per_min + 1)

        ff = adjusted % int_fps
        ss = (adjusted // int_fps) % 60
        mm = (adjusted // (int_fps * 60)) % 60
        hh = adjusted // (int_fps * 3600)
    else:
        ff = total_frames % int_fps
        remaining = total_frames // int_fps
        ss = remaining % 60
        remaining //= 60
        mm = remaining % 60
        hh = remaining // 60

    sep = ";" if drop_frame else ":"
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def _start_tc_seconds(rec: dict, clip_fps: float) -> float:
    """Parse a record's camera body start timecode (HH:MM:SS:FF) into seconds."""
    start_tc_str = rec.get("start_tc") or ""
    if not start_tc_str:
        return 0.0
    _tc = start_tc_str.replace(";", ":").split(":")
    if len(_tc) == 4:
        try:
            _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
            return _h * 3600 + _m * 60 + _s + _f / clip_fps
        except (ValueError, ZeroDivisionError):
            return 0.0
    return 0.0


def _edl_fps_warning(recs: list, tl_fps: float) -> "str | None":
    """B13: EDL comment warning when clips carry differing frame rates.

    A timeline's record/sequence TC assumes a single rate (tl_fps = the first
    clip's), so mixing rates drifts the record TC against clips that aren't at
    tl_fps (per-clip SOURCE TC stays exact — it's computed in each clip's own
    rate). Return an EDL comment line so the editor sees this on import, or None
    when every clip shares one rate. Pure (no I/O) so it's unit-testable."""
    rates = {round(float(r.get("fps") or tl_fps), 3) for r in recs}
    if len(rates) <= 1:
        return None
    listed = ", ".join(f"{r:g}" for r in sorted(rates))
    return (
        f"* WARNING: mixed frame rates ({listed}) — timeline record TC assumes "
        f"{float(tl_fps):g}; per-clip source TC preserved."
    )


# ── FCPXML ───────────────────────────────────────────────────────────────────

def _fcpxml_rational(fps: float):
    """FCPXML frameDuration numerator/denominator for a frame rate (exact for NTSC)."""
    _fps_map = {
        23.98: ("1001", "24000"), 23.976: ("1001", "24000"),
        29.97: ("1001", "30000"), 59.94: ("1001", "60000"),
    }
    rounded = round(fps, 2)
    if rounded in _fps_map:
        return _fps_map[rounded]
    return "1", str(round(fps))
