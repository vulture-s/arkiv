"""Camera-motion measurement: what the CAMERA is doing, per scene. Zero VLM.

Why this exists
---------------
A client brief like "pan left-to-right so every audience member is in shot" is a
condition on camera movement. Every existing per-scene field (description,
content_type, stability, ...) describes what is IN the frame; none can answer
"which clips pan?" or "which of these three near-identical takes is the shaky
one?" — vision models describe all three takes with the same sentence.

Method (ported from the smart-edit experiment `camera_motion.py`, same author)
------------------------------------------------------------------------------
Decode at 2 fps, scaled to 160 px wide, grayscale. Collapse each frame to a 1-D
horizontal projection (mean of every column). Between consecutive frames:

- dx: horizontal shift (px in 160-px-wide space). Positive = content moved
  right = camera panned LEFT.
- dz: scale change (% per step). Positive = zoom IN.

Both come from ONE joint search over (shift, scale) minimising the mean absolute
residual of the two projections (see _step for why joint, not the experiment's
shift-then-scale).

Per window (a scene) those steps reduce to six numbers:

- pan     = sum|dx| / seconds   — how much it travels
- net     = sum(dx) / seconds   — signed travel (direction)
- jitter  = mean|diff(dx)|      — how much the direction keeps changing
- zoom / znet / zjitter         — the same three on the scale axis

pan and jitter are deliberately separate: a steady pan and handheld shake can
travel the same distance, and one "how much does it move" number cannot tell
them apart (field data: clean pan net 8.14 / jitter 1.77 vs. shake net 11.65 /
jitter 11.12). Likewise zjitter is the trust signal for znet: a whip pan puts a
large fake value on the scale axis (znet -6.62 with zjitter 15.83).

Only ffmpeg + numpy; no OpenCV. numpy is imported lazily so importing this leaf
(scenes.py does, to render the stored value) costs nothing.
"""
import json
import subprocess
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

FPS = 2       # two samples a second is enough for the trend; decoding is the cost
WIDTH = 160   # projection width — the unit of every dx/pan/net/jitter value
HEIGHT = 90   # fixed: the horizontal projection does not care about aspect, and a
              # fixed height means no ffprobe call (and no multi-video-stream trap)
MAX_SHIFT = 24
MAX_ZOOM_PCT = 25
SCHEMA_VERSION = 1

# Classification thresholds — the values the experiment settled on against
# hand-labelled field footage (smart-edit edit_pipeline.is_pan / is_zoom).
PAN_MIN_NET = 2.0       # |net| px/s (160-px space) to count as a deliberate pan
PAN_MAX_JITTER = 6.0    # above this it is shake, not a pan
ZOOM_MIN_ZNET = 3.0     # |znet| %/s to count as a push-in / pull-out
ZOOM_MAX_ZJITTER = 4.0  # above this the scale signal is not trustworthy

METRIC_KEYS = ("pan", "net", "jitter", "zoom", "znet", "zjitter")


def _keyframe_dense(path: str, ffprobe: str = "ffprobe") -> bool:
    """True when the first seconds show a keyframe at least every 1/FPS s.

    Camera long-GOP (e.g. Sony XAVC S at 29.97p: a keyframe every 0.5 s) already
    has one decodable picture per sample slot, so `-skip_frame nokey` gives the
    same 2 fps grid without decoding the P/B frames in between — measured 54.6 s
    -> 18.7 s on a 66 s 4K 10-bit 4:2:2 clip. Sparse-GOP sources (phones, screen
    recordings) fail this test and take the full decode."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
             "-show_entries", "frame=pts_time", "-of", "csv=p=0",
             "-read_intervals", "%+4", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    ts = []
    for line in out.splitlines():
        v = line.strip().strip(",")
        try:
            ts.append(float(v))
        except ValueError:
            continue
    if len(ts) < 3:
        return False
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    return max(gaps) <= 1.0 / FPS + 0.02


def _projections(path: str, ffmpeg: str = "ffmpeg",
                 timeout_s: float = 600.0, keyframes_only: bool = False) -> List[Any]:
    """One 1-D column-mean projection (float64[WIDTH]) per sampled frame.

    Streamed frame by frame so a long clip never holds its raw decode in RAM.
    Returns [] when ffmpeg yields nothing usable (audio-only, unreadable)."""
    import numpy as np
    skip = ["-skip_frame", "nokey"] if keyframes_only else []
    cmd = [ffmpeg, "-v", "error", "-nostdin"] + skip + ["-i", path, "-map", "0:v:0",
           "-an", "-sn", "-vf",
           "fps={0},scale={1}:{2},format=gray".format(FPS, WIDTH, HEIGHT),
           "-f", "rawvideo", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return []
    timer = threading.Timer(timeout_s, proc.kill)
    timer.start()
    frame_bytes = WIDTH * HEIGHT
    cols = []
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            a = np.frombuffer(buf, dtype=np.uint8).reshape(HEIGHT, WIDTH)
            cols.append(a.mean(axis=0))
    finally:
        timer.cancel()
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()
    return cols


_GRID = None


def _grid():
    """Candidate (scale, shift) sampling positions, built once: (P, D, WIDTH)."""
    global _GRID
    if _GRID is None:
        import numpy as np
        n = WIDTH
        xc = np.arange(n) - (n - 1) / 2.0
        shifts = np.arange(-MAX_SHIFT, MAX_SHIFT + 1)
        pcts = np.arange(-MAX_ZOOM_PCT, MAX_ZOOM_PCT + 1)
        scales = 1.0 + pcts / 100.0
        pos = xc[None, None, :] / scales[:, None, None] + (n - 1) / 2.0 + shifts[None, :, None]
        inside = (pos >= 0) & (pos <= n - 1)
        cnt = inside.sum(-1)
        # Tiny bias toward "no motion" so exact ties (flat frames) resolve to 0/0.
        bias = 1e-9 * (np.abs(pcts)[:, None] + np.abs(shifts)[None, :])
        _GRID = (pos, inside, np.maximum(cnt, 1), cnt >= n * 0.6, bias, shifts, pcts)
    return _GRID


def _step(c0, c1) -> Tuple[int, float]:
    """(dx, dz) between two projections: the joint (shift, scale) that best maps
    c1 onto c0, by minimum mean |residual| over the overlap.

    dx > 0 = content moved right (camera panned left), px in WIDTH space.
    dz > 0 = zoom in, % per step.

    Joint rather than the experiment's sequential "shift first, then scale":
    on a synthetic pure push-in the sequential search explained the first few
    small-scale steps as a 3-px shift (an off-centre feature under a centred zoom
    moves sideways) and the clip read as a 2.36 px/s pan — over PAN_MIN_NET. The
    joint search reads 0 shift on every step there and is identical on the pure
    pan / static / shake controls. Vectorised, it costs ~4 ms a step."""
    import numpy as np
    pos, inside, cnt, valid, bias, shifts, pcts = _grid()
    r = np.interp(pos.ravel(), np.arange(len(c1)), c1).reshape(pos.shape)
    err = np.where(inside, np.abs(r - c0[None, None, :]), 0.0).sum(-1) / cnt
    err = np.where(valid, err + bias, np.inf)
    i, j = np.unravel_index(int(np.argmin(err)), err.shape)
    # Searched: c0(x) = c1(x/s + d). A push-in magnifies c1, so it must be
    # shrunk (s < 1, pct < 0) to match — hence the sign flip.
    return int(shifts[j]), -float(pcts[i])


def steps(path: str, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
          timeout_s: float = 600.0) -> List[Tuple[float, int, float]]:
    """Per-step motion for the whole clip: [(t_start, dx, dz), ...], one per pair
    of consecutive samples. One decode, then any number of windows."""
    cols = _projections(path, ffmpeg=ffmpeg, timeout_s=timeout_s,
                        keyframes_only=_keyframe_dense(path, ffprobe=ffprobe))
    out = []
    for i in range(len(cols) - 1):
        d, z = _step(cols[i], cols[i + 1])
        out.append((i / float(FPS), d, z))
    return out


def summarize(dxs: Sequence[float], dzs: Sequence[float]) -> Optional[Dict[str, Any]]:
    """Reduce a window's steps to the six metrics. None when there are fewer than
    two steps — jitter is undefined there, and "not measured" must stay distinct
    from "measured, no movement"."""
    import numpy as np
    if len(dxs) < 2:
        return None
    dur = len(dxs) / float(FPS)
    return {
        "pan": round(float(np.abs(dxs).sum()) / dur, 2),
        "net": round(float(np.sum(dxs)) / dur, 2),
        "jitter": round(float(np.abs(np.diff(dxs)).mean()), 2),
        "zoom": round(float(np.abs(dzs).sum()) / dur, 2),
        "znet": round(float(np.sum(dzs)) / dur, 2),
        "zjitter": round(float(np.abs(np.diff(dzs)).mean()), 2),
        "steps": len(dxs),
    }


def per_scene(step_list: Sequence[Tuple[float, int, float]],
              bounds: Sequence[Tuple[float, float]]) -> List[Optional[Dict[str, Any]]]:
    """Assign each step to the scene containing its midpoint, then summarize.
    `bounds` = [(start_s, end_s), ...] in scene order (scenes.py's cut)."""
    half = 0.5 / FPS
    out = []
    for start, end in bounds:
        sel = [(d, z) for (t, d, z) in step_list if start <= t + half < end]
        out.append(summarize([d for d, _ in sel], [z for _, z in sel]))
    return out


def scene_bounds(frames: Sequence[Dict[str, Any]],
                 media_duration_s: float) -> List[Tuple[float, float]]:
    """The same boundaries scenes._build_scenes derives: each frame opens a scene
    that closes at the next frame's timestamp (or the media duration)."""
    out = []
    for i, f in enumerate(frames):
        start = float(f["timestamp_s"])
        end = float(frames[i + 1]["timestamp_s"]) if i + 1 < len(frames) else float(media_duration_s or 0.0)
        out.append((start, max(start, end)))
    return out


def measure_scenes(path: str, frames: Sequence[Dict[str, Any]],
                   media_duration_s: float, ffmpeg: str = "ffmpeg",
                   ffprobe: str = "ffprobe") -> List[Optional[Dict[str, Any]]]:
    """Clip path + its scene frames → one metrics dict (or None) per frame."""
    timeout = max(120.0, float(media_duration_s or 0.0) * 2.0)
    st = steps(path, ffmpeg=ffmpeg, ffprobe=ffprobe, timeout_s=timeout)
    return per_scene(st, scene_bounds(frames, media_duration_s))


def to_db(metrics: Optional[Dict[str, Any]]) -> Optional[str]:
    if metrics is None:
        return None
    d = dict(metrics)
    d["v"] = SCHEMA_VERSION
    return json.dumps(d, sort_keys=True)


def classify(m: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Derived, threshold-based labels.

    pan_dir: "left" / "right" (camera direction) when |net| clears PAN_MIN_NET and
    jitter stays under PAN_MAX_JITTER, else None. net > 0 means content moved
    right, i.e. the camera panned left.
    zoom_dir: "in" / "out" under the analogous znet / zjitter rule."""
    pan_dir = None
    if abs(m.get("net", 0.0)) >= PAN_MIN_NET and m.get("jitter", 99.0) <= PAN_MAX_JITTER:
        pan_dir = "left" if m["net"] > 0 else "right"
    zoom_dir = None
    if abs(m.get("znet", 0.0)) >= ZOOM_MIN_ZNET and m.get("zjitter", 99.0) <= ZOOM_MAX_ZJITTER:
        zoom_dir = "in" if m["znet"] > 0 else "out"
    return {"pan_dir": pan_dir, "zoom_dir": zoom_dir}


def from_db(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Stored JSON → the public per-scene `camera_motion` object, or None.

    Fixed key order (the scenes body is a byte-frozen contract): the six
    metrics, then the two derived labels. Unparseable / foreign rows read as
    None (omitted) rather than raising inside a read endpoint."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or any(k not in d for k in METRIC_KEYS):
        return None
    out = {k: d[k] for k in METRIC_KEYS}
    out.update(classify(out))
    return out
