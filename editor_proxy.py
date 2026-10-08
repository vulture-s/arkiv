"""Editing proxies: a ProRes Proxy `.mov` beside the source, for the NLE.

This is NOT the Inspector proxy (`ingest.generate_proxy`). That one is a 720p
H.264 named `{id}_{hash}.mp4` in `.arkiv/proxies/`, built only for codecs a
browser can't play, with one stereo track and no timecode — fine for the web UI,
useless to an editor: Resolve relinks proxies by file name, and a proxy without
the source's timecode or audio tracks can't stand in for it on a timeline.

So this writes a second, opt-in output in the layout cutting rooms already use:

    <source dir>/Proxy/<source stem>.mov   ProRes 422 Proxy, source TC, every audio track

Rules this module keeps:

- **Never overwrite.** If `Proxy/<stem>.{mov,mp4,mxf}` already exists, that file
  is the editor's (or a previous run's) and is left alone — reported as `exists`.
- **No half-written finals.** Encode to a hidden tmp in the same folder, then
  move it into place only after a clean, non-empty encode.
- **The Inspector proxy is untouched.** `/api/stream` only serves browser-playable
  candidates (#365), so a ProRes file in `Proxy/` is never picked for playback;
  `pathres.editor_proxy_for` may still use it for waveforms, behind its duration gate.

Imports config + ingest (probe only). No server import, no cycle.
"""
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import config
import mediatypes

PROXY_DIRNAME = "Proxy"
# Any of these with the same stem counts as "an editor proxy is already there".
_EXISTING_EXTS = (".mov", ".mp4", ".mxf")

EXISTS = "exists"
CREATED = "created"
FAILED = "failed"


def target_for(src: str) -> Path:
    source = Path(src)
    return source.parent / PROXY_DIRNAME / (source.stem + ".mov")


def existing_for(src: str) -> Optional[Path]:
    """The proxy already sitting in `Proxy/` for this source, any extension, or None.
    Exact `Proxy` only: that is the folder we write to, so it is the one we must
    not clobber. A non-empty file counts; an empty one is still not ours to delete."""
    source = Path(src)
    folder = source.parent / PROXY_DIRNAME
    for ext in _EXISTING_EXTS:
        candidate = folder / (source.stem + ext)
        if candidate.exists():
            return candidate
    return None


def build_cmd(src: str, dst: str, height: int, start_tc: Optional[str], hwaccel: bool) -> List[str]:
    """ffmpeg args for a ProRes 422 Proxy (`prores_ks -profile:v 0`).

    - `-map 0:v:0 -map 0:a?` keeps the picture and EVERY audio track (the Inspector
      proxy keeps one); `?` so a silent clip doesn't fail the map.
    - `-timecode` writes a tmcd track starting at the source's TC, so the proxy
      lines up with the original on a timeline. Omitted when the source has none.
    - `min(ih,H)` never upscales: a 720p source stays 720p.
    """
    cmd = [config.FFMPEG_PATH, "-y", "-hide_banner"]
    if hwaccel:
        cmd += ["-hwaccel", "videotoolbox"]
    cmd += [
        "-i", src,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "prores_ks", "-profile:v", "0",
        "-pix_fmt", "yuv422p10le",
        "-vf", "scale=-2:'min(ih,{0})'".format(int(height)),
        "-c:a", "pcm_s16le",
        "-map_metadata", "0",
    ]
    if start_tc:
        cmd += ["-timecode", start_tc]
    cmd.append(dst)
    return cmd


def _timeout_for(duration_s) -> int:
    # ProRes of a long 4K clip is not a 600 s job. Three times realtime, floored.
    try:
        return max(600, int(float(duration_s or 0) * 3))
    except (TypeError, ValueError):
        return 600


def generate(src: str, height: Optional[int] = None, hwaccel: Optional[bool] = None) -> Dict:
    """Write `Proxy/<stem>.mov` beside `src`. Returns {status, path, reason?}.

    status is `exists` (left alone), `created`, or `failed` (with a reason).
    Never raises for an ffmpeg/filesystem failure — a batch must keep going."""
    import ingest  # local: ingest is heavy and only needed when we actually encode

    if height is None:
        height = config.EDITOR_PROXY_HEIGHT
    if hwaccel is None:
        hwaccel = config.PROXY_HWDECODE_DEFAULT

    target = target_for(src)
    already = existing_for(src)
    if already is not None:
        return {"status": EXISTS, "path": str(already)}
    if not Path(src).is_file():
        return {"status": FAILED, "path": str(target), "reason": "source not found"}

    info = ingest.probe(src) or {}
    if not info.get("width"):
        return {"status": FAILED, "path": str(target), "reason": "no video stream (ffprobe)"}

    folder = target.parent
    made_folder = not folder.exists()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": FAILED, "path": str(target), "reason": "cannot create {0}: {1}".format(folder, exc)}

    # Hidden, same folder (os.replace stays on one filesystem), real .mov suffix
    # (ffmpeg picks the muxer from it).
    tmp = folder / ".{0}.arkiv-tmp.{1}.mov".format(Path(src).stem, os.getpid())

    def _cleanup():
        try:
            tmp.unlink()
        except OSError:
            pass
        if made_folder:
            try:
                folder.rmdir()  # only if still empty
            except OSError:
                pass

    def _run(use_hw: bool):
        # encoding pinned: Windows zh-TW cp950 can't decode ffmpeg's utf-8 stderr.
        return subprocess.run(
            build_cmd(src, str(tmp), height, info.get("start_tc"), use_hw),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_timeout_for(info.get("duration_s")),
        )

    try:
        r = _run(hwaccel)
        if r.returncode != 0 and hwaccel:
            try:
                tmp.unlink()
            except OSError:
                pass
            r = _run(False)
    except FileNotFoundError as exc:
        _cleanup()
        return {"status": FAILED, "path": str(target), "reason": "ffmpeg not found ({0})".format(exc)}
    except subprocess.TimeoutExpired:
        _cleanup()
        return {"status": FAILED, "path": str(target), "reason": "ffmpeg timeout"}

    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        _cleanup()
        return {"status": FAILED, "path": str(target),
                "reason": "ffmpeg rc={0}: {1}".format(r.returncode, (r.stderr or "")[-300:])}

    # Re-check right before the move: an editor (or another run) may have dropped
    # a proxy in while we were encoding. Theirs wins.
    raced = existing_for(src)
    if raced is not None:
        _cleanup()
        return {"status": EXISTS, "path": str(raced)}
    os.replace(str(tmp), str(target))
    return {"status": CREATED, "path": str(target)}


def generate_for_ids(media_ids: List[int]) -> List[Dict]:
    """Batch over indexed media. Non-video rows are skipped, not failed."""
    import db
    from pathres import _resolve_media_path

    results = []
    for mid in media_ids:
        rec = db.get_record_by_id(int(mid))
        if not rec:
            results.append({"media_id": mid, "status": FAILED, "reason": "unknown media id"})
            continue
        src = _resolve_media_path(rec["path"])
        if Path(src).suffix.lower() not in mediatypes.VIDEO_EXT:
            results.append({"media_id": mid, "status": "skipped", "reason": "not a video"})
            continue
        res = generate(src)
        res["media_id"] = mid
        print("[editor-proxy] {0} {1} {2}".format(mid, res["status"], res.get("reason", res.get("path"))))
        results.append(res)
    return results


def main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Write ProRes Proxy files to <source dir>/Proxy/ for editing.")
    ap.add_argument("ids", nargs="*", type=int, help="media ids (default with --all: every video)")
    ap.add_argument("--all", action="store_true", help="every indexed video")
    args = ap.parse_args(argv)
    ids = list(args.ids)
    if args.all:
        import db
        with db.get_conn() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM media ORDER BY id").fetchall()]
    if not ids:
        ap.error("give media ids, or --all")
    results = generate_for_ids(ids)
    failed = sum(1 for r in results if r["status"] == FAILED)
    created = sum(1 for r in results if r["status"] == CREATED)
    print("Editor proxies: {0} created, {1} already there, {2} failed".format(
        created, sum(1 for r in results if r["status"] == EXISTS), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
