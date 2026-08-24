"""The inspector's waveform decoded the 4K original every time.

`/api/stream` has served the H.264 proxy since the proxy feature shipped. The
waveform endpoint — which runs ffmpeg over the whole file to produce sixty numbers
— never did. So drawing a 60-bar strip meant a full decode of the camera original,
on a clip we already keep a small proxy of. It is the slowest thing the inspector
does and the one with the least excuse.

The second half of this is the cache key. `waveforms/{id}_{bins}.json` recorded
nothing about where the peaks came from, so the first answer cached won a clip
forever: draw it before its proxy exists and it is drawn from the original for
good. The source is now part of the filename.
"""
from __future__ import annotations

import importlib
import json

import pytest

import config
import pathres


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """The waveform cache lives under `BASE_DIR/waveforms` — the real repo
    directory. Without this the tests write into the working tree AND read each
    other's cached answers, which is how three of them first passed for the wrong
    reason."""
    import routers.media as rm
    monkeypatch.setattr(rm, "BASE_DIR", tmp_path / "cache_root")
    yield


def _recording_waveform(seen, value=0.5):
    def _compute(path, bins):
        seen.append(path)
        return [value] * bins
    return _compute


def _seed(db, sample_record, tmp_path):
    src = tmp_path / "cam" / "A001.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00" * 16)
    db.upsert(sample_record(path=str(src), filename="A001.mov", ext=".mov", has_audio=1))
    return 1, src


def _make_proxy(media_id, src):
    p = config.proxy_path_for(media_id, str(src))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 16)
    return p


def test_the_proxy_is_decoded_when_there_is_one(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path)
    proxy = _make_proxy(mid, src)

    seen = []
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform", _recording_waveform(seen))

    r = fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert r.status_code == 200
    assert seen == [str(proxy)], "decoded the original instead of the proxy"


def test_the_original_is_decoded_when_there_is_no_proxy(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path)

    seen = []
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform", _recording_waveform(seen))

    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert seen == [str(src)]


def test_an_empty_proxy_file_is_not_treated_as_a_proxy(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """A killed encode leaves a zero-byte file. Decoding it yields no peaks at all,
    and the clip would show a flat line forever — the same `_proxy_ready` rule
    /api/stream uses."""
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path)
    proxy = _make_proxy(mid, src)
    proxy.write_bytes(b"")
    assert pathres._proxy_ready(proxy) is False

    seen = []
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform", _recording_waveform(seen))

    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert seen == [str(src)]


def test_the_cache_filename_records_the_source(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path)
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform", lambda path, bins: [0.25] * bins)

    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)
    _make_proxy(mid, src)
    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    import routers.media as rm2
    names = sorted(p.name for p in (rm2.BASE_DIR / "waveforms").glob("*.json"))
    assert names == ["%d_8_o.json" % mid, "%d_8_p.json" % mid]


def test_gaining_a_proxy_redraws_instead_of_serving_the_old_picture(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """The bug the tag exists for. With one key per (id, bins) the answer computed
    from the original is served for the rest of the library's life."""
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path)
    import routers.media as rm

    monkeypatch.setattr(rm, "_compute_waveform", lambda path, bins: [0.1] * bins)
    first = fastapi_client.get("/api/media/%d/waveform?bins=8" % mid).json()

    _make_proxy(mid, src)
    monkeypatch.setattr(rm, "_compute_waveform", lambda path, bins: [0.9] * bins)
    second = fastapi_client.get("/api/media/%d/waveform?bins=8" % mid).json()

    assert first["peaks"][0] == 0.1
    assert second["peaks"][0] == 0.9, "served the pre-proxy picture from cache"


def test_the_cache_is_still_used_when_the_source_has_not_changed(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """Tagging must not accidentally turn the cache off — that would make every
    inspector click re-decode."""
    db = importlib.import_module("db")
    mid, _src = _seed(db, sample_record, tmp_path)
    import routers.media as rm

    calls = []
    monkeypatch.setattr(rm, "_compute_waveform", _recording_waveform(calls, 0.3))

    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)
    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert len(calls) == 1


def test_a_clip_whose_original_is_offline_still_draws_from_its_proxy(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """A side effect worth having: the NAS being unmounted no longer blanks the
    waveform for clips that have a local proxy."""
    db = importlib.import_module("db")
    mid, src = _seed(db, sample_record, tmp_path)
    _make_proxy(mid, src)
    src.unlink()

    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform", lambda path, bins: [0.4] * bins)

    r = fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert r.status_code == 200
    assert r.json()["peaks"][0] == 0.4
