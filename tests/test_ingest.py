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
