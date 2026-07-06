def test_parse_xavc_sidecar_with_namespace(tmp_path):
    mp4 = tmp_path / 'FX30.5378.MP4'
    mp4.write_bytes(b'fake mp4')
    sidecar = tmp_path / 'FX30.5378M01.XML'
    sidecar.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">
    <Device manufacturer="Sony" modelName="ILME-FX30" serialNo="05000452"/>
    <Lens modelName="E 17-70mm F2.8 B070"/>
    <CreationDate value="2026-01-28T10:50:45+08:00"/>
</NonRealTimeMeta>''', encoding='utf-8')
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest
    result = ingest.parse_xavc_sidecar(str(mp4))
    assert result['camera_make'] == 'Sony'
    assert result['camera_model'] == 'ILME-FX30'
    assert result['lens_model'] == 'E 17-70mm F2.8 B070'
    assert result['creation_date'] == '2026-01-28T10:50:45+08:00'


def test_parse_xavc_sidecar_missing(tmp_path):
    mp4 = tmp_path / 'lone.MP4'
    mp4.write_bytes(b'fake')
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest
    assert ingest.parse_xavc_sidecar(str(mp4)) == {}


def test_parse_xavc_sidecar_malformed(tmp_path):
    mp4 = tmp_path / 'broken.MP4'
    mp4.write_bytes(b'fake')
    sidecar = tmp_path / 'brokenM01.XML'
    sidecar.write_text('<not valid xml', encoding='utf-8')
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest
    assert ingest.parse_xavc_sidecar(str(mp4)) == {}


# issue #115: Sony XAVC embedded-XML (NRT) camera identity fallback

def test_exiftool_camera_falls_back_to_embedded_xml_device(monkeypatch):
    """Sony XAVC without an M01.XML sidecar leaves standard Make/Model blank but
    carries device identity in the embedded XML as DeviceManufacturer /
    DeviceModelName. Read them in the same exiftool call so camera_make/model
    populate instead of staying NULL (445/480 恬馨 clips were empty for this)."""
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    fake_stdout = json.dumps([{
        "SourceFile": "C0042.MP4",
        # standard EXIF Make/Model/LensModel intentionally absent (Sony XAVC)
        "DeviceManufacturer": "Sony",
        "DeviceModelName": "ILCE-7M5",
        "LensZoomModelName": "FE 24-70mm F2.8 GM",
    }])

    class _FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _FakeProc())
    result = ingest.exiftool_extract("C0042.MP4")
    assert result["camera_make"] == "Sony"
    assert result["camera_model"] == "ILCE-7M5"
    assert result["lens_model"] == "FE 24-70mm F2.8 GM"


def test_exiftool_camera_prefers_standard_over_embedded_xml(monkeypatch):
    """When both the standard EXIF Make/Model and the embedded-XML device tags
    are present, the standard tags win — the embedded fallback only fills gaps."""
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    fake_stdout = json.dumps([{
        "SourceFile": "C0042.MP4",
        "Make": "SONY",
        "Model": "ILME-FX30",
        "LensModel": "E 17-70mm F2.8 B070",
        # stale/duplicate embedded values that must NOT override standard tags
        "DeviceManufacturer": "Sony Corporation",
        "DeviceModelName": "wrong-model",
        "LensZoomModelName": "wrong-lens",
    }])

    class _FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _FakeProc())
    result = ingest.exiftool_extract("C0042.MP4")
    assert result["camera_make"] == "SONY"
    assert result["camera_model"] == "ILME-FX30"
    assert result["lens_model"] == "E 17-70mm F2.8 B070"


# B10b2: Blackmagic Cam app (iOS) per-vendor lens tag

def test_exiftool_lens_falls_back_to_bmd_camera_lens_type(monkeypatch):
    """Blackmagic Cam app writes lens in non-standard `Blackmagic-design Camera
    Lens Type` (Keys group). When standard `-LensModel` returns empty, fall
    back to BMD tag so iPhone clips recorded via BMD Cam still populate lens."""
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    fake_stdout = json.dumps([{
        "SourceFile": "iphone.mov",
        "Model": "Apple iPhone 16 Pro 48mm",
        "Blackmagic-designCameraLensType": "iPhone 16 Pro 48mm",
        # LensModel intentionally absent (BMD Cam doesn't write it)
    }])

    class _FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _FakeProc())
    result = ingest.exiftool_extract("iphone.mov")
    assert result["lens_model"] == "iPhone 16 Pro 48mm"
    assert result["camera_model"] == "Apple iPhone 16 Pro 48mm"


def test_exiftool_lens_prefers_standard_lensmodel_over_bmd(monkeypatch):
    """If both standard LensModel and BMD tag present, standard wins (e.g.
    BMD app on a Sony body that already writes LensModel)."""
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    fake_stdout = json.dumps([{
        "SourceFile": "edge.mov",
        "LensModel": "Standard 24-70mm f/2.8",
        "Blackmagic-designCameraLensType": "Generic BMD lens",
    }])

    class _FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _FakeProc())
    result = ingest.exiftool_extract("edge.mov")
    assert result["lens_model"] == "Standard 24-70mm f/2.8"


def test_bmd_metadata_parsing_and_shutter_angle_conversion(monkeypatch):
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    fake_stdout = json.dumps([{
        "SourceFile": "bmd.mov",
        "Blackmagic-designCameraIso": 640,
        "Blackmagic-designCameraAperture": 2.8,
        "Blackmagic-designCameraShutterAngle": 172.8,
        "Blackmagic-designCameraWhiteBalanceKelvin": 5600,
        "Blackmagic-designCameraWhiteBalanceTint": 10,
        "Blackmagic-designCameraEnvironment": "Indoor",
        "Blackmagic-designCameraDayNight": "Night",
    }])

    class _FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _FakeProc())
    result = ingest.exiftool_extract("bmd.mov", fps=24)
    assert result["iso"] == 640
    assert result["aperture"] == 2.8
    assert result["shutter_speed"] == "1/50"
    assert result["white_balance"] == "5600K T10"
    assert result["_auto_tags"] == ["indoor", "night"]


def test_bmd_shutter_angle_conversion_examples():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    cases = [
        (172.8, 24, "1/50"),
        (360.0, 24, "1/24"),
        (90.0, 24, "1/96"),
        (180.0, 25, "1/50"),
    ]
    for angle, fps, expected in cases:
        assert ingest._shutter_angle_to_speed(angle, fps) == expected


def test_bmd_shutter_angle_parse_fail_logs_warning(monkeypatch, caplog):
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    fake_stdout = json.dumps([{
        "SourceFile": "broken.mov",
        "Blackmagic-designCameraShutterAngle": "not-a-number",
    }])

    class _FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _FakeProc())
    with caplog.at_level("WARNING"):
        result = ingest.exiftool_extract("broken.mov", fps=24)
    assert result["shutter_speed"] is None
    assert any("Blackmagic-designCameraShutterAngle parse failed" in record.message for record in caplog.records)


def test_bmd_white_balance_negative_tint():
    """Cooling tints are negative in BMD metadata and must survive intact."""
    import ingest
    assert ingest._white_balance_string(5600, -10) == "5600K T-10"


def test_bmd_white_balance_rounds_floats():
    import ingest
    assert ingest._white_balance_string(5603.6, 9.4) == "5604K T9"


def test_bmd_white_balance_non_numeric_returns_none(caplog):
    import ingest
    with caplog.at_level("WARNING"):
        assert ingest._white_balance_string("auto", None) is None
    assert any(
        "Blackmagic-designCameraWhiteBalance parse failed" in r.message for r in caplog.records
    )


def test_bmd_shutter_angle_non_positive_returns_none():
    """Zero/negative angle or fps is invalid — must not divide, returns None."""
    import ingest
    assert ingest._shutter_angle_to_speed(0, 24) is None
    assert ingest._shutter_angle_to_speed(-90, 24) is None
    assert ingest._shutter_angle_to_speed(180, 0) is None


def test_bmd_shutter_angle_near_360_and_over():
    """Near-360 rounds to base shutter; >360° (shutter drag) is still valid."""
    import ingest
    assert ingest._shutter_angle_to_speed(359.0, 24) == "1/24"   # 24.07 → 24
    assert ingest._shutter_angle_to_speed(720.0, 24) == "1/12"   # drag, no upper guard


def test_bmd_standard_exposure_fields_win_over_bmd(monkeypatch):
    """When a body writes standard ISO/FNumber/ShutterSpeed, those win and the
    BMD-design fallbacks are ignored — the precedence the parser promises."""
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    fake_stdout = json.dumps([{
        "SourceFile": "mix.mov",
        "ISO": 200, "FNumber": 4.0, "ShutterSpeed": "1/100",
        # BMD fallbacks present but should be shadowed by the standard fields
        "Blackmagic-designCameraIso": 640,
        "Blackmagic-designCameraAperture": 2.8,
        "Blackmagic-designCameraShutterAngle": 172.8,
    }])

    class _FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _FakeProc())
    result = ingest.exiftool_extract("mix.mov", fps=24)
    assert result["iso"] == 200
    assert result["aperture"] == 4.0
    assert result["shutter_speed"] == "1/100"


def test_bmd_metadata_roundtrips_white_balance_column(tmp_db, sample_record):
    import importlib

    db = importlib.import_module("db")
    db.upsert(
        sample_record(
            path="/tmp/bmd.mov",
            filename="bmd.mov",
            iso=800,
            aperture=2.8,
            shutter_speed="1/50",
            white_balance="5600K T0",
        )
    )
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT iso, aperture, shutter_speed, white_balance FROM media WHERE path = ?",
            ("/tmp/bmd.mov",),
        ).fetchone()
    assert row["iso"] == 800
    assert row["aperture"] == 2.8
    assert row["shutter_speed"] == "1/50"
    assert row["white_balance"] == "5600K T0"


# ── probe() robustness: surface real errors + timeout + one retry ────────────

def _probe_good_json():
    import json
    return json.dumps({
        "format": {"duration": "5", "size": "1048576"},
        "streams": [
            {"codec_type": "video", "r_frame_rate": "30/1", "width": 1920,
             "height": 1080, "codec_name": "h264"},
            {"codec_type": "audio"},
        ],
    })


def test_probe_retries_on_spawn_error_then_succeeds(monkeypatch):
    """A transient subprocess-spawn failure (e.g. handle exhaustion under load)
    is retried once instead of poisoning the whole batch."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    class _P:
        returncode = 0
        stdout = _probe_good_json()
        stderr = ""

    calls = {"n": 0}

    def fake_run(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(24, "Too many open files")
        return _P()

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    meta = ingest.probe("x.mp4")
    assert meta is not None
    assert calls["n"] == 2                      # failed once, retried, then OK
    assert abs(meta["duration_s"] - 5.0) < 0.01


def test_probe_returns_none_and_surfaces_nonzero_rc(monkeypatch, capsys):
    """rc != 0 no longer silently returns None — the real ffprobe stderr is printed."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    class _P:
        returncode = 1
        stdout = ""
        stderr = "moov atom not found"

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **kw: _P())
    assert ingest.probe("bad.mp4") is None
    out = capsys.readouterr().out
    assert "rc=1" in out and "moov atom" in out


def test_probe_gives_up_after_retry_on_persistent_spawn_error(monkeypatch, capsys):
    """Two spawn failures → returns None with a diagnostic, not an unhandled raise."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    def fake_run(*a, **kw):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    assert ingest.probe("x.mp4") is None
    assert "spawn failed" in capsys.readouterr().out


def test_probe_returns_none_on_timeout(monkeypatch, capsys):
    """A hung ffprobe times out (bounded) instead of blocking the batch forever."""
    import sys, os, subprocess as _sp
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import ingest

    def fake_run(*a, **kw):
        raise _sp.TimeoutExpired(cmd="ffprobe", timeout=120)

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    assert ingest.probe("x.mp4") is None
    assert "spawn failed" in capsys.readouterr().out
