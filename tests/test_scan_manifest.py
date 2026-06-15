"""S1a — /api/ingest/scan returns op-01's MANIFEST panel (counts + sizes by
category + unsupported-stills skip count). Pure aggregation over the scan."""


def test_scan_manifest_categorizes_and_sizes(fastapi_client, tmp_path):
    card = tmp_path / "card"; (card / "DCIM").mkdir(parents=True)
    (card / "DCIM" / "A001.MP4").write_bytes(b"v" * (2 * 1048576))   # 2 MB video
    (card / "DCIM" / "A002.MOV").write_bytes(b"v" * (1 * 1048576))   # 1 MB video
    (card / "DCIM" / "VOICE.WAV").write_bytes(b"a" * (1048576 // 2)) # 0.5 MB audio
    (card / "DCIM" / "STILL.CRW").write_bytes(b"r" * 100)            # unsupported raw still
    (card / "DCIM" / "STILL2.crw").write_bytes(b"r" * 100)           # case-insensitive
    (card / "DCIM" / "notes.txt").write_bytes(b"x")                  # neither media nor still — ignored

    r = fastapi_client.post("/api/ingest/scan", json={"src": str(card), "path": str(card)})
    assert r.status_code == 200
    m = r.json()["manifest"]

    assert m["video"]["count"] == 2
    assert m["video"]["size_mb"] == 3.0
    assert m["audio"]["count"] == 1
    assert m["audio"]["size_mb"] == 0.5
    assert m["unsupported"]["count"] == 2          # both .crw / .CRW, .txt excluded
    assert m["unsupported"]["by_ext"] == {".crw": 2}
    assert m["total_size_mb"] == 3.5               # video+audio only, stills not counted


def test_scan_manifest_empty_dir(fastapi_client, tmp_path):
    d = tmp_path / "empty"; d.mkdir()
    m = fastapi_client.post("/api/ingest/scan", json={"path": str(d)}).json()["manifest"]
    assert m["video"]["count"] == 0 and m["audio"]["count"] == 0
    assert m["unsupported"]["count"] == 0
    assert m["total_size_mb"] == 0
