"""Tests for the DIT card-watcher — offload.py --watch (DIT wrapper ②).

`--watch --dst X [--organize T]` waits for a camera card to mount and auto-offloads
it (copy + verify + MHL, never deletes the source) with the ① naming policy. Only
NEW inserts trigger; already-mounted volumes are ignored.
"""
import importlib
import os

import pytest

offload = importlib.import_module("offload")


# ── _looks_like_card ────────────────────────────────────────────────────────
def test_looks_like_card_dcim(tmp_path):
    (tmp_path / "DCIM").mkdir()
    assert offload._looks_like_card(str(tmp_path)) is True


def test_looks_like_card_media_file(tmp_path):
    (tmp_path / "C0001.MP4").write_bytes(b"x")
    assert offload._looks_like_card(str(tmp_path)) is True


def test_looks_like_card_no_media_is_false(tmp_path):
    (tmp_path / "readme.txt").write_text("hi")
    assert offload._looks_like_card(str(tmp_path)) is False


def test_looks_like_card_missing_path_is_false():
    assert offload._looks_like_card("/no/such/mount") is False


# ── _media_volumes (injected partition list) ────────────────────────────────
def test_media_volumes_filters_system_and_non_cards(tmp_path):
    card = tmp_path / "CARD"; (card / "DCIM").mkdir(parents=True)
    plain = tmp_path / "PLAIN"; plain.mkdir()
    vols = offload._media_volumes(_partitions=[str(card), str(plain), "/"])
    assert os.path.normpath(str(card)) in vols
    assert os.path.normpath(str(plain)) not in vols   # no DCIM / media
    assert "/" not in vols                            # system root skipped


# ── run_card_watch ──────────────────────────────────────────────────────────
def _seq(*states):
    it = iter(states)
    return lambda: next(it)


# Real systems always have at least "/" mounted; a card coexists with it. (An
# *empty* raw set means the probe failed, not "everything unplugged".)
_SYS = "/"


def test_watch_offloads_new_card(monkeypatch):
    calls = []
    monkeypatch.setattr(offload, "run_offload",
                        lambda vol, dsts, **kw: (calls.append((vol, dsts, kw)), (0, {}, "s"))[1])
    handled = offload.run_card_watch(
        ["/dst"], organize="{date}/{camera}", once=True,
        _mounts_fn=_seq({_SYS}, {_SYS, "/Volumes/CARD"}),  # baseline system-only → CARD mounts
        _list_fn=_seq({"/Volumes/CARD"}))                  # …and is card-like
    assert len(calls) == 1
    vol, dsts, kw = calls[0]
    assert vol == "/Volumes/CARD"
    assert dsts == ["/dst"]
    assert kw["organize"] == "{date}/{camera}"     # ① naming policy threaded through
    assert handled == [("/Volumes/CARD", 0)]


def test_watch_ignores_already_mounted(monkeypatch):
    calls = []
    monkeypatch.setattr(offload, "run_offload", lambda *a, **k: (calls.append(1), (0, {}, "s"))[1])
    offload.run_card_watch(
        ["/dst"], once=True,
        _mounts_fn=_seq({_SYS, "/Volumes/CARD"}, {_SYS, "/Volumes/CARD"}),  # mounted at baseline
        _list_fn=_seq({"/Volumes/CARD"}))
    assert calls == []                                # baseline card NOT offloaded


def test_watch_flicker_does_not_reoffload(monkeypatch):
    # Codex: a still-mounted card that transiently fails _looks_like_card must NOT
    # re-offload. handled is pruned by RAW mounts, not the card-detection result.
    calls = []
    monkeypatch.setattr(offload, "run_offload", lambda *a, **k: (calls.append(1), (0, {}, "s"))[1])
    offload.run_card_watch(
        ["/dst"], interval=0, _loops=2,
        _mounts_fn=_seq({_SYS}, {_SYS, "/Volumes/CARD"}, {_SYS, "/Volumes/CARD"}),  # stays mounted
        _list_fn=_seq({"/Volumes/CARD"}, set()))                       # card-like, then flickers off
    assert len(calls) == 1                            # offloaded once, not twice


def test_watch_replug_reoffloads(monkeypatch):
    calls = []
    monkeypatch.setattr(offload, "run_offload", lambda *a, **k: (calls.append(1), (0, {}, "s"))[1])
    offload.run_card_watch(
        ["/dst"], interval=0, _loops=3,
        _mounts_fn=_seq({_SYS}, {_SYS, "/Volumes/CARD"}, {_SYS}, {_SYS, "/Volumes/CARD"}),  # plug/unplug/replug
        _list_fn=_seq({"/Volumes/CARD"}, set(), {"/Volumes/CARD"}))
    assert len(calls) == 2                            # unplug→replug re-arms → second offload


def test_watch_empty_raw_does_not_reoffload(monkeypatch):
    # Codex edge: a transient EMPTY raw-mount probe must not clear `handled` and
    # re-offload a still-mounted card. Empty raw → skip the cycle.
    calls = []
    monkeypatch.setattr(offload, "run_offload", lambda *a, **k: (calls.append(1), (0, {}, "s"))[1])
    offload.run_card_watch(
        ["/dst"], interval=0, _loops=2,
        _mounts_fn=_seq({_SYS, "/Volumes/CARD"}, set(), {_SYS, "/Volumes/CARD"}),  # card handled, then probe blips empty
        _list_fn=_seq({"/Volumes/CARD"}, {"/Volumes/CARD"}))
    assert calls == []                                # baseline-handled card never re-offloaded


def test_watch_offload_failure_is_recorded_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("copy failed")
    monkeypatch.setattr(offload, "run_offload", boom)
    handled = offload.run_card_watch(
        ["/dst"], once=True,
        _mounts_fn=_seq({_SYS}, {_SYS, "/Volumes/CARD"}), _list_fn=_seq({"/Volumes/CARD"}))
    assert handled == [("/Volumes/CARD", 1)]          # failure recorded, didn't crash the loop


def test_watch_requires_dst():
    with pytest.raises(ValueError, match="dst"):
        offload.run_card_watch([], once=True, _list_fn=lambda: set())


def test_watch_validates_organize_template():
    with pytest.raises(ValueError):
        offload.run_card_watch(["/dst"], organize="no-tokens", once=True, _list_fn=lambda: set())
