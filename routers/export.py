"""Metadata + NLE export routes (R5-25 / round-5 #51 router split).

All six export handlers, co-located so export_batch/export_to_file can call
export_media directly (an internal call, not an HTTP round-trip):
  * /api/export/metadata-csv (GET) + /api/export/metadata-csv-to (POST) —
    DaVinci Resolve metadata CSV (browser blob vs Tauri server-write);
  * /api/media/{id}/export/{fmt} (GET) — single-clip txt/srt/vtt/edl/edl-markers/
    fcpxml with an optional trim window;
  * /api/media/{id}/export-to (POST) — same, written to a caller-picked path;
  * /api/export/batch (POST) — zip of many single-clip exports;
  * /api/export/timeline/{fmt} (GET) — several clips laid end-to-end on ONE
    timeline (edl/srt/fcpxml).
_attachment_headers (RFC 6266 CJK-safe Content-Disposition) moves here with them.
The format serialisers (_subtitle_*, _edl_*, _fcpxml_rational, _build_metadata_csv)
already live in export_builders.py; path/guard helpers in pathres/webguard/reqopts.
Imports auth + db + settings + those leaf modules — no server import, no cycle.
"""
import re as _re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

import db
import settings as settings_store
from auth import require_scopes
from export_builders import (
    _build_metadata_csv,
    _edl_comment,
    _edl_fps_warning,
    _edl_reel,
    _edl_timecode,
    _fcpxml_rational,
    _media_streams,
    _start_tc_seconds,
    _subtitle_text,
    _subtitle_ts,
)
from pathres import _resolve_media_path
from reqopts import _parse_ids_query
from webguard import _assert_export_dest_safe

router = APIRouter()


@router.get("/api/export/metadata-csv")
def export_metadata_csv(
    ids: Optional[str] = None,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """DaVinci Resolve metadata CSV — File Name as match key.

    Import in Resolve: File → Import Metadata from CSV.
    Browser path: returns CSV body for blob download.

    ids query param (CSV of integers): batch-scoped export — plugin import 後
    呼叫時只想拿剛 import 的 N 個 clip 對應的 row，不要把整庫 transcript 一起
    塞給協作者（audit Batch E F5）。
    """
    media_ids = _parse_ids_query(ids)
    return Response(
        content=_build_metadata_csv(media_ids=media_ids),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="arkiv_davinci_metadata.csv"',
        },
    )


class MetadataCsvExportRequest(BaseModel):
    # dest optional: when omitted/blank, fall back to the persisted
    # export.default_dir setting (Phase 9.7 G5③) + a default filename.
    dest: Optional[str] = None
    ids: Optional[list] = None  # batch-scoped variant; None = full library


@router.post("/api/export/metadata-csv-to")
def export_metadata_csv_to(
    body: MetadataCsvExportRequest,
    # writes a file to a caller-chosen local path — gate on write, not read, so a
    # read-only token can't drop files in the operator's home dir (audit H10).
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Tauri WKWebView path: server writes CSV directly to user-picked dest.

    WKWebView 對 <a download> blob 觸發下載不可靠（Tauri docs 也建議走 fs API），
    所以 Tauri front-end 用 dialog.save 拿 path 後 POST 來這裡，由 server 直接寫
    檔；browser 端則繼續用 GET + blob download。
    body.ids 給時為 batch-scoped；不給為整庫匯出。"""
    # Phase 9.7 G5③: resolve dest from the request, else the persisted
    # export.default_dir setting (+ a default filename). A bare directory also
    # gets the default filename appended.
    raw_dest = (body.dest or "").strip()
    if not raw_dest:
        default_dir = settings_store.effective("export.default_dir")
        if not default_dir:
            raise HTTPException(400, "no dest provided and export.default_dir is unset")
        raw_dest = str(Path(default_dir) / "arkiv-metadata.csv")
    dest = Path(raw_dest).expanduser().resolve()
    if dest.is_dir() or raw_dest.endswith(("/", "\\")):
        dest = dest / "arkiv-metadata.csv"
    _assert_export_dest_safe(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    media_ids = None
    if body.ids is not None:
        try:
            media_ids = [int(i) for i in body.ids]
        except (TypeError, ValueError):
            raise HTTPException(400, "ids 必須是整數 list")
    csv_body = _build_metadata_csv(media_ids=media_ids)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        fh.write(csv_body)
    return {"ok": True, "path": str(dest), "size": dest.stat().st_size, "rows": len(media_ids) if media_ids is not None else None}


def _attachment_headers(stem: str, ext: str) -> dict:
    """Content-Disposition for a download, safe for non-ASCII (e.g. CJK) filenames.

    Starlette encodes response headers as latin-1, so a raw
    f'attachment; filename="{stem}.{ext}"' whose stem contains CJK characters
    raises UnicodeEncodeError → 500 (broke batch + single-clip export for every
    中日韓-named clip). Per RFC 6266/5987 we emit an ASCII-only `filename`
    fallback (non-ASCII + quote/backslash → "_") plus a percent-encoded
    `filename*` carrying the real UTF-8 name, which every modern client — and the
    Tauri WKWebView — prefers."""
    from urllib.parse import quote

    name = f"{stem}.{ext}"
    ascii_fallback = _re.sub(r'[^\x20-\x7e]|["\\]', "_", name)
    # safe="" so a "/" in the name is percent-encoded too — RFC 5987 ext-value
    # forbids a raw "/" (not an attr-char), and quote()'s default leaves it bare.
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{quote(name, safe='')}"
        )
    }


@router.get("/api/media/{media_id}/export/{fmt}")
def export_media(
    media_id: int,
    fmt: str,
    in_s: Optional[float] = None,
    out_s: Optional[float] = None,
    _tok: dict = Depends(require_scopes("media_read")),
):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    transcript = rec.get("transcript", "") or ""
    filename = rec.get("filename", f"media_{media_id}")
    stem = filename.rsplit(".", 1)[0]
    full_duration = rec.get("duration_s", 0) or 0

    # Normalize trim window: [trim_in, trim_out] in seconds, duration = trim_out - trim_in
    trim_in = max(0.0, float(in_s)) if in_s is not None else 0.0
    trim_out = min(full_duration, float(out_s)) if out_s is not None else full_duration
    if trim_out <= trim_in:
        trim_in, trim_out = 0.0, full_duration
    has_trim = trim_in > 0.05 or trim_out < full_duration - 0.05
    duration = trim_out - trim_in

    # TC helpers are module-level (shared with the batch-timeline endpoint).
    _ts = _subtitle_ts
    _edl_tc = _edl_timecode

    # Try to use segment-aligned timestamps if available
    import json as _json
    _seg_json = rec.get("segments_json")
    _segments = []
    if _seg_json:
        try:
            _segments = _json.loads(_seg_json)
        except Exception:
            pass

    # When trimmed, keep only segments that overlap [trim_in, trim_out] and
    # rebase their timestamps so the output starts at 0.
    if has_trim and _segments:
        trimmed = []
        for seg in _segments:
            s, e = seg.get("start", 0), seg.get("end", 0)
            if e <= trim_in or s >= trim_out:
                continue
            trimmed.append({
                **seg,
                "start": max(0.0, s - trim_in),
                "end": min(duration, e - trim_in),
            })
        _segments = trimmed

    if fmt == "txt":
        if has_trim:
            # Only text from segments within the trim window. With no segment data
            # we can't trim plain text by time, so the export is empty by design.
            content = "\n".join(seg.get("text", "").strip() for seg in _segments if seg.get("text"))
        else:
            content = transcript
        return HTMLResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "txt"),
        )

    if fmt == "srt":
        srt = ""
        if _segments:
            # Segment-aligned timestamps (precise). .get() tolerates legacy
            # segment dicts missing keys; _subtitle_text blocks cue injection.
            i = 1
            for seg in _segments:
                text = _subtitle_text(seg.get("text") or "")
                if not text:
                    continue
                srt += f"{i}\n{_ts(seg.get('start', 0) or 0)} --> {_ts(seg.get('end', 0) or 0)}\n{text}\n\n"
                i += 1
        else:
            # Fallback: evenly distributed
            lines = [l.strip() for l in transcript.split("\n") if l.strip()]
            for i, line in enumerate(lines, 1):
                t_start = (i - 1) * (duration / max(len(lines), 1))
                t_end = i * (duration / max(len(lines), 1))
                srt += f"{i}\n{_ts(t_start)} --> {_ts(t_end)}\n{_subtitle_text(line)}\n\n"
        return HTMLResponse(
            content=srt,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "srt"),
        )

    if fmt == "vtt":
        vtt = "WEBVTT\n\n"
        if _segments:
            for seg in _segments:
                text = _subtitle_text(seg.get("text") or "")
                if not text:
                    continue
                vtt += f"{_ts(seg.get('start', 0) or 0, '.')} --> {_ts(seg.get('end', 0) or 0, '.')}\n{text}\n\n"
        else:
            lines = [l.strip() for l in transcript.split("\n") if l.strip()]
            for i, line in enumerate(lines, 1):
                t_start = (i - 1) * (duration / max(len(lines), 1))
                t_end = i * (duration / max(len(lines), 1))
                vtt += f"{_ts(t_start, '.')} --> {_ts(t_end, '.')}\n{_subtitle_text(line)}\n\n"
        return HTMLResponse(
            content=vtt,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "vtt"),
        )

    if fmt in ("edl", "edl-markers"):
        # CMX3600 EDL — full clip + optional frame markers
        clip_fps = rec.get("fps") or 30.0
        # 29.97/59.94 are drop-frame by convention
        is_df = round(clip_fps, 2) in (29.97, 59.94)
        fcm = "DROP FRAME" if is_df else "NON-DROP FRAME"

        # Camera body start timecode (may not be 00:00:00:00)
        start_tc_str = rec.get("start_tc") or ""
        start_tc_offset = 0.0
        if start_tc_str:
            # Parse HH:MM:SS:FF or HH:MM:SS;FF into seconds
            _tc = start_tc_str.replace(";", ":").split(":")
            if len(_tc) == 4:
                try:
                    _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
                    start_tc_offset = _h * 3600 + _m * 60 + _s + _f / clip_fps
                except (ValueError, ZeroDivisionError):
                    start_tc_offset = 0.0

        # Source TC = camera start TC + offset into clip (shifted by trim_in when trimmed)
        src_start = _edl_tc(start_tc_offset + trim_in, clip_fps, is_df)
        src_end = _edl_tc(start_tc_offset + trim_in + duration, clip_fps, is_df)
        # Record TC = timeline position (starts at 01:00:00:00 by convention)
        rec_base = 3600.0  # 01:00:00:00
        rec_start = _edl_tc(rec_base, clip_fps, is_df)
        rec_end = _edl_tc(rec_base + duration, clip_fps, is_df)

        edl = f"TITLE: {_edl_comment(stem)}\nFCM: {fcm}\n\n"
        reel = _edl_reel(rec, stem)
        edl += f"001  {reel} V     C        {src_start} {src_end} {rec_start} {rec_end}\n"
        edl += f"* FROM CLIP NAME: {_edl_comment(filename)}\n"
        if start_tc_str:
            edl += f"* SOURCE START TC: {_edl_comment(start_tc_str)}\n"
        edl += "\n"

        if fmt == "edl-markers":
            # LOC comments — DaVinci reads these via "Import > Timeline Markers from EDL"
            colors = ["RED", "BLUE", "GREEN", "CYAN", "MAGENTA", "YELLOW", "WHITE"]
            frames = db.get_frames(media_id)
            kept = 0
            for fr in frames:
                marker_offset = fr["timestamp_s"]
                if marker_offset < trim_in or marker_offset > trim_out:
                    continue
                rtc = _edl_tc(rec_base + (marker_offset - trim_in), clip_fps, is_df)
                # Strip non-ASCII for DaVinci compatibility (no UTF-8 in EDL markers)
                desc = (fr.get("description") or f"Frame {fr['frame_index']+1}")
                desc = desc.encode("ascii", "replace").decode("ascii")[:60]
                color = colors[kept % len(colors)]
                edl += f"* LOC: {rtc} {color} {desc}\n"
                kept += 1

        return HTMLResponse(
            content=edl,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "edl"),
        )

    if fmt == "fcpxml":
        # FCPXML 1.8 — max compatibility: FCPX 10.4+, DaVinci 17+, Premiere via XtoCC
        clip_fps = rec.get("fps") or 30.0

        # Rational frame duration for FCPXML (must be exact, not rounded)
        _fps_map = {
            23.98: ("1001", "24000"), 23.976: ("1001", "24000"),
            29.97: ("1001", "30000"), 59.94: ("1001", "60000"),
        }
        rounded_fps = round(clip_fps, 2)
        if rounded_fps in _fps_map:
            _num, _den = _fps_map[rounded_fps]
        else:
            _num, _den = "1", str(round(clip_fps))

        # Drop frame for NTSC rates
        is_df = rounded_fps in (29.97, 59.94)
        tc_fmt = "DF" if is_df else "NDF"

        # Asset references the full file on disk; the timeline clip uses the trim window.
        asset_dur_frames = round(full_duration * clip_fps)
        clip_dur_frames = round(duration * clip_fps)

        # Camera body start timecode
        start_tc_str = rec.get("start_tc") or "00:00:00:00"
        start_tc_offset = 0.0
        _tc = start_tc_str.replace(";", ":").split(":")
        if len(_tc) == 4:
            try:
                _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
                start_tc_offset = _h * 3600 + _m * 60 + _s + _f / clip_fps
            except (ValueError, ZeroDivisionError):
                pass

        from xml.sax.saxutils import escape as xml_esc
        import pathlib
        # Attribute escaping must also cover the double quote (xml_esc leaves it
        # alone by default), or a filename like `cam "A".mp4` breaks name="..." /
        # src="..." — same protection the batch timeline path uses.
        _attr = lambda s: xml_esc(s, {'"': "&quot;"})

        # Build file URI with proper file:/// prefix
        raw_path = _resolve_media_path(rec.get("path", ""))
        file_uri = pathlib.PurePosixPath(raw_path.replace("\\", "/"))
        if not str(file_uri).startswith("/"):
            file_uri = pathlib.PurePosixPath("/" + str(file_uri))
        file_uri_str = _attr(f"file://{file_uri}")

        # Build marker elements from frame analysis (filter to trim window, rebase to clip start)
        markers_xml = ""
        frames = db.get_frames(media_id)
        colors = ["Blue", "Red", "Green", "Cyan", "Magenta", "Yellow", "White"]
        kept = 0
        for fr in frames:
            ts = fr["timestamp_s"]
            if ts < trim_in or ts > trim_out:
                continue
            offset_frames = round((ts - trim_in) * clip_fps)
            desc = xml_esc((fr.get("description") or f"Frame {fr['frame_index']+1}")[:60],
                           {'"': '&quot;'})
            color = colors[kept % len(colors)]
            markers_xml += f'                <marker start="{offset_frames * int(_num)}/{_den}s" duration="{_num}/{_den}s" value="{desc}" />\n'
            kept += 1

        # asset-clip start = where in the asset to begin reading (camera TC + trim_in)
        clip_start_frames = round((start_tc_offset + trim_in) * clip_fps)

        fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" frameDuration="{_num}/{_den}s" width="{rec.get('width') or 1920}" height="{rec.get('height') or 1080}" />
        <asset id="r2" name="{_attr(stem)}" src="{file_uri_str}" start="0s" duration="{asset_dur_frames * int(_num)}/{_den}s" format="r1" hasAudio="1" hasVideo="1" />
    </resources>
    <library>
        <event name="arkiv Export">
            <project name="{_attr(stem)}">
                <sequence format="r1" tcStart="0s" tcFormat="{tc_fmt}" duration="{clip_dur_frames * int(_num)}/{_den}s">
                    <spine>
                        <asset-clip ref="r2" name="{_attr(filename)}" offset="0s" duration="{clip_dur_frames * int(_num)}/{_den}s" start="{clip_start_frames * int(_num)}/{_den}s" tcFormat="{tc_fmt}">
{markers_xml}                        </asset-clip>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>"""

        return HTMLResponse(
            content=fcpxml,
            media_type="application/xml; charset=utf-8",
            headers=_attachment_headers(stem, "fcpxml"),
        )

    raise HTTPException(400, f"不支援的格式：{fmt}。請使用 srt/vtt/txt/edl/edl-markers/fcpxml")


class ExportToRequest(BaseModel):
    fmt: str
    dest: str
    in_s: Optional[float] = None
    out_s: Optional[float] = None


@router.post("/api/media/{media_id}/export-to")
def export_to_file(
    media_id: int,
    body: ExportToRequest,
    # writes a file to a caller-chosen local path → require write scope (audit H10).
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Export and write directly to a local path (for Tauri native save dialog).

    Codex Round-2 Critical fix：原本內聯一份過時的 _blocked denylist（漏掉
    ~/.ssh / ~/Library/LaunchAgents 等敏感位置），改走 _assert_export_dest_safe
    共用 helper（allowlist of approved user export roots + 副檔名白名單）。
    """
    resp = export_media(media_id, body.fmt, in_s=body.in_s, out_s=body.out_s)
    content = resp.body.decode("utf-8")
    dest = Path(body.dest).expanduser().resolve()
    _assert_export_dest_safe(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return {"ok": True, "path": str(dest), "size": dest.stat().st_size}


class BatchExportRequest(BaseModel):
    # Phase 12.4: zip several clips' per-clip exports (one subtitle/transcript
    # file each). For a single stitched timeline use /api/export/timeline.
    ids: List[int]
    fmt: str = "srt"


@router.post("/api/export/batch")
def export_batch(
    body: BatchExportRequest,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Bundle the per-clip export (`/api/media/{id}/export/{fmt}`) of many clips
    into one .zip — one file per clip. Reuses the single-clip builder verbatim so
    the formats + content stay identical. Missing ids are skipped."""
    import io
    import zipfile

    fmt = (body.fmt or "").lower()
    allowed = {"txt", "srt", "vtt", "edl", "edl-markers", "fcpxml"}
    if fmt not in allowed:
        raise HTTPException(422, "unsupported fmt: {0} (use {1})".format(fmt, "/".join(sorted(allowed))))
    if not body.ids:
        raise HTTPException(422, "ids must be a non-empty list")

    ext = "edl" if fmt == "edl-markers" else fmt
    buf = io.BytesIO()
    used: dict = {}
    written = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for mid in body.ids:
            rec = db.get_record_by_id(mid)
            if not rec:
                continue
            resp = export_media(media_id=mid, fmt=fmt, _tok=_tok)  # _tok is gate-only
            content = resp.body if isinstance(resp.body, (bytes, bytearray)) else str(resp.body).encode("utf-8")
            stem = (rec.get("filename") or "media_{0}".format(mid)).rsplit(".", 1)[0]
            arcname = "{0}.{1}".format(stem, ext)
            # de-collide duplicate stems so no file is silently overwritten
            n = used.get(arcname, 0)
            used[arcname] = n + 1
            if n:
                arcname = "{0}_{1}.{2}".format(stem, n, ext)
            zf.writestr(arcname, content)
            written += 1
    if written == 0:
        raise HTTPException(404, "none of the requested ids exist")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="arkiv-export.zip"'},
    )


@router.get("/api/export/timeline/{fmt}")
def export_timeline(
    fmt: str,
    ids: str,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Lay several clips end-to-end on ONE timeline and export it.

    Unlike /api/media/{id}/export/{fmt} (single clip), this sequences the given
    clips in the order supplied so a filmmaker can multi-select in the grid and
    drop a single EDL / FCPXML / SRT into Resolve.

    ids: comma-separated media ids, e.g. ?ids=3,1,7 — order is preserved, and a
    repeated id places the same clip twice. Mixed frame rates: the timeline uses
    the FIRST clip's rate for record/sequence timecode (EDL source TC stays in
    each clip's own rate); same-camera footage (the common case) is exact.
    """
    fmt = (fmt or "").lower()
    if fmt not in ("edl", "srt", "fcpxml"):
        raise HTTPException(400, f"批次匯出僅支援 edl / srt / fcpxml，收到：{fmt}")

    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids 必須是逗號分隔的整數，例如 ids=3,1,7")
    if not id_list:
        raise HTTPException(400, "ids 不可為空")
    if len(id_list) > 500:
        raise HTTPException(400, "一次最多 500 支")

    recs = []
    missing = []
    for mid in id_list:
        rec = db.get_record_by_id(mid)
        if rec:
            recs.append(rec)
        else:
            missing.append(mid)
    # Fail loud on ANY missing id rather than silently shipping a short timeline.
    # A clip deleted/moved between selection and export would otherwise produce a
    # timeline missing a shot with no warning (Codex review P2).
    if missing:
        raise HTTPException(404, f"找不到素材：{','.join(str(m) for m in missing)}")

    tl_fps = recs[0].get("fps") or 30.0
    tl_is_df = round(tl_fps, 2) in (29.97, 59.94)

    if fmt == "edl":
        fcm = "DROP FRAME" if tl_is_df else "NON-DROP FRAME"
        edl = f"TITLE: arkiv timeline\nFCM: {fcm}\n"
        fps_warn = _edl_fps_warning(recs, tl_fps)  # B13
        if fps_warn:
            edl += fps_warn + "\n"
        edl += "\n"
        rec_pos = 3600.0  # timeline starts at 01:00:00:00 by convention
        for i, rec in enumerate(recs, 1):
            filename = rec.get("filename", f"media_{rec.get('id')}")
            stem = filename.rsplit(".", 1)[0]
            dur = rec.get("duration_s", 0) or 0
            clip_fps = rec.get("fps") or tl_fps
            clip_is_df = round(clip_fps, 2) in (29.97, 59.94)
            src_off = _start_tc_seconds(rec, clip_fps)
            src_start = _edl_timecode(src_off, clip_fps, clip_is_df)
            src_end = _edl_timecode(src_off + dur, clip_fps, clip_is_df)
            rec_start = _edl_timecode(rec_pos, tl_fps, tl_is_df)
            rec_end = _edl_timecode(rec_pos + dur, tl_fps, tl_is_df)
            reel = _edl_reel(rec, stem)
            has_vid, _ = _media_streams(rec)
            chan = "V" if has_vid else "A"  # audio-only clip → audio channel
            edl += f"{i:03d}  {reel} {chan}     C        {src_start} {src_end} {rec_start} {rec_end}\n"
            edl += f"* FROM CLIP NAME: {_edl_comment(filename)}\n"
            if rec.get("start_tc"):
                edl += f"* SOURCE START TC: {_edl_comment(rec['start_tc'])}\n"
            edl += "\n"
            rec_pos += dur
        return HTMLResponse(
            content=edl,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="arkiv-timeline.edl"'},
        )

    if fmt == "srt":
        import json as _json
        srt = ""
        idx = 1
        offset = 0.0  # cumulative timeline position in seconds
        for rec in recs:
            dur = rec.get("duration_s", 0) or 0
            segs = []
            if rec.get("segments_json"):
                try:
                    segs = _json.loads(rec["segments_json"])
                except Exception:
                    segs = []
            if segs:
                for seg in segs:
                    s = offset + (seg.get("start", 0) or 0)
                    e = offset + (seg.get("end", 0) or 0)
                    text = _subtitle_text(seg.get("text") or "")
                    if not text:
                        continue
                    srt += f"{idx}\n{_subtitle_ts(s)} --> {_subtitle_ts(e)}\n{text}\n\n"
                    idx += 1
            else:
                # No segment timestamps (legacy rows / segmentless transcription):
                # mirror the single-clip /export/srt fallback and distribute the
                # transcript lines evenly across the clip, offset onto the timeline
                # (Codex review P2 — otherwise transcript-only clips vanish).
                lines = [l.strip() for l in (rec.get("transcript") or "").split("\n") if l.strip()]
                n = max(len(lines), 1)
                for li, line in enumerate(lines):
                    s = offset + li * (dur / n)
                    e = offset + (li + 1) * (dur / n)
                    srt += f"{idx}\n{_subtitle_ts(s)} --> {_subtitle_ts(e)}\n{_subtitle_text(line)}\n\n"
                    idx += 1
            offset += dur
        return HTMLResponse(
            content=srt,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="arkiv-timeline.srt"'},
        )

    # fmt == "fcpxml"
    from xml.sax.saxutils import escape as xml_esc
    import pathlib
    # Attribute escaping must also cover the double quote, or a filename like
    # `cam "A".mp4` breaks the name="..." attribute → malformed XML (Codex P2).
    _attr = lambda s: xml_esc(s, {'"': "&quot;"})
    _num, _den = _fcpxml_rational(tl_fps)
    tc_fmt = "DF" if tl_is_df else "NDF"

    assets_xml = ""
    spine_xml = ""
    offset_frames = 0
    total_frames = 0
    for i, rec in enumerate(recs):
        ref = f"r{i + 2}"  # r1 is the format
        filename = rec.get("filename", f"media_{rec.get('id')}")
        stem = filename.rsplit(".", 1)[0]
        dur = rec.get("duration_s", 0) or 0
        clip_fps = rec.get("fps") or tl_fps
        # Every asset references format r1 (the timeline rate), so ALL durations
        # and offsets must be expressed in the timeline timebase — otherwise a
        # mixed-rate clip's frame count would be serialized against the wrong
        # frameDuration and decode to the wrong seconds (Codex review P2). The
        # start TC string is parsed with the clip's own fps (correct), then
        # converted to timeline frames. asset duration == clip duration so the
        # asset is never shorter than the span the asset-clip reads.
        dur_frames = round(dur * tl_fps)
        asset_dur_frames = dur_frames
        src_off_frames = round(_start_tc_seconds(rec, clip_fps) * tl_fps)

        raw_path = _resolve_media_path(rec.get("path", ""))
        file_uri = pathlib.PurePosixPath(raw_path.replace("\\", "/"))
        if not str(file_uri).startswith("/"):
            file_uri = pathlib.PurePosixPath("/" + str(file_uri))
        file_uri_str = _attr(f"file://{file_uri}")

        # asset.start = the media's own start timecode (camera TC). The asset
        # therefore spans [src_off, src_off + duration], so the asset-clip's
        # start=src_off below sits at the head of that range rather than hours
        # past the end of a 0s-anchored asset (Codex review P2).
        has_vid, has_aud = _media_streams(rec)
        assets_xml += (
            f'        <asset id="{ref}" name="{_attr(stem)}" src="{file_uri_str}" '
            f'start="{src_off_frames * int(_num)}/{_den}s" '
            f'duration="{asset_dur_frames * int(_num)}/{_den}s" '
            f'format="r1" hasAudio="{1 if has_aud else 0}" hasVideo="{1 if has_vid else 0}" />\n'
        )
        spine_xml += (
            f'                    <asset-clip ref="{ref}" name="{_attr(filename)}" '
            f'offset="{offset_frames * int(_num)}/{_den}s" '
            f'duration="{dur_frames * int(_num)}/{_den}s" '
            f'start="{src_off_frames * int(_num)}/{_den}s" tcFormat="{tc_fmt}" />\n'
        )
        offset_frames += dur_frames
        total_frames += dur_frames

    w = recs[0].get("width") or 1920
    h = recs[0].get("height") or 1080
    fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" frameDuration="{_num}/{_den}s" width="{w}" height="{h}" />
{assets_xml}    </resources>
    <library>
        <event name="arkiv Export">
            <project name="arkiv timeline">
                <sequence format="r1" tcStart="0s" tcFormat="{tc_fmt}" duration="{total_frames * int(_num)}/{_den}s">
                    <spine>
{spine_xml}                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>"""
    return HTMLResponse(
        content=fcpxml,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="arkiv-timeline.fcpxml"'},
    )
