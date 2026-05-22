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
