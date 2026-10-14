"""Camera log profiles → Rec.709 preview LUTs.

Built-in LUTs are GENERATED here from each vendor's published transfer function
and gamut primaries — no vendor .cube file is copied or shipped (their
redistribution terms are unstated). The result is a *technical* conversion for
browsing (log decode → gamut matrix → compressive tone curve → BT.709 OETF), not
the vendor's creative look, so it will not match a vendor LUT pixel for pixel.

User LUTs: any ``*.cube`` dropped into ``USER_LUT_DIR`` (``~/.arkiv-luts`` or
``$ARKIV_LUT_DIR``) is listed as profile ``user:<file stem>`` and applied as-is.

Input convention: every curve takes the 10-bit code value / 1023 (the vendors'
own convention; the legal-range offsets live inside the constants). The ffmpeg
chain therefore reads the source as full range before the LUT — see
``ffmpeg_lut_filter``.

Sources (constants transcribed from these; spot values are unit-tested):
  Sony     Technical Summary S-Gamut3.Cine/S-Log3 & S-Gamut3/S-Log3 V1.00
  DJI      White Paper on D-Log and D-Gamut (2017; X9 paper same math)
  Insta360 I-Log White Paper (2026-06; curve revised 2025-07 "10 bit container")
  Canon    Input Transform v202007 (C-Log2/C-Log3 "v1.2"), Cinema Gamut primaries
  Nikon    N-Log Specification v1.0.0 (2018); gamut = BT.2020
  Fujifilm F-Log v1.2 / F-Log2 v1.1 / F-Log2 C v1.0 data sheets; F-Gamut = BT.2020
  Apple    Apple Log profile (Apple-supplied ACES IDT AppleLog_BT2020); BT.2020
  Panasonic VARICAM V-Log/V-Gamut (2014)
Not built in: DJI D-Log M and Apple Log 2 (no published math) — import a .cube.
"""
from __future__ import annotations

import hashlib
import math
import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import config

GENERATOR_VERSION = "2"
CUBE_SIZE = 33
USER_LUT_DIR = Path(os.getenv("ARKIV_LUT_DIR", str(Path.home() / ".arkiv-luts"))).expanduser()
_CACHE_DIR = config.BASE_DIR / ".arkiv" / "luts"


# ── transfer functions: code value / 1023 → scene-linear reflectance ─────────
def _slog3(v: float) -> float:
    cv = v * 1023.0
    if cv >= 171.2102946929:
        return 10.0 ** ((cv - 420.0) / 261.5) * (0.18 + 0.01) - 0.01
    return (cv - 95.0) * 0.01125 / (171.2102946929 - 95.0)


def _dlog(v: float) -> float:
    if v <= 0.14:
        return (v - 0.0929) / 6.025
    return (10.0 ** (3.89616 * v - 2.27752) - 0.0108) / 0.9892


def _ilog(v: float) -> float:
    if v < 0.154402:
        return (v - 0.09055934) / 5.77837328
    return 10.0 ** ((v - 0.623992) / 0.280055) - 0.01


def _clog2(v: float) -> float:
    if v < 0.092864125:
        x = -(10.0 ** ((0.092864125 - v) / 0.24136077) - 1.0) / 87.09937546
    else:
        x = (10.0 ** ((v - 0.092864125) / 0.24136077) - 1.0) / 87.09937546
    return 0.9 * x  # Canon's linear is 0.9 x reflectance


def _clog3(v: float) -> float:
    if v < 0.097465473:
        x = -(10.0 ** ((0.12783901 - v) / 0.36726845) - 1.0) / 14.98325
    elif v <= 0.15277891:
        x = (v - 0.12512219) / 1.9754798
    else:
        x = (10.0 ** ((v - 0.12240537) / 0.36726845) - 1.0) / 14.98325
    return 0.9 * x


def _nlog(v: float) -> float:
    if v < 452.0 / 1023.0:
        return (v / (650.0 / 1023.0)) ** 3 - 0.0075  # low segment is a CUBIC
    return math.exp((v - 619.0 / 1023.0) / (150.0 / 1023.0))


def _fuji(a: float, b: float, c: float, d: float, e: float, f: float, cut2: float) -> Callable[[float], float]:
    def decode(v: float) -> float:
        if v < cut2:
            return (v - f) / e
        return 10.0 ** ((v - d) / c) / a - b / a
    return decode


_flog = _fuji(0.555556, 0.009468, 0.344676, 0.790453, 8.735631, 0.092864, 0.100537775223865)
_flog2 = _fuji(5.555556, 0.064829, 0.245281, 0.384316, 8.799461, 0.092864, 0.100686685370811)


def _applelog(v: float) -> float:
    r0, rt, sigma = -0.05641088, 0.01, 47.28711236
    beta, gamma, delta = 0.00964052, 0.08550479, 0.69336945
    pt = sigma * (rt - r0) ** 2
    if v >= pt:
        return 2.0 ** ((v - delta) / gamma) - beta
    if v >= 0.0:
        return math.sqrt(v / sigma) + r0
    return r0


def _vlog(v: float) -> float:
    if v < 0.181:
        return (v - 0.125) / 5.6
    return 10.0 ** ((v - 0.598206) / 0.241514) - 0.00873


# ── gamuts (all D65) ─────────────────────────────────────────────────────────
Primaries = Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]
_D65 = (0.3127, 0.3290)
_BT709: Primaries = ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060))
_BT2020: Primaries = ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046))
_SGAMUT3: Primaries = ((0.730, 0.280), (0.140, 0.855), (0.100, -0.050))
_SGAMUT3_CINE: Primaries = ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087))
_DGAMUT: Primaries = ((0.71, 0.31), (0.21, 0.88), (0.09, -0.08))
_CINEMA_GAMUT: Primaries = ((0.740, 0.270), (0.170, 1.140), (0.080, -0.100))
_FGAMUT_C: Primaries = ((0.73470, 0.26530), (0.02630, 0.97370), (0.11730, -0.02240))
_VGAMUT: Primaries = ((0.730, 0.280), (0.165, 0.840), (0.100, -0.030))

Mat = List[List[float]]


def _inv3(m: Mat) -> Mat:
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]


def _mul3(a: Mat, b: Mat) -> Mat:
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]


def _npm(p: Primaries, white: Tuple[float, float] = _D65) -> Mat:
    """RGB→XYZ normalised primary matrix (SMPTE RP 177)."""
    cols = [[x / y, 1.0, (1.0 - x - y) / y] for x, y in p]
    m = [[cols[c][r] for c in range(3)] for r in range(3)]
    wx, wy = white
    w = [wx / wy, 1.0, (1.0 - wx - wy) / wy]
    inv = _inv3(m)
    s = [sum(inv[r][k] * w[k] for k in range(3)) for r in range(3)]
    return [[m[r][c] * s[c] for c in range(3)] for r in range(3)]


def gamut_to_709(p: Primaries) -> Mat:
    return _mul3(_inv3(_npm(_BT709)), _npm(p))


# ── output: tone curve + BT.709 OETF ─────────────────────────────────────────
# Log footage carries ~6+ stops above 18% grey; a plain clip at linear 1.0 throws
# all of it away and makes 90% white read near-white (measured 0.96 code value vs
# Sony's own s709 at 0.78). A simple compressive curve f(x) = A·x/(x+B), solved so
# 18% grey → 0.20 linear (≈0.43 after the OETF, Sony s709 ≈0.43) and 90% white →
# 0.60 (≈0.77, Sony ≈0.78), reaches display white at x = 4.5 (~4.6 stops over grey).
_TONE_A, _TONE_B = 1.2, 0.9


def _tone(x: float) -> float:
    return _TONE_A * x / (x + _TONE_B) if x > 0.0 else 0.0


def _oetf709(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 4.5 * x if x < 0.018 else 1.099 * x ** 0.45 - 0.099


# ── profiles ─────────────────────────────────────────────────────────────────
class Profile:
    def __init__(self, pid: str, label: str, vendor: str, decode: Callable[[float], float],
                 primaries: Primaries, note: str = ""):
        self.id, self.label, self.vendor = pid, label, vendor
        self.decode, self.primaries, self.note = decode, primaries, note

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "label": self.label, "vendor": self.vendor,
                "source": "builtin", "note": self.note}


PROFILES: Dict[str, Profile] = {p.id: p for p in [
    Profile("sony-slog3-sgamut3cine", "S-Log3 / S-Gamut3.Cine", "Sony", _slog3, _SGAMUT3_CINE),
    Profile("sony-slog3-sgamut3", "S-Log3 / S-Gamut3", "Sony", _slog3, _SGAMUT3),
    Profile("dji-dlog", "D-Log / D-Gamut", "DJI", _dlog, _DGAMUT,
            "Not D-Log M — D-Log M has no published math; import DJI's .cube for it"),
    Profile("insta360-ilog", "I-Log / BT.2020", "Insta360", _ilog, _BT2020,
            "Curve as revised 2025-07; older-firmware I-Log may differ"),
    Profile("canon-clog2-cinemagamut", "C-Log2 / Cinema Gamut", "Canon", _clog2, _CINEMA_GAMUT),
    Profile("canon-clog3-cinemagamut", "C-Log3 / Cinema Gamut", "Canon", _clog3, _CINEMA_GAMUT),
    Profile("nikon-nlog", "N-Log / BT.2020", "Nikon", _nlog, _BT2020),
    Profile("fujifilm-flog", "F-Log / F-Gamut", "Fujifilm", _flog, _BT2020),
    Profile("fujifilm-flog2", "F-Log2 / F-Gamut", "Fujifilm", _flog2, _BT2020),
    Profile("fujifilm-flog2c", "F-Log2 C / F-Gamut C", "Fujifilm", _flog2, _FGAMUT_C),
    Profile("apple-log", "Apple Log / BT.2020", "Apple", _applelog, _BT2020,
            "Apple Log 2 (iPhone 17 Pro) gamut is unpublished — not covered"),
    Profile("panasonic-vlog", "V-Log / V-Gamut", "Panasonic", _vlog, _VGAMUT),
]}


def generate_cube(profile_id: str, size: int = CUBE_SIZE) -> str:
    """Text of a .cube 3D LUT for a built-in profile (R varies fastest)."""
    p = PROFILES[profile_id]
    m = gamut_to_709(p.primaries)
    axis = [p.decode(i / (size - 1.0)) for i in range(size)]
    lines = [
        "# arkiv generated preview LUT: {0} -> Rec.709 (generator v{1})".format(p.label, GENERATOR_VERSION),
        "# Technical conversion from published vendor math; not a vendor LUT.",
        'TITLE "{0} to Rec.709 (arkiv)"'.format(p.label),
        "LUT_3D_SIZE {0}".format(size),
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                lin = (axis[r], axis[g], axis[b])
                out = []
                for row in m:
                    x = row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2]
                    out.append(_oetf709(_tone(x)))  # negatives (out of 709) clip to 0  # clip out-of-709 negatives
                lines.append("{0:.6f} {1:.6f} {2:.6f}".format(*out))
    return "\n".join(lines) + "\n"


def _builtin_cube_path(profile_id: str) -> Path:
    """Generated lazily into a version-keyed cache so a generator change rebuilds."""
    key = hashlib.sha1("{0}:{1}:{2}".format(profile_id, GENERATOR_VERSION, CUBE_SIZE).encode()).hexdigest()[:8]
    path = _CACHE_DIR / "{0}-{1}.cube".format(profile_id, key)
    if not path.exists():
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp.{0}".format(os.getpid()))
        tmp.write_text(generate_cube(profile_id), encoding="utf-8")
        os.replace(str(tmp), str(path))
    return path


def _user_luts() -> Dict[str, Path]:
    if not USER_LUT_DIR.is_dir():
        return {}
    return {"user:" + f.stem: f for f in sorted(USER_LUT_DIR.glob("*.cube")) if f.is_file()}


def list_profiles() -> List[Dict[str, str]]:
    out = [p.to_dict() for p in PROFILES.values()]
    out += [{"id": pid, "label": f.stem, "vendor": "", "source": "user", "note": str(f)}
            for pid, f in _user_luts().items()]
    return out


def lut_file_for(profile_id: Optional[str]) -> Optional[str]:
    """Path of the .cube for a profile id, or None (unknown / no conversion)."""
    if not profile_id:
        return None
    if profile_id in PROFILES:
        return str(_builtin_cube_path(profile_id))
    user = _user_luts().get(profile_id)
    return str(user) if user else None


# ── detection ────────────────────────────────────────────────────────────────
# Sony XAVC embeds an NRT XML AcquisitionRecord: name/value pairs such as
# CaptureGammaEquation=s-log3-cine, CaptureColorPrimaries=s-gamut3-cine. Other
# vendors' log tags are not mapped yet (no verified samples) → None → user picks.
_SONY_MAP = {
    ("s-log3-cine", "s-gamut3-cine"): "sony-slog3-sgamut3cine",
    ("s-log3", "s-gamut3"): "sony-slog3-sgamut3",
    ("s-log3", "s-gamut3-cine"): "sony-slog3-sgamut3cine",
    ("s-log3-cine", "s-gamut3"): "sony-slog3-sgamut3",
}


def profile_from_acquisition(pairs: Dict[str, str]) -> Optional[str]:
    gamma = (pairs.get("CaptureGammaEquation") or "").strip().lower()
    prim = (pairs.get("CaptureColorPrimaries") or "").strip().lower()
    if not gamma.startswith("s-log3"):
        return None
    return _SONY_MAP.get((gamma, prim), "sony-slog3-sgamut3cine" if "cine" in gamma else "sony-slog3-sgamut3")


def _acquisition_pairs(path: str) -> Dict[str, str]:
    """Name→value of the embedded AcquisitionRecord. exiftool reports the repeated
    tag pairs as Copy1/Copy2…; -G4 keeps them apart instead of keeping only the first."""
    import json
    try:
        r = subprocess.run(
            [config.EXIFTOOL_PATH, "-j", "-a", "-G4",
             "-AcquisitionRecordGroupItemName", "-AcquisitionRecordGroupItemValue", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        d = json.loads(r.stdout)[0] if r.returncode == 0 and r.stdout.strip() else {}
    except Exception:
        return {}
    names, values = {}, {}
    for k, v in d.items():
        copy, _, tag = k.rpartition(":")
        if tag == "AcquisitionRecordGroupItemName":
            names[copy] = str(v)
        elif tag == "AcquisitionRecordGroupItemValue":
            values[copy] = str(v)
    return {names[c]: values[c] for c in names if c in values}


def detect_profile(path: str) -> Optional[str]:
    return profile_from_acquisition(_acquisition_pairs(path))


# ── ffmpeg ───────────────────────────────────────────────────────────────────
def _filter_quote(value: str) -> str:
    """Quote a value for an ffmpeg filtergraph option (spaces, ':' and CJK safe).
    Inside '…' everything is literal except the quote itself, which is closed,
    escaped and reopened."""
    return "'" + value.replace("'", "'\\''") + "'"


def ffmpeg_lut_filter(lut_path: str, matrix: str = "bt709") -> str:
    """Filter chain applying a .cube to the decoded frame. Log curves expect code
    value/1023, so the source is read as full range (no 64–940 expansion) with the
    camera's YCbCr matrix, converted to RGB, then the LUT."""
    return "scale=in_range=pc:out_range=pc:in_color_matrix={0},format=rgb48le,lut3d=file={1}".format(
        matrix, _filter_quote(lut_path))


_matrix_cache: Dict[Tuple[str, int, int], str] = {}
_FFMPEG_MATRIX = {"bt709": "bt709", "bt2020nc": "bt2020", "bt2020c": "bt2020",
                  "smpte170m": "smpte170m", "bt470bg": "bt470bg"}


def yuv_matrix_for(path: str) -> str:
    """The YCbCr matrix the file says it was encoded with (ffprobe color_space),
    mapped to ffmpeg's scale names; untagged/unknown → bt709, which is what the
    Sony/DJI/Canon/Panasonic log modes use. Cached by (path, mtime, size)."""
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return "bt709"
    if key in _matrix_cache:
        return _matrix_cache[key]
    try:
        r = subprocess.run(
            [config.FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_space", "-of", "csv=p=0", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        tag = r.stdout.strip().lower() if r.returncode == 0 else ""
    except Exception:
        return "bt709"  # transient — don't cache
    _matrix_cache[key] = _FFMPEG_MATRIX.get(tag, "bt709")
    return _matrix_cache[key]


def add_lut_to_vf_args(args: Sequence[str], lut_path: Optional[str], matrix: str = "bt709") -> List[str]:
    """Append the LUT chain to frames' ``-vf``/``-filter_complex`` args (after the
    downscale, so the 3D LUT runs on the small image — the expensive step)."""
    args = list(args)
    if not lut_path:
        return args
    chain = ffmpeg_lut_filter(lut_path, matrix)
    if args and args[0] == "-vf":
        args[1] = args[1] + "," + chain
    elif args and args[0] == "-filter_complex":
        fc = args[1]
        assert fc.endswith("[o]"), fc
        args[1] = fc[: -len("[o]")] + "," + chain + "[o]"
    return args


# ── user LUT import / validation ─────────────────────────────────────────────
MAX_USER_LUT_BYTES = 16 * 1024 * 1024  # a 65³ float .cube is ~7 MB
_UNSAFE_NAME = __import__("re").compile(r"[^\w\-. ()　-鿿]+")


class InvalidLUT(ValueError):
    pass


def validate_cube(text: str) -> int:
    """Return LUT_3D_SIZE of a well-formed 3D .cube, else raise InvalidLUT.
    1D LUTs are rejected (they cannot carry a gamut transform)."""
    size, rows = None, 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()[0].upper()
        if head == "LUT_1D_SIZE":
            raise InvalidLUT("只支援 3D LUT（這是 1D LUT）")
        if head == "LUT_3D_SIZE":
            try:
                size = int(line.split()[1])
            except (IndexError, ValueError):
                raise InvalidLUT("LUT_3D_SIZE 格式錯誤")
            if not 2 <= size <= 129:
                raise InvalidLUT("LUT_3D_SIZE 超出範圍（2–129）")
            continue
        if head in ("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_3D_INPUT_RANGE"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise InvalidLUT("資料列格式錯誤：{0}".format(line[:40]))
        try:
            [float(p) for p in parts]
        except ValueError:
            raise InvalidLUT("資料列不是數字：{0}".format(line[:40]))
        rows += 1
    if size is None:
        raise InvalidLUT("缺少 LUT_3D_SIZE")
    if rows != size ** 3:
        raise InvalidLUT("資料列數 {0} ≠ {1}³".format(rows, size))
    return size


def save_user_lut(filename: str, data: bytes) -> str:
    """Validate and store an uploaded .cube in USER_LUT_DIR. Returns its profile id.
    Same-named file is replaced (re-import an updated vendor LUT)."""
    if len(data) > MAX_USER_LUT_BYTES:
        raise InvalidLUT("檔案太大（上限 16 MB）")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidLUT("不是文字格式的 .cube")
    validate_cube(text)
    stem = _UNSAFE_NAME.sub("_", Path(filename or "").stem).strip(" ._") or "imported"
    USER_LUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = USER_LUT_DIR / (stem[:80] + ".cube")
    tmp = dest.with_name(dest.name + ".tmp.{0}".format(os.getpid()))
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(dest))
    return "user:" + dest.stem


def is_known(profile_id: Optional[str]) -> bool:
    return profile_id in (None, "none") or profile_id in PROFILES or profile_id in _user_luts()
