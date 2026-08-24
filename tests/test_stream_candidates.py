"""`/api/stream` picks a file, then every candidate goes through the same gate.

The old shape returned arkiv's own proxy early and only gated the original. That
was fine while ours was the only alternative — it is H.264 by construction. It
stops being fine the moment there is a second candidate: a ProRes original next to
a ProRes sidecar proxy would be declared playable because a file was *found*, and
the viewer gets a black player with no explanation.

Two rules that are easy to get wrong and expensive when wrong:

* **`.mxf` is not a candidate.** A browser cannot demux it whatever the codec
  inside, so serving one is a guaranteed black player rather than a 409 the UI can
  act on.
* **Never write a proxy's codec back to `media.codec`.** That column describes the
  camera original, and a sidecar being a different codec is the entire point of it.
  Backfilling from the proxy makes the library claim the original is H.264, and
  every later decision believes it.
"""
from __future__ import annotations

import importlib

import pytest

import codec
import config
import pathres


@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "cam" / "A001.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00" * 16)
    return src


def _seed(db, sample_record, src, **over):
    rec = dict(path=str(src), filename=src.name, ext=src.suffix,
               duration_s=12.0, fps=30.0, has_audio=1)
    rec.update(over)
    db.upsert(sample_record(**rec))
    return 1


def _sidecar(src, name="A001.mp4"):
    folder = src.parent / "Proxy"
    folder.mkdir(exist_ok=True)
    p = folder / name
    p.write_bytes(b"\x00" * 16)
    return p


def _probe_duration(monkeypatch, seconds=12.0):
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: seconds)


def test_a_playable_sidecar_is_served(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="prores")
    proxy = _sidecar(clip)
    _probe_duration(monkeypatch)
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: "h264")

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith('A001.mp4"')


def test_a_prores_sidecar_is_refused_like_any_other_prores(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """The case the restructure exists for. Finding a file is not the same as
    finding a playable one."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="prores")
    _sidecar(clip, name="A001.mov")
    _probe_duration(monkeypatch)
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: "prores")

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 409
    assert r.json()["need_proxy"] is True


def test_an_mxf_sidecar_is_never_a_candidate(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """Even H.264 inside MXF: no browser demuxes the container."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="prores")
    _sidecar(clip, name="A001.mxf")
    _probe_duration(monkeypatch)
    probed = []
    monkeypatch.setattr(codec, "probe_codec",
                        lambda path, **k: probed.append(path) or "prores")

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 409  # fell through to the original, which is ProRes
    assert all(not p.endswith(".mxf") for p in probed)


def test_the_candidate_is_probed_not_the_stored_codec(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """`media.codec` describes the ORIGINAL. Trusting it for a sidecar would
    refuse a perfectly playable proxy because the camera shot ProRes."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="prores")
    proxy = _sidecar(clip)
    _probe_duration(monkeypatch)
    probed = []
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: probed.append(path) or "h264")

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 200
    assert probed == [str(proxy)]


def test_the_proxy_codec_is_not_written_back_to_the_record(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """The quiet, permanent version of the bug: after one playback the library
    would believe the ProRes original is H.264."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec=None)
    _sidecar(clip)
    _probe_duration(monkeypatch)
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: "h264")

    fastapi_client.get("/api/stream/%d" % mid)

    assert (db.get_record_by_id(mid).get("codec") or "") == ""


def test_the_original_still_backfills_its_own_codec(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """The existing behaviour for legacy rows must survive the restructure —
    probing on every playback is what that backfill avoids."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec=None)
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: "h264")

    fastapi_client.get("/api/stream/%d" % mid)

    assert db.get_record_by_id(mid).get("codec") == "h264"


def test_our_own_proxy_still_wins_and_needs_no_probe(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="prores")
    _sidecar(clip)
    _probe_duration(monkeypatch)
    ours = config.proxy_path_for(mid, str(clip))
    ours.parent.mkdir(parents=True, exist_ok=True)
    ours.write_bytes(b"\x00" * 16)
    probed = []
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: probed.append(path) or "prores")

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 200
    assert probed == [], "arkiv's own proxy is H.264 by construction"


def test_a_mismatched_sidecar_is_not_served(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """The duration gate applies here too: a proxy with handles would play, but
    every seek the user makes would land in the wrong place."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="h264")
    _sidecar(clip)
    _probe_duration(monkeypatch, 14.0)  # two seconds of handles
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: "h264")

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith('A001.mov"')  # the original
