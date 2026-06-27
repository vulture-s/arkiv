"""Phase 12.4 — POST /api/export/batch (zip of per-clip exports)."""
import importlib
import io
import json
import zipfile


def _seed(db):
    segs = json.dumps([{"start": 0.0, "end": 2.0, "text": "第一句"},
                       {"start": 2.0, "end": 4.0, "text": "第二句"}], ensure_ascii=False)
    for i, name in enumerate(["a.mp4", "b.mov"], start=1):
        db.upsert({
            "path": "/tmp/{0}".format(name), "filename": name, "ext": ".mp4",
            "duration_s": 4.0, "size_mb": 5.0, "width": 1920, "height": 1080,
            "fps": 30.0, "has_audio": 1, "transcript": "第一句 第二句", "lang": "zh",
            "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-0{0}T09:00:00".format(i),
            "segments_json": segs,
        })


def test_batch_srt_zip_has_one_file_per_clip(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/export/batch", json={"ids": [1, 2], "fmt": "srt"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(zf.namelist())
    assert names == ["a.srt", "b.srt"]
    assert "第一句" in zf.read("a.srt").decode("utf-8")


def test_batch_skips_missing_ids(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/export/batch", json={"ids": [1, 999], "fmt": "txt"})
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.namelist() == ["a.txt"]


def test_batch_all_missing_is_404(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/export/batch", json={"ids": [998, 999], "fmt": "srt"})
    assert r.status_code == 404


def test_batch_bad_fmt_422(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/export/batch", json={"ids": [1], "fmt": "docx"})
    assert r.status_code == 422


def test_batch_empty_ids_422(fastapi_client):
    r = fastapi_client.post("/api/export/batch", json={"ids": [], "fmt": "srt"})
    assert r.status_code == 422


def test_batch_dedupes_duplicate_stems(fastapi_client, server_module):
    db = importlib.import_module("db")
    segs = json.dumps([{"start": 0.0, "end": 1.0, "text": "x"}])
    # two different paths but the same filename stem
    for i, p in enumerate(["/tmp/d1/clip.mp4", "/tmp/d2/clip.mp4"], start=1):
        db.upsert({
            "path": p, "filename": "clip.mp4", "ext": ".mp4", "duration_s": 1.0,
            "size_mb": 1.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": 1,
            "transcript": "x", "lang": "zh", "frame_tags": "", "thumbnail_path": "",
            "processed_at": "2026-05-0{0}T09:00:00".format(i), "segments_json": segs,
        })
    r = fastapi_client.post("/api/export/batch", json={"ids": [1, 2], "fmt": "srt"})
    assert r.status_code == 200
    names = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == ["clip.srt", "clip_1.srt"]  # no silent overwrite
