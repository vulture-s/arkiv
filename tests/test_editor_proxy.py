"""The cutting room already made proxies; arkiv re-encoded its own anyway.

When footage comes from an edit, there is usually a `Proxy/` folder sitting next to
the media with a small H.264 of every clip. arkiv ignored it and built a second
copy — or, when it had built nothing yet, decoded the 4K original.

**The duration gate is the whole reason this is safe to use**, not padding around
it. A Resolve proxy routinely carries handles, a slate, or a different start
timecode. "Same stem, same folder" is not "same media", and a proxy two seconds
longer would shift every waveform bar and every trimmed export derived from it — a
mis-picked file that looks like a transcription bug.
"""
from __future__ import annotations

import importlib
import json

import pytest

import config
import pathres


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    import routers.media as rm
    monkeypatch.setattr(rm, "BASE_DIR", tmp_path / "cache_root")
    yield


@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "cam" / "A001.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00" * 16)
    return src


def _sidecar(src, name="A001.mp4", dirname="Proxy"):
    folder = src.parent / dirname
    folder.mkdir(exist_ok=True)
    p = folder / name
    p.write_bytes(b"\x00" * 16)
    return p


def _probe(monkeypatch, seconds):
    """Stand in for ffprobe. `seconds` may be a dict keyed by path."""
    def _fake(path):
        return seconds.get(path) if isinstance(seconds, dict) else seconds
    monkeypatch.setattr(pathres, "_probe_duration", _fake)


# ── the finder ───────────────────────────────────────────────────────────────

def test_a_matching_sidecar_proxy_is_found(clip, monkeypatch):
    proxy = _sidecar(clip)
    _probe(monkeypatch, 12.0)

    assert pathres.editor_proxy_for(str(clip), 12.0, 30.0) == proxy


def test_a_proxy_with_handles_is_refused(clip, monkeypatch):
    """Two seconds of handle. This is the case the gate exists for — and the one
    that would be silently wrong everywhere downstream."""
    _sidecar(clip)
    _probe(monkeypatch, 14.0)

    assert pathres.editor_proxy_for(str(clip), 12.0, 30.0) is None


def test_one_frame_of_difference_is_tolerated(clip, monkeypatch):
    """Rounding between containers legitimately moves the duration by a frame."""
    proxy = _sidecar(clip)
    _probe(monkeypatch, 12.0 + 1.0 / 30.0 - 0.001)

    assert pathres.editor_proxy_for(str(clip), 12.0, 30.0) == proxy


def test_the_tolerance_never_goes_below_half_a_second(clip, monkeypatch):
    """At 120 fps one frame is 8 ms — tighter than container rounding, which would
    reject perfectly good proxies."""
    proxy = _sidecar(clip)
    _probe(monkeypatch, 12.4)

    assert pathres.editor_proxy_for(str(clip), 12.0, 120.0) == proxy


def test_an_unknown_source_duration_refuses_rather_than_guesses(clip, monkeypatch):
    _sidecar(clip)
    _probe(monkeypatch, 12.0)

    assert pathres.editor_proxy_for(str(clip), None, 30.0) is None
    assert pathres.editor_proxy_for(str(clip), 0, 30.0) is None


def test_an_unprobeable_candidate_refuses_rather_than_guesses(clip, monkeypatch):
    _sidecar(clip)
    _probe(monkeypatch, None)

    assert pathres.editor_proxy_for(str(clip), 12.0, 30.0) is None


def test_a_zero_byte_proxy_is_ignored(clip, monkeypatch):
    p = _sidecar(clip)
    p.write_bytes(b"")
    _probe(monkeypatch, 12.0)

    assert pathres.editor_proxy_for(str(clip), 12.0, 30.0) is None


def test_no_proxy_folder_is_not_an_error(clip, monkeypatch):
    _probe(monkeypatch, 12.0)
    assert pathres.editor_proxy_for(str(clip), 12.0, 30.0) is None


@pytest.mark.parametrize("dirname", ["Proxy", "proxy", "PROXY"])
def test_the_folder_name_may_be_any_of_the_usual_spellings(clip, monkeypatch, dirname):
    """macOS is case-insensitive so this looks like one name locally; on the NAS
    the footage actually lives on, it is three."""
    proxy = _sidecar(clip, dirname=dirname)
    _probe(monkeypatch, 12.0)

    found = pathres.editor_proxy_for(str(clip), 12.0, 30.0)
    # samefile, not ==: on a case-insensitive filesystem the finder reports whichever
    # spelling it tried first, which is the same file by a different name. On the
    # NAS the footage actually lives on, these are three distinct directories.
    assert found is not None and found.samefile(proxy)


def test_a_mov_sidecar_is_accepted_too(clip, monkeypatch):
    proxy = _sidecar(clip, name="A001.mov")
    _probe(monkeypatch, 12.0)

    assert pathres.editor_proxy_for(str(clip), 12.0, 30.0) == proxy


# ── through the waveform endpoint ────────────────────────────────────────────

def _seed(db, sample_record, src, duration=12.0, fps=30.0):
    db.upsert(sample_record(path=str(src), filename=src.name, ext=src.suffix,
                            has_audio=1, duration_s=duration, fps=fps))
    return 1


def test_the_waveform_decodes_the_editor_proxy(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip)
    proxy = _sidecar(clip)
    _probe(monkeypatch, 12.0)

    seen = []
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform",
                        lambda path, bins: seen.append(path) or [0.6] * bins)

    r = fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert r.status_code == 200
    assert seen == [str(proxy)]
    assert (rm.BASE_DIR / "waveforms" / ("%d_8_e.json" % mid)).exists()


def test_the_arkiv_proxy_still_wins_over_the_editor_one(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """Ours is a known quantity — same duration by construction, H.264, made for
    this. The sidecar is a fallback for clips we have not proxied yet."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip)
    _sidecar(clip)
    _probe(monkeypatch, 12.0)
    ours = config.proxy_path_for(mid, str(clip))
    ours.parent.mkdir(parents=True, exist_ok=True)
    ours.write_bytes(b"\x00" * 16)

    seen = []
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform",
                        lambda path, bins: seen.append(path) or [0.6] * bins)

    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert seen == [str(ours)]


def test_a_cached_editor_answer_is_served_without_probing_again(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """Finding a sidecar costs an ffprobe. Paying it on every poll of an already
    cached waveform would undo the point of the cache."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip)
    _sidecar(clip)

    probes = []
    monkeypatch.setattr(pathres, "_probe_duration",
                        lambda path: probes.append(path) or 12.0)
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform", lambda path, bins: [0.6] * bins)

    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)
    assert len(probes) == 1
    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)
    assert len(probes) == 1, "re-probed on a cache hit"


def test_a_corrupt_editor_cache_falls_back_instead_of_500ing(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip)
    proxy = _sidecar(clip)
    _probe(monkeypatch, 12.0)
    import routers.media as rm
    (rm.BASE_DIR / "waveforms").mkdir(parents=True, exist_ok=True)
    (rm.BASE_DIR / "waveforms" / ("%d_8_e.json" % mid)).write_text("{not json", encoding="utf-8")

    seen = []
    monkeypatch.setattr(rm, "_compute_waveform",
                        lambda path, bins: seen.append(path) or [0.7] * bins)

    r = fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert r.status_code == 200
    assert seen == [str(proxy)]


def test_a_mismatched_sidecar_leaves_the_waveform_on_the_original(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """End to end: the gate has to hold at the endpoint, not only in the helper."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip)
    _sidecar(clip)
    _probe(monkeypatch, 14.0)  # two seconds of handles

    seen = []
    import routers.media as rm
    monkeypatch.setattr(rm, "_compute_waveform",
                        lambda path, bins: seen.append(path) or [0.6] * bins)

    fastapi_client.get("/api/media/%d/waveform?bins=8" % mid)

    assert seen == [str(clip)]
