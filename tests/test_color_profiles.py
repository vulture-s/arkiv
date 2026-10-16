"""Log → Rec.709 preview LUTs (color_profiles.py).

Curve constants are transcribed from vendor papers, so each one is pinned to the
paper's own reference points (0% / 18% / 90% reflectance at a 10-bit code value).
A typo in a constant moves 18% grey visibly and fails here.
"""
import importlib
import shutil
import subprocess

import pytest

cp = importlib.import_module("color_profiles")
config = importlib.import_module("config")

# (profile, code value, expected reflectance) — 10-bit CV from the vendor tables
# (Sony 95/420/598, Panasonic 128/433/602, Fujifilm 95/470/705 & 95/400/570,
# DJI 95/408/586, Insta360 float 0.422003 = 18%) or computed by inverting the
# published encode where the paper prints no table (Canon, Nikon, Apple).
REFERENCE_POINTS = [
    ("sony-slog3-sgamut3cine", 95.0, 0.0), ("sony-slog3-sgamut3cine", 420.0, 0.18),
    ("sony-slog3-sgamut3cine", 597.9, 0.90),
    ("panasonic-vlog", 433.05, 0.18), ("panasonic-vlog", 601.70, 0.90),
    ("fujifilm-flog", 95.0, 0.0), ("fujifilm-flog", 469.88, 0.18), ("fujifilm-flog", 705.36, 0.90),
    ("fujifilm-flog2", 400.0, 0.18), ("fujifilm-flog2", 569.95, 0.90),
    ("fujifilm-flog2c", 400.0, 0.18),
    ("dji-dlog", 407.94, 0.18), ("dji-dlog", 586.12, 0.90),
    ("insta360-ilog", 0.422003 * 1023, 0.18),
    ("canon-clog2-cinemagamut", 407.41, 0.18), ("canon-clog2-cinemagamut", 575.24, 0.90),
    ("canon-clog3-cinemagamut", 351.29, 0.18), ("canon-clog3-cinemagamut", 577.46, 0.90),
    ("nikon-nlog", 372.03, 0.18), ("nikon-nlog", 603.20, 0.90),
    ("apple-log", 0.488272 * 1023, 0.18),
]


@pytest.mark.parametrize("pid, cv, refl", REFERENCE_POINTS)
def test_decode_matches_vendor_reference_points(pid, cv, refl):
    assert cp.PROFILES[pid].decode(cv / 1023.0) == pytest.approx(refl, abs=2e-3)


def test_canon_applies_the_0p9_reflectance_scale():
    # Canon's formula outputs x with reflectance = 0.9·x; forgetting it puts 18% at 0.2
    assert cp.PROFILES["canon-clog2-cinemagamut"].decode(407.41 / 1023) == pytest.approx(0.18, abs=2e-3)


def test_dgamut_matrix_matches_dji_whitepaper():
    # DJI's paper prints D-Gamut → Rec.709 to 4 dp; ours is computed from primaries
    printed = [[1.6746, -0.5797, -0.0949], [-0.0981, 1.3340, -0.2359], [-0.0410, -0.2430, 1.2840]]
    ours = cp.gamut_to_709(cp._DGAMUT)
    for r in range(3):
        for c in range(3):
            assert ours[r][c] == pytest.approx(printed[r][c], abs=2e-4)


@pytest.mark.parametrize("pid", sorted(cp.PROFILES))
def test_gamut_matrix_keeps_white_white(pid):
    m = cp.gamut_to_709(cp.PROFILES[pid].primaries)
    assert [sum(row) for row in m] == pytest.approx([1.0, 1.0, 1.0], abs=1e-9)


def _parse_cube(text):
    rows = [tuple(map(float, l.split())) for l in text.splitlines()
            if l and (l[0].isdigit() or l[0] in "-.")]
    size = int(next(l.split()[1] for l in text.splitlines() if l.startswith("LUT_3D_SIZE")))
    return size, rows


def test_generated_cube_shape_and_grey_axis():
    size, rows = _parse_cube(cp.generate_cube("sony-slog3-sgamut3cine", size=17))
    assert size == 17 and len(rows) == 17 ** 3
    greys = [rows[i + i * size + i * size * size] for i in range(size)]
    assert all(abs(r - g) < 1e-6 and abs(g - b) < 1e-6 for r, g, b in greys)  # neutral stays neutral
    lum = [g[0] for g in greys]
    assert lum == sorted(lum)  # monotonic
    assert all(0.0 <= v <= 1.0 for row in rows for v in row)


def test_tone_curve_places_grey_and_white_like_a_709_preview():
    # 18% grey ≈ 0.43 and 90% white ≈ 0.77 display code (Sony's s709 sits at 0.43 / 0.78);
    # a hard clip at linear 1.0 put 90% white at 0.96 and blew every highlight.
    assert cp._oetf709(cp._tone(0.18)) == pytest.approx(0.43, abs=0.01)
    assert cp._oetf709(cp._tone(0.90)) == pytest.approx(0.77, abs=0.01)
    assert cp._oetf709(cp._tone(4.5)) == pytest.approx(1.0, abs=1e-6)


def test_lut_cache_is_versioned_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "_CACHE_DIR", tmp_path)
    a = cp.lut_file_for("panasonic-vlog")
    assert a and a.startswith(str(tmp_path)) and a.endswith(".cube")
    assert cp.lut_file_for("panasonic-vlog") == a
    monkeypatch.setattr(cp, "GENERATOR_VERSION", "999")
    assert cp.lut_file_for("panasonic-vlog") != a  # generator change → new file


def test_unknown_none_and_missing_profiles_give_no_lut(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "USER_LUT_DIR", tmp_path)
    assert cp.lut_file_for(None) is None
    assert cp.lut_file_for("none") is None
    assert cp.lut_file_for("user:nope") is None


def test_user_luts_are_listed_and_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "USER_LUT_DIR", tmp_path)
    (tmp_path / "DJI DLog-M to Rec709.cube").write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    ids = {p["id"]: p for p in cp.list_profiles()}
    assert ids["user:DJI DLog-M to Rec709"]["source"] == "user"
    assert "user:notes" not in ids
    assert cp.lut_file_for("user:DJI DLog-M to Rec709") == str(tmp_path / "DJI DLog-M to Rec709.cube")
    assert {"sony-slog3-sgamut3cine", "apple-log", "panasonic-vlog"} <= set(ids)


@pytest.mark.parametrize("pairs, expected", [
    ({"CaptureGammaEquation": "s-log3-cine", "CaptureColorPrimaries": "s-gamut3-cine"}, "sony-slog3-sgamut3cine"),
    ({"CaptureGammaEquation": "s-log3", "CaptureColorPrimaries": "s-gamut3"}, "sony-slog3-sgamut3"),
    ({"CaptureGammaEquation": "rec709", "CaptureColorPrimaries": "rec709"}, None),
    ({"CaptureGammaEquation": "s-cinetone"}, None),
    ({}, None),
])
def test_sony_acquisition_record_detection(pairs, expected):
    assert cp.profile_from_acquisition(pairs) == expected


def test_acquisition_pairs_keep_repeated_exiftool_tags_apart(monkeypatch):
    # exiftool -G4 reports the repeated name/value tags as "", Copy1, Copy2…
    out = ('[{"SourceFile":"x",":AcquisitionRecordGroupItemName":"CaptureGammaEquation",'
           '"Copy1:AcquisitionRecordGroupItemName":"CaptureColorPrimaries",'
           '":AcquisitionRecordGroupItemValue":"s-log3-cine",'
           '"Copy1:AcquisitionRecordGroupItemValue":"s-gamut3-cine"}]')

    class R:
        returncode, stdout = 0, out

    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: R())
    assert cp.detect_profile("clip.mp4") == "sony-slog3-sgamut3cine"


def test_detection_survives_exiftool_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no exiftool")
    monkeypatch.setattr(cp.subprocess, "run", boom)
    assert cp.detect_profile("clip.mp4") is None


def test_lut_chain_appends_after_downscale():
    vf = cp.add_lut_to_vf_args(["-vf", "scale=320:-1"], "/l/x.cube")
    assert vf[1].startswith("scale=320:-1,scale=in_range=pc") and vf[1].endswith("lut3d=file='/l/x.cube'")
    fc = cp.add_lut_to_vf_args(["-filter_complex", "[0:v:0][0:v:1]hstack[f];[f]v360,scale=1024:-1[o]", "-map", "[o]"],
                               "/l/x.cube")
    assert fc[1].endswith("lut3d=file='/l/x.cube'[o]") and fc[2:] == ["-map", "[o]"]
    assert cp.add_lut_to_vf_args(["-vf", "scale=320:-1"], None) == ["-vf", "scale=320:-1"]


def test_filter_quote_handles_single_quote():
    assert cp._filter_quote("it's.cube") == "'it'\\''s.cube'"


@pytest.mark.skipif(not shutil.which(config.FFMPEG_PATH or "ffmpeg"), reason="ffmpeg not installed")
def test_real_ffmpeg_applies_lut_from_a_path_with_spaces_and_cjk(tmp_path, monkeypatch):
    frames = importlib.import_module("frames")
    lut_dir = tmp_path / "我的 LUT 資料夾"
    lut_dir.mkdir()
    monkeypatch.setattr(cp, "_CACHE_DIR", lut_dir)
    lut = cp.lut_file_for("sony-slog3-sgamut3cine")
    clip = tmp_path / "grey.mp4"
    # 18%-grey S-Log3 patch: code value 420/1023 ≈ 0.41 in every channel
    subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x696969:s=64x36:d=1:r=5", "-pix_fmt", "yuvj444p", str(clip)], check=True)
    plain, graded = tmp_path / "plain.jpg", tmp_path / "graded.jpg"
    assert frames._extract_frame_to(str(clip), 0.2, plain)
    assert frames._extract_frame_to(str(clip), 0.2, graded, lut)
    assert plain.read_bytes() != graded.read_bytes()
