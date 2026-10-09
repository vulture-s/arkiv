"""Audit 2026-10-09 (E3 HIGH ×2): DIT offload must never report "done" for a card
it did not copy, and must never overwrite footage another card already backed up.

  * resume key: the API keyed its resumable state on the *mount path* alone, so a
    second card mounted at the same path (/Volumes/Untitled — every Sony card)
    reused card A's state: card B's files were never enumerated, the same-named
    C0001 was "skipped" as already verified, and the run reported done/code 0.
  * no-clobber: across runs (different state), a same-named file on a second card
    was `os.replace`d over the first card's verified copy — silently, code 0.
  * MHL: a file whose bytes changed since the previous manifest generation must
    fail the destination, not just be recorded as a new hash.
"""
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_offload(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    sys.modules.pop("mhl", None)
    sys.modules.pop("offload", None)
    return importlib.import_module("offload")


@pytest.fixture
def scratch():
    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="arkiv-offload-id-", dir=str(temp_root)))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _card(root, mapping):
    if root.exists():
        shutil.rmtree(root)
    for rel, payload in mapping.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
    return root


CARD_A = {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"card A clip1"}
CARD_B = {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"card B clip1 NEW",
          "PRIVATE/M4ROOT/CLIP/C0002.MP4": b"card B clip2 NEW"}


# ── engine: resume state from a different card is refused ───────────────────
def test_resume_with_state_from_a_different_card_is_refused(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    mount = scratch / "Volumes" / "Untitled"
    dst = scratch / "dst"
    st = scratch / "st.json"
    _card(mount, CARD_A)
    code, _s, _p = offload.run_offload(mount, [dst], resume=st, emit_mhl=False)
    assert code == 0

    _card(mount, CARD_B)  # eject A, insert B at the same mount point
    with pytest.raises(ValueError) as exc:
        offload.run_offload(mount, [dst], resume=st, emit_mhl=False)
    assert "does not match" in str(exc.value)
    # Card A's backup is untouched.
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0001.MP4").read_bytes() == b"card A clip1"


def test_resume_same_card_still_skips_verified(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    mount = scratch / "card"
    dst = scratch / "dst"
    st = scratch / "st.json"
    _card(mount, CARD_B)
    assert offload.run_offload(mount, [dst], resume=st, emit_mhl=False)[0] == 0
    code, summary, _ = offload.run_offload(mount, [dst], resume=st, emit_mhl=False)
    assert code == 0
    assert summary[str(dst.resolve())]["verified_files"] == 2


# ── engine: cross-run no-clobber ────────────────────────────────────────────
def test_second_card_same_name_never_overwrites_backup(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"DAY1 interview take"})
    c2 = _card(scratch / "card2", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"DAY2 broll totally different",
                                   "PRIVATE/M4ROOT/CLIP/C0002.MP4": b"DAY2 second clip"})
    assert offload.run_offload(c1, [dst], emit_mhl=False)[0] == 0
    code, summary, state_path = offload.run_offload(c2, [dst], emit_mhl=False)

    assert code != 0, "a refused file must not report success"
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0001.MP4").read_bytes() == b"DAY1 interview take"
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0002.MP4").read_bytes() == b"DAY2 second clip"
    s = summary[str(dst.resolve())]
    assert s["failed_files"] == 1 and s["verified_files"] == 1
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    rec = [f for f in state["files"] if f["rel"].endswith("C0001.MP4")][0]
    ds = rec["destinations"][str(dst.resolve())]
    assert ds["status"] == "conflict"
    assert "already exists" in ds["error"]
    assert not list(dst.rglob("*.partial"))


def test_second_card_organize_template_never_overwrites(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    monkeypatch.setattr(offload, "_probe_camera_meta_batch",
                        lambda paths: {str(p): {"date": None, "camera": None, "reel": None} for p in paths})
    dst = scratch / "dst2"
    c1 = _card(scratch / "card1", {"C0001.MP4": b"DAY1 interview take"})
    c2 = _card(scratch / "card2", {"C0001.MP4": b"DAY2 broll totally different"})
    assert offload.run_offload(c1, [dst], emit_mhl=False, organize="{camera}")[0] == 0
    code, _s, _p = offload.run_offload(c2, [dst], emit_mhl=False, organize="{camera}")
    assert code != 0
    assert (dst / "UNKNOWN/C0001.MP4").read_bytes() == b"DAY1 interview take"


def test_rerun_with_identical_file_is_idempotent(scratch, monkeypatch):
    # Same card offloaded again with a FRESH state (e.g. state file lost): files
    # already at the destination with identical bytes are verified, not conflicts.
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c = _card(scratch / "card", CARD_B)
    assert offload.run_offload(c, [dst], emit_mhl=False)[0] == 0
    code, summary, _ = offload.run_offload(c, [dst], emit_mhl=False)
    assert code == 0
    assert summary[str(dst.resolve())]["verified_files"] == 2


# ── MHL: history mismatch is caught ─────────────────────────────────────────
def test_mhl_flags_file_changed_since_previous_generation(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"A/C0001.MP4": b"DAY1 interview take"})
    code, summary, _ = offload.run_offload(c1, [dst])
    assert code == 0 and summary[str(dst.resolve())]["mhl_path"]

    # Something (another tool, a bad sync, a pre-fix arkiv) rewrote the backup.
    (dst / "A/C0001.MP4").write_bytes(b"DAY2 broll totally different")

    c2 = _card(scratch / "card2", {"B/C0001.MP4": b"unrelated new clip"})
    code, summary, _ = offload.run_offload(c2, [dst])
    s = summary[str(dst.resolve())]
    assert code != 0
    assert s["status"] == "failed"
    assert "A/C0001.MP4" in (s["error"] or "")

    # …and it must STAY failed: the tampered hash was written into the new
    # generation, so a previous-generation-only comparison would go green here
    # with the damage still on disk (#497 review C1).
    c3 = _card(scratch / "card3", {"C/C0001.MP4": b"yet another clip"})
    code, summary, _ = offload.run_offload(c3, [dst])
    s = summary[str(dst.resolve())]
    assert code != 0 and s["status"] == "failed"
    assert "A/C0001.MP4" in (s["error"] or "")


def test_mhl_history_ignores_os_metadata_churn_on_destination(scratch, monkeypatch):
    """Dst-side twin of B1: on an exFAT backup drive macOS keeps AppleDouble
    ``._*`` files (rewritten whenever an xattr changes — opening a clip in
    QuickTime stamps lastuseddate). The history check must not treat that churn
    as tampered footage, or every later offload to the drive goes red for good."""
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"A/C0001.MP4": b"day1 clip"})
    assert offload.run_offload(c1, [dst])[0] == 0
    (dst / "A/._C0001.MP4").write_bytes(b"appledouble v1")
    (dst / ".Spotlight-V100").mkdir()
    (dst / ".Spotlight-V100/store").write_bytes(b"idx v1")
    c2 = _card(scratch / "card2", {"B/C0001.MP4": b"day2 clip"})
    assert offload.run_offload(c2, [dst])[0] == 0
    (dst / "A/._C0001.MP4").write_bytes(b"appledouble v2 -- clip opened again")
    (dst / ".Spotlight-V100/store").write_bytes(b"idx v2")
    c3 = _card(scratch / "card3", {"C/C0001.MP4": b"day3 clip"})
    code, summary, _ = offload.run_offload(c3, [dst])
    s = summary[str(dst.resolve())]
    assert code == 0 and s["status"] == "done", s["error"]

    # …while real footage changing is still caught.
    (dst / "A/C0001.MP4").write_bytes(b"tampered!")
    c4 = _card(scratch / "card4", {"D/C0001.MP4": b"day4 clip"})
    code, summary, _ = offload.run_offload(c4, [dst])
    s = summary[str(dst.resolve())]
    assert code != 0 and "A/C0001.MP4" in (s["error"] or "")
    assert "._C0001" not in (s["error"] or "")


def test_os_metadata_sets_match_between_offload_and_mhl(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    import mhl
    assert offload._OS_METADATA_DIRS == mhl.OS_METADATA_DIRS
    assert offload._OS_METADATA_FILES == mhl.OS_METADATA_FILES


# ── OS metadata on the card (#497 review B1) ────────────────────────────────
def test_finder_metadata_is_not_footage(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    c = _card(scratch / "card", {"DCIM/C0001.MP4": b"clip",
                                 ".DS_Store": b"finder v1",
                                 "DCIM/._C0001.MP4": b"appledouble",
                                 ".Spotlight-V100/Store-V2/x": b"idx",
                                 ".fseventsd/0001": b"ev",
                                 ".Trashes/501/junk": b"t",
                                 "System Volume Information/IndexerVolumeGuid": b"w"})
    names = [p.name for p in offload._collect_sources(c)]
    assert names == ["C0001.MP4"]


def test_finder_touching_card_between_runs_keeps_resume_and_no_conflict(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    mount = scratch / "Volumes" / "Untitled"
    dst = scratch / "dst"
    st = scratch / "st.json"
    _card(mount, {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"clip one", ".DS_Store": b"finder v1"})
    assert offload.run_offload(mount, [dst], resume=st, emit_mhl=False)[0] == 0
    (mount / ".DS_Store").write_bytes(b"finder v2 -- DIT opened the card")
    (mount / "PRIVATE/M4ROOT/CLIP/._C0001.MP4").write_bytes(b"appledouble")
    code, summary, _ = offload.run_offload(mount, [dst], resume=st, emit_mhl=False)
    assert code == 0
    assert summary[str(dst.resolve())]["conflict_files"] == []
    assert not (dst / ".DS_Store").exists()


def test_api_same_card_after_finder_gets_same_state_key(fastapi_client, tmp_path, monkeypatch):
    import routers.offload as ro
    state_cwd = _state_dir(monkeypatch, tmp_path)
    mount = tmp_path / "Volumes" / "Untitled"
    _card(mount, {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"clip one", ".DS_Store": b"finder v1"})
    dst = tmp_path / "backup"; dst.mkdir()
    key1 = ro._offload_state_path(state_cwd, mount.resolve())
    _ = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]}).text
    (mount / ".DS_Store").write_bytes(b"finder v2")
    assert ro._offload_state_path(state_cwd, mount.resolve()) == key1  # resume still applies
    r = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]})
    done = [json.loads(l) for l in r.text.splitlines() if '"type": "done"' in l][-1]
    assert done["code"] == 0


# ── API: per-card state key ─────────────────────────────────────────────────
def _state_dir(monkeypatch, tmp_path):
    config = importlib.import_module("config")
    d = tmp_path / "arkiv-state" / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "THUMBNAILS_DIR", d)
    return d.parent


def test_api_second_card_at_same_mount_is_actually_copied(fastapi_client, tmp_path, monkeypatch):
    _state_dir(monkeypatch, tmp_path)
    mount = tmp_path / "Volumes" / "Untitled"
    _card(mount, CARD_A)
    dst_a = tmp_path / "backupA"; dst_a.mkdir()
    r = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst_a)]})
    assert r.status_code == 200
    _ = r.text

    _card(mount, CARD_B)
    dst_b = tmp_path / "backupB"; dst_b.mkdir()
    r = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst_b)]})
    assert r.status_code == 200
    events = [json.loads(l) for l in r.text.splitlines() if l.strip().startswith("{")]
    done = [e for e in events if e.get("type") == "done"][-1]
    assert done["code"] == 0
    assert (dst_b / "PRIVATE/M4ROOT/CLIP/C0001.MP4").read_bytes() == b"card B clip1 NEW"
    assert (dst_b / "PRIVATE/M4ROOT/CLIP/C0002.MP4").read_bytes() == b"card B clip2 NEW"


def test_api_second_card_same_mount_same_dst_reports_conflict(fastapi_client, tmp_path, monkeypatch):
    _state_dir(monkeypatch, tmp_path)
    mount = tmp_path / "Volumes" / "Untitled"
    dst = tmp_path / "backup"; dst.mkdir()
    _card(mount, CARD_A)
    _ = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]}).text
    _card(mount, CARD_B)
    r = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]})
    events = [json.loads(l) for l in r.text.splitlines() if l.strip().startswith("{")]
    done = [e for e in events if e.get("type") == "done"][-1]
    assert done["code"] != 0, "UI must not show done/green for a card that was not fully backed up"
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0001.MP4").read_bytes() == b"card A clip1"
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0002.MP4").read_bytes() == b"card B clip2 NEW"
    conflicts = [e for e in events if e.get("type") == "file" and e.get("reason") == "conflict"]
    assert len(conflicts) == 1 and conflicts[0]["status"] == "failed"  # old UIs count it as FAIL
    assert done["summary"][str(dst.resolve())]["conflict_files"] == ["PRIVATE/M4ROOT/CLIP/C0001.MP4"]


# ── dual-track audit of #497 (fallback reviewer) ────────────────────────────
import os


def _same_mtime(*paths):
    for p in paths:
        os.utime(p, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))


def test_cards_with_same_name_size_mtime_but_different_bytes_are_different_cards(
        scratch, monkeypatch, tmp_path):
    import routers.offload as ro
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    mount = scratch / "Volumes" / "Untitled"
    dst = scratch / "dst"
    _card(mount, {"CLIP/C0001.MP4": b"A" * 1000}); _same_mtime(mount / "CLIP/C0001.MP4")
    st_a = ro._offload_state_path(tmp_path, mount.resolve())
    assert offload.run_offload(mount, [dst], resume=st_a, emit_mhl=False)[0] == 0
    _card(mount, {"CLIP/C0001.MP4": b"B" * 1000}); _same_mtime(mount / "CLIP/C0001.MP4")
    st_b = ro._offload_state_path(tmp_path, mount.resolve())
    assert st_b != st_a  # router: a different card gets a fresh state
    with pytest.raises(ValueError):  # engine: card A's state is refused for card B
        offload.run_offload(mount, [dst], resume=st_a, emit_mhl=False)
    code, summary, _ = offload.run_offload(mount, [dst], resume=st_b, emit_mhl=False)
    assert code != 0 and summary[str(dst.resolve())]["conflict_files"]
    assert (dst / "CLIP/C0001.MP4").read_bytes() == b"A" * 1000


def test_resume_does_not_trust_a_swapped_destination_drive(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    card = _card(scratch / "card", {"CLIP/C0001.MP4": b"real footage 1234"})
    dst = scratch / "Volumes" / "SHUTTLE"
    st = scratch / "st.json"
    assert offload.run_offload(card, [dst], resume=st, emit_mhl=False)[0] == 0
    # a different shuttle drive with the same volume name, same clip name
    shutil.rmtree(dst)
    _card(dst, {"CLIP/C0001.MP4": b"other job ZZZZZZ!"})  # same size, different bytes
    code, summary, _ = offload.run_offload(card, [dst], resume=st, emit_mhl=False)
    assert code != 0
    assert summary[str(dst.resolve())]["conflict_files"] == ["CLIP/C0001.MP4"]
    # and a missing-then-recreated identical file is still fine
    _card(dst, {"CLIP/C0001.MP4": b"real footage 1234"})
    assert offload.run_offload(card, [dst], resume=st, emit_mhl=False)[0] == 0


def test_mhl_unreadable_generation_fails_closed(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"A/C0001.MP4": b"day1"})
    assert offload.run_offload(c1, [dst])[0] == 0
    first = sorted((dst / "ascmhl").glob("*.mhl"))[0]
    first.write_text(first.read_text()[:200])
    (dst / "A/C0001.MP4").write_bytes(b"tampered")
    c2 = _card(scratch / "card2", {"B/C0001.MP4": b"day2"})
    code, summary, _ = offload.run_offload(c2, [dst])
    s = summary[str(dst.resolve())]
    assert code != 0 and s["status"] == "failed" and "unreadable" in (s["error"] or "")


def test_mhl_history_with_no_common_algorithm_is_not_a_pass(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"A/C0001.MP4": b"day1"})
    assert offload.run_offload(c1, [dst])[0] == 0
    (dst / "A/C0001.MP4").write_bytes(b"tampered")
    c2 = _card(scratch / "card2", {"B/C0001.MP4": b"day2"})
    code, summary, _ = offload.run_offload(c2, [dst], hash_algo="md5")
    assert code != 0 and "A/C0001.MP4" in (summary[str(dst.resolve())]["error"] or "")


def test_card_under_a_folder_named_ascmhl_is_still_copied(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    card = _card(scratch / "ascmhl" / "card", {"CLIP/C0001.MP4": b"clip", "ascmhl/x.mhl": b"m"})
    dst = scratch / "dst"
    code, summary, _ = offload.run_offload(card, [dst], emit_mhl=False)
    assert code == 0 and summary[str(dst.resolve())]["verified_files"] == 1


def test_empty_source_is_refused_not_done(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    card = _card(scratch / "card", {"DCIM/IMG_0001.HEIC": b"heic only"})
    with pytest.raises(ValueError):
        offload.run_offload(card, [scratch / "dst"], emit_mhl=False)


def test_orphan_truncated_mhl_from_an_interrupted_run_does_not_poison_the_drive(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"A/C0001.MP4": b"day1"})
    assert offload.run_offload(c1, [dst])[0] == 0
    # killed between manifest write and chain append: an unlisted, truncated .mhl
    (dst / "ascmhl" / "0002_dst_2000-01-01_000000Z.mhl").write_text("<?xml version")
    for n in (2, 3):
        c = _card(scratch / "card{0}".format(n), {"D{0}/C0001.MP4".format(n): b"day %d" % n})
        code, summary, _ = offload.run_offload(c, [dst])
        assert code == 0, summary[str(dst.resolve())]["error"]
    # tampering is still caught (the chain-listed history is intact)
    (dst / "A/C0001.MP4").write_bytes(b"tampered")
    c4 = _card(scratch / "card4", {"D4/C0001.MP4": b"day4"})
    assert offload.run_offload(c4, [dst])[0] != 0


def test_mhl_writes_leave_no_temp_files(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"A/C0001.MP4": b"day1"})
    assert offload.run_offload(c1, [dst])[0] == 0
    assert not [p for p in (dst / "ascmhl").iterdir() if p.name.endswith(".tmp")]


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="chmod 000 is not enforced here")
def test_one_unreadable_clip_does_not_block_the_card(scratch, monkeypatch, tmp_path):
    import routers.offload as ro
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    card = _card(scratch / "card", {"CLIP/C0001.MP4": b"ok one", "CLIP/C0002.MP4": b"locked"})
    locked = card / "CLIP/C0002.MP4"
    locked.chmod(0)
    try:
        st = ro._offload_state_path(tmp_path, card.resolve())  # no PermissionError → no 400
        dst = scratch / "dst"
        code, summary, _ = offload.run_offload(card, [dst], resume=st, emit_mhl=False)
        s = summary[str(dst.resolve())]
        assert code != 0 and s["verified_files"] == 1 and s["failed_files"] == 1
        assert (dst / "CLIP/C0001.MP4").read_bytes() == b"ok one"
    finally:
        locked.chmod(0o644)


def test_cli_resume_with_pre_sampling_state_is_refused(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    card = _card(scratch / "card", {"CLIP/C0001.MP4": b"clip"})
    st = scratch / "st.json"
    assert offload.run_offload(card, [scratch / "dst"], resume=st, emit_mhl=False)[0] == 0
    data = json.loads(st.read_text())
    for fe in data["files"]:
        fe.pop("sample")
    st.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        offload.run_offload(card, [scratch / "dst"], resume=st, emit_mhl=False)


def test_deleting_the_chain_does_not_erase_the_baseline(scratch, monkeypatch):
    offload = _load_offload(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"A/C0001.MP4": b"day1"})
    assert offload.run_offload(c1, [dst])[0] == 0
    (dst / "ascmhl" / "ascmhl_chain.xml").unlink()
    (dst / "A/C0001.MP4").write_bytes(b"tampered")
    c2 = _card(scratch / "card2", {"B/C0001.MP4": b"day2"})
    assert offload.run_offload(c2, [dst])[0] != 0
    c3 = _card(scratch / "card3", {"C/C0001.MP4": b"day3"})
    assert offload.run_offload(c3, [dst])[0] != 0  # stays red
