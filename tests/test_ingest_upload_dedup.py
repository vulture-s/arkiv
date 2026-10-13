"""Upload collision-safety (the "keep both snow.mp4" fix).

The upload endpoint must never overwrite an existing file on disk, nor an
already-indexed media row at that path. A same-name re-upload is renamed
(<stem>__<YYYYMMDD-HHMMSS><ext>) so both clips survive instead of the original
being clobbered + then SKIPped by ingest (which would lose both).
"""
import pytest


def _upload(client, ri, name, data):
    return client.post(
        "/api/ingest/upload",
        files=[("files", (name, data, "video/mp4"))],
    )


def test_upload_fresh_name_is_not_renamed(fastapi_client, tmp_path, monkeypatch):
    import routers.ingest as ri

    upload_dir = tmp_path / "media-in"
    upload_dir.mkdir()
    monkeypatch.setattr(ri, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(ri, "_bg_ingest", lambda *a, **k: None)  # skip heavy ingest

    r = _upload(fastapi_client, ri, "fresh.mp4", b"\x00dcIMB" + b"x" * 100)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["saved"] == ["fresh.mp4"]
    assert body["renamed"] == []
    assert (upload_dir / "fresh.mp4").read_bytes() == b"\x00dcIMB" + b"x" * 100


def test_upload_same_name_keeps_both_and_original(fastapi_client, tmp_path, monkeypatch):
    import routers.ingest as ri
    import db

    upload_dir = tmp_path / "media-in"
    upload_dir.mkdir()
    monkeypatch.setattr(ri, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(ri, "_bg_ingest", lambda *a, **k: None)  # skip heavy ingest

    # Pre-existing indexed clip "snow.mp4" — exactly the user's scenario: the
    # library already holds one snow scene; they upload another named snow.mp4.
    original_path = str(upload_dir / "snow.mp4")
    original_bytes = b"\x00dcIMB" + b"ORIGINAL-snow"  # 1st snow scene
    (upload_dir / "snow.mp4").write_bytes(original_bytes)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO media (path, filename) VALUES (?, ?)",
            (original_path, "snow.mp4"),
        )

    # Two distinct uploads, both named snow.mp4 (2nd + 3rd snow scenes).
    content_a = b"\x00dcIMB" + b"SECOND-snow"
    content_b = b"\x00dcIMB" + b"THIRD-snow"
    r_a = _upload(fastapi_client, ri, "snow.mp4", content_a)
    r_b = _upload(fastapi_client, ri, "snow.mp4", content_b)
    assert r_a.status_code == 202 and r_b.status_code == 202, (r_a.text, r_b.text)

    saved_a = r_a.json()["saved"][0]
    saved_b = r_b.json()["saved"][0]
    # Both must be renamed (timestamp suffix) — neither clobbered the original.
    assert saved_a.startswith("snow__") and saved_b.startswith("snow__")
    assert saved_a != saved_b  # distinct names
    assert r_a.json()["renamed"] == [{"from": "snow.mp4", "to": saved_a}]
    assert r_b.json()["renamed"] == [{"from": "snow.mp4", "to": saved_b}]

    # Disk holds THREE distinct files; the original is intact.
    on_disk = {f.name: f.read_bytes() for f in upload_dir.iterdir()}
    assert len(on_disk) == 3
    assert on_disk["snow.mp4"] == original_bytes          # 1st scene untouched
    assert on_disk[saved_a] == content_a                  # 2nd scene
    assert on_disk[saved_b] == content_b                  # 3rd scene


def test_upload_two_same_name_in_one_request(fastapi_client, tmp_path, monkeypatch):
    import routers.ingest as ri
    import db

    upload_dir = tmp_path / "media-in"
    upload_dir.mkdir()
    monkeypatch.setattr(ri, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(ri, "_bg_ingest", lambda *a, **k: None)

    original_path = str(upload_dir / "snow.mp4")
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO media (path, filename) VALUES (?, ?)",
            (original_path, "snow.mp4"),
        )

    data1 = b"\x00dcIMB" + b"one"
    data2 = b"\x00dcIMB" + b"two"
    r = fastapi_client.post(
        "/api/ingest/upload",
        files=[
            ("files", ("snow.mp4", data1, "video/mp4")),
            ("files", ("snow.mp4", data2, "video/mp4")),
        ],
    )
    assert r.status_code == 202, r.text
    saved = r.json()["saved"]
    assert len(saved) == 2
    assert all(s.startswith("snow__") for s in saved)
    assert saved[0] != saved[1]
    on_disk = {f.name: f.read_bytes() for f in upload_dir.iterdir()}
    assert on_disk[saved[0]] == data1
    assert on_disk[saved[1]] == data2
