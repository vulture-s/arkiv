"""`/api/stream` must honour Range, because the UI now asks it a question.

The Inspector used to render "此編碼瀏覽器播不了 · 建立 proxy" off a bare
`<video on:error>`, which fires for a 401 and a 404 just as readily as for a
codec with no decoder. It now asks the endpoint what actually happened — and it
asks with `Range: bytes=0-0`, because `/api/stream` serves the ORIGINAL file for
a playable codec. Without a range, a diagnostic question pulls a 4K source down
the wire to answer it, which is worse than the bug it replaces.

That guarantee currently comes from Starlette's `FileResponse` and was never
asserted anywhere. Swapping the response class — for a signed redirect, a
custom streamer, anything — would silently turn every playback failure into a
full download, and nothing on screen would look different.
"""
import importlib

import pathres


def _seed(db, sample_record, tmp_path, name="A001.mp4", size=4096, **over):
    src = tmp_path / "cam" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(bytes(range(256)) * (size // 256))
    rec = dict(path=str(src), filename=src.name, ext=src.suffix,
               duration_s=12.0, fps=30.0, has_audio=1, codec="h264")
    rec.update(over)
    db.upsert(sample_record(**rec))
    return 1, src


def test_a_one_byte_range_returns_one_byte(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path)
    monkeypatch.setattr(pathres, "_probe_duration", lambda p: 12.0)

    r = fastapi_client.get("/api/stream/%d" % mid, headers={"Range": "bytes=0-0"})

    assert r.status_code == 206, "a range request must not be answered with the whole file"
    assert len(r.content) == 1, (
        "the diagnostic probe would download {0} bytes instead of 1".format(len(r.content))
    )
    assert r.headers.get("content-range") == "bytes 0-0/%d" % src.stat().st_size


def test_without_a_range_the_whole_file_still_comes_back(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """The contrast is the point: playback needs the full body, so the saving
    comes from the header the caller sends, not from anything the server does on
    its own. A test that only checked the ranged call would pass on a server that
    had started truncating every response."""
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path, name="B001.mp4")
    monkeypatch.setattr(pathres, "_probe_duration", lambda p: 12.0)

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 200
    assert len(r.content) == src.stat().st_size
