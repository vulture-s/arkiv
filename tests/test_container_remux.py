""".mts probes as H.264, and no browser can play it.

AVCHD camcorder footage is plain H.264 inside an MPEG-TS container. The codec
check therefore says "browser-friendly", arkiv hands over the original bytes
labelled `video/mp4`, and the demuxer fails silently. The user sees a black player
and no error — the worst version of this failure, because every diagnostic
available to them says the file is fine.

So the container has to be decided BEFORE the codec, and without probing: the
probe's answer is the thing that misleads.
"""
from __future__ import annotations

import importlib

import pytest

import codec


@pytest.mark.parametrize("name", ["A.mts", "A.M2TS", "A.m2t", "A.ts", "A.mxf"])
def test_undemuxable_containers_are_recognised_by_extension(name):
    assert codec.container_needs_remux(name) is True


@pytest.mark.parametrize("name", ["A.mp4", "A.mov", "A.mkv", "A.webm"])
def test_ordinary_containers_are_not(name):
    assert codec.container_needs_remux(name) is False


def test_the_verdict_does_not_wait_for_a_probe(monkeypatch):
    """If the container check ran after probing, a missing/slow ffprobe would turn
    a known-bad container into UNKNOWN — which the stream endpoint falls through
    on, i.e. straight back to serving the unplayable bytes."""
    probed = []
    monkeypatch.setattr(codec, "probe_codec", lambda p, **k: probed.append(p) or "h264")

    assert codec.needs_proxy("/x/A001.mts") == codec.NEEDED
    assert probed == [], "probed a file whose container already settled it"


def test_h264_in_an_ordinary_container_is_still_fine(monkeypatch):
    monkeypatch.setattr(codec, "probe_codec", lambda p, **k: "h264")
    assert codec.needs_proxy("/x/A001.mp4") == codec.NOT_NEEDED


def _seed(db, sample_record, tmp_path, name, **over):
    src = tmp_path / name
    src.write_bytes(b"\x00" * 16)
    rec = dict(path=str(src), filename=name, ext=src.suffix, duration_s=12.0, fps=30.0)
    rec.update(over)
    db.upsert(sample_record(**rec))
    return 1, src


def test_streaming_an_mts_asks_for_a_proxy_instead_of_sending_it(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid, _src = _seed(db, sample_record, tmp_path, "A001.mts", codec="h264")

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 409
    body = r.json()
    assert body["need_proxy"] is True
    assert ".mts" in body["reason"]


def test_the_proxy_builder_agrees_that_an_mts_needs_one(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """The two halves have to agree. The batch builder decides from the STORED
    codec, which for an .mts is "h264" — so it would skip the very files the
    stream endpoint has just told the user to build proxies for, and the 409 would
    never clear."""
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path, "A001.mts", codec="h264")
    ingest = importlib.import_module("ingest")

    built = []
    fake_out = tmp_path / "proxy.mp4"
    fake_out.write_bytes(b"\x00" * 8)  # the reporter stats it for the size delta

    monkeypatch.setattr(ingest, "generate_proxy",
                        lambda media_id, path, **k: built.append(path) or str(fake_out))

    ingest.build_proxies()

    assert built == [str(src)]


def test_the_proxy_builder_still_skips_an_ordinary_h264_mp4(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    _seed(db, sample_record, tmp_path, "B001.mp4", codec="h264")
    ingest = importlib.import_module("ingest")

    built = []
    monkeypatch.setattr(ingest, "generate_proxy",
                        lambda media_id, path, **k: built.append(path) or "/fake/proxy.mp4")

    ingest.build_proxies()

    assert built == []
