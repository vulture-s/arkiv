"""Same-name conflict at the destination → needs_rename → the user picks a name.

Hevin 2026-10-09 23:14: a second card whose C0001.MP4 differs from the one
already backed up must NOT overwrite it (#497) and must NOT be silently left
behind either. The run reports `needs_rename` (never done), each conflict
carries enough to decide (both sides' size / mtime / hash prefix) plus a
predictable suggested name, and a follow-up call copies just that one clip under
the chosen name — hash-verified, recorded in the MHL with its original path.
The card only reads as done once every conflict is resolved and verified.
"""
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLIP = "PRIVATE/M4ROOT/CLIP/C0001.MP4"


def _load_offload(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    sys.modules.pop("mhl", None)
    sys.modules.pop("offload", None)
    return importlib.import_module("offload")


@pytest.fixture
def scratch():
    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="arkiv-offload-rename-", dir=str(temp_root)))
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


def _two_cards(scratch, offload, extra_b=None, emit_mhl=True):
    """Card 1 backed up, then card 2 (same C0001 name, different bytes) → conflict."""
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {CLIP: b"DAY1 interview take"})
    b = {CLIP: b"DAY2 broll totally different", "PRIVATE/M4ROOT/CLIP/C0002.MP4": b"DAY2 second"}
    b.update(extra_b or {})
    c2 = _card(scratch / "card2", b)
    assert offload.run_offload(c1, [dst], emit_mhl=emit_mhl, resume=scratch / "st1.json")[0] == 0
    st = scratch / "st2.json"
    code, summary, _ = offload.run_offload(c2, [dst], emit_mhl=emit_mhl, resume=st)
    return dst, c2, st, code, summary


# ── engine: the run itself ──────────────────────────────────────────────────
def test_conflict_is_needs_rename_with_decision_info(scratch, monkeypatch):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst, c2, st, code, summary = _two_cards(scratch, offload)
    s = summary[str(dst.resolve())]

    assert code == offload.EXIT_NEEDS_RENAME and code != 0
    assert s["status"] == "needs_rename"
    assert offload.overall_status(summary) == "needs_rename"
    assert s["failed_files"] == 0, "a name conflict is not a copy failure"
    assert (dst / CLIP).read_bytes() == b"DAY1 interview take"  # never overwritten
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0002.MP4").read_bytes() == b"DAY2 second"

    [item] = s["needs_rename"]
    assert item["rel"] == CLIP
    assert item["source"] == str(c2 / CLIP)
    assert item["suggested_name"] == "C0001 (2).MP4"
    assert item["suggested_rel"] == "PRIVATE/M4ROOT/CLIP/C0001 (2).MP4"
    assert item["existing"]["size"] == len(b"DAY1 interview take")
    assert item["incoming"]["size"] == len(b"DAY2 broll totally different")
    assert item["existing"]["mtime"] and item["incoming"]["mtime"]
    assert len(item["existing"]["hash_prefix"]) >= 8

    state = json.loads(Path(st).read_text(encoding="utf-8"))
    rec = [f for f in state["files"] if f["rel"] == CLIP][0]
    assert rec["destinations"][str(dst.resolve())]["status"] == "needs_rename"


def test_suggested_name_skips_names_already_taken(scratch, monkeypatch):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    # "C0001 (2).MP4" already on the drive AND "C0001 (3).MP4" is a file on the
    # incoming card itself — the suggestion must avoid both.
    _card(dst, {CLIP: b"old one", "PRIVATE/M4ROOT/CLIP/C0001 (2).MP4": b"older"})
    c2 = _card(scratch / "card2", {CLIP: b"new and different",
                                   "PRIVATE/M4ROOT/CLIP/C0001 (3).MP4": b"also on card"})
    code, summary, _ = offload.run_offload(c2, [dst], emit_mhl=False, resume=scratch / "st.json")
    [item] = summary[str(dst.resolve())]["needs_rename"]
    assert item["suggested_name"] == "C0001 (4).MP4"
    assert not (dst / "PRIVATE/M4ROOT/CLIP/C0001 (4).MP4").exists()


def test_suggestion_is_stable_across_reruns(scratch, monkeypatch):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst, c2, st, _code, s1 = _two_cards(scratch, offload, emit_mhl=False)
    _code2, s2, _ = offload.run_offload(c2, [dst], emit_mhl=False, resume=st)
    k = str(dst.resolve())
    assert s1[k]["needs_rename"][0]["suggested_name"] == s2[k]["needs_rename"][0]["suggested_name"]


# ── engine: resolving a conflict ────────────────────────────────────────────
def test_rename_copies_verifies_and_records_original_path_in_mhl(scratch, monkeypatch):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst, c2, st, _code, _s = _two_cards(scratch, offload)

    res = offload.resolve_conflict(st, dst, CLIP, "rename", new_name="C0001_cardB.MP4")

    new = dst / "PRIVATE/M4ROOT/CLIP/C0001_cardB.MP4"
    assert new.read_bytes() == b"DAY2 broll totally different"
    assert (dst / CLIP).read_bytes() == b"DAY1 interview take"
    assert res["status"] == "done"
    ds = res["summary"][str(dst.resolve())]
    assert ds["status"] == "done" and ds["needs_rename"] == []
    assert ds["renamed"] == [{"from": CLIP, "to": "PRIVATE/M4ROOT/CLIP/C0001_cardB.MP4"}]

    mhl_path = Path(ds["mhl_path"])
    import mhl
    assert mhl.verify_manifest(mhl_path) == 0
    ns = "{urn:ASC:MHL:v2.0}"
    root = ET.parse(mhl_path).getroot()
    entries = {h.find(ns + "path").text: h for h in root.iter(ns + "hash")}
    entry = entries["PRIVATE/M4ROOT/CLIP/C0001_cardB.MP4"]
    assert entry.find(ns + "previouspath").text == CLIP
    # the other files carry no previouspath
    assert entries[CLIP].find(ns + "previouspath") is None

    state = json.loads(Path(st).read_text(encoding="utf-8"))
    rec = [f for f in state["files"] if f["rel"] == CLIP][0]
    fs = rec["destinations"][str(dst.resolve())]
    assert fs["status"] == "verified" and fs["src_hash"] == fs["dst_hash"]
    assert fs["dst_rel"] == "PRIVATE/M4ROOT/CLIP/C0001_cardB.MP4"

    # A later resume of the same card is done (uses the renamed path, no re-ask).
    code, summary, _ = offload.run_offload(c2, [dst], resume=st)
    assert code == 0 and offload.overall_status(summary) == "done"


@pytest.mark.parametrize("bad", [
    "../escape.MP4", "..", ".", "", "   ", "sub/C0001.MP4", "sub\\C0001.MP4",
    "/abs.MP4", "C0001.MP4\x00x", ".hidden.MP4", "x.partial",
])
def test_rename_rejects_traversal_and_bad_names(scratch, monkeypatch, bad):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst, c2, st, _code, _s = _two_cards(scratch, offload, emit_mhl=False)
    before = sorted(p.relative_to(scratch).as_posix() for p in scratch.rglob("*") if p.is_file()
                    and not p.name.startswith("st"))
    with pytest.raises(ValueError):
        offload.resolve_conflict(st, dst, CLIP, "rename", new_name=bad)
    after = sorted(p.relative_to(scratch).as_posix() for p in scratch.rglob("*") if p.is_file()
                   and not p.name.startswith("st"))
    assert before == after, "a rejected name must not write anything"


@pytest.mark.parametrize("taken", ["C0001.MP4", "c0001.mp4", "C0002.MP4"])
def test_rename_to_a_taken_name_is_refused(scratch, monkeypatch, taken):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst, c2, st, _code, _s = _two_cards(scratch, offload, emit_mhl=False)
    with pytest.raises(ValueError) as exc:
        offload.resolve_conflict(st, dst, CLIP, "rename", new_name=taken)
    assert "exists" in str(exc.value) or "taken" in str(exc.value)
    assert (dst / CLIP).read_bytes() == b"DAY1 interview take"
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0002.MP4").read_bytes() == b"DAY2 second"


def test_rename_refused_for_a_file_that_is_not_pending(scratch, monkeypatch):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst, c2, st, _code, _s = _two_cards(scratch, offload, emit_mhl=False)
    with pytest.raises(ValueError):
        offload.resolve_conflict(st, dst, "PRIVATE/M4ROOT/CLIP/C0002.MP4", "rename", new_name="x.MP4")


def test_done_only_after_every_conflict_is_resolved(scratch, monkeypatch):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst = scratch / "dst"
    c1 = _card(scratch / "card1", {"C/A.MP4": b"one-A", "C/B.MP4": b"one-B"})
    c2 = _card(scratch / "card2", {"C/A.MP4": b"two-A!", "C/B.MP4": b"two-B!"})
    assert offload.run_offload(c1, [dst], resume=scratch / "s1.json")[0] == 0
    st = scratch / "s2.json"
    code, summary, _ = offload.run_offload(c2, [dst], resume=st)
    items = summary[str(dst.resolve())]["needs_rename"]
    assert code == offload.EXIT_NEEDS_RENAME and len(items) == 2
    # both suggestions are distinct and unused
    assert len({i["suggested_rel"].casefold() for i in items}) == 2

    r1 = offload.resolve_conflict(st, dst, "C/A.MP4", "rename", new_name=items[0]["suggested_name"])
    assert r1["status"] == "needs_rename", "one conflict left → still not done"
    r2 = offload.resolve_conflict(st, dst, "C/B.MP4", "rename", new_name=items[1]["suggested_name"])
    assert r2["status"] == "done"


def test_skip_requires_confirmation_and_leaves_card_incomplete(scratch, monkeypatch):
    offload = _load_offload(monkeypatch)
    monkeypatch.chdir(scratch)
    dst, c2, st, _code, _s = _two_cards(scratch, offload, emit_mhl=False)
    with pytest.raises(ValueError):
        offload.resolve_conflict(st, dst, CLIP, "skip")
    res = offload.resolve_conflict(st, dst, CLIP, "skip", confirm=True)
    assert res["status"] == "incomplete", "a skipped clip is NOT backed up — never done"
    ds = res["summary"][str(dst.resolve())]
    assert ds["status"] == "incomplete"
    assert ds["skipped_unbacked"] == [CLIP]
    assert (dst / CLIP).read_bytes() == b"DAY1 interview take"
    assert not list((dst / "PRIVATE/M4ROOT/CLIP").glob("C0001 (*"))


# ── API ─────────────────────────────────────────────────────────────────────
def _state_dir(monkeypatch, tmp_path):
    config = importlib.import_module("config")
    d = tmp_path / "arkiv-state" / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "THUMBNAILS_DIR", d)
    return d.parent


def _done(r):
    events = [json.loads(l) for l in r.text.splitlines() if l.strip().startswith("{")]
    return events, [e for e in events if e.get("type") == "done"][-1]


def test_api_run_then_resolve(fastapi_client, tmp_path, monkeypatch):
    _state_dir(monkeypatch, tmp_path)
    mount = tmp_path / "Volumes" / "Untitled"
    dst = tmp_path / "backup"; dst.mkdir()
    _card(mount, {CLIP: b"card A clip1"})
    _ = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]}).text
    _card(mount, {CLIP: b"card B clip1 NEW", "PRIVATE/M4ROOT/CLIP/C0002.MP4": b"card B clip2"})
    events, done = _done(fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]}))

    assert done["code"] != 0 and done["status"] == "needs_rename"
    [item] = done["needs_rename"]
    assert item["dst"] == str(dst.resolve()) and item["rel"] == CLIP
    fev = [e for e in events if e.get("type") == "file" and e.get("name") == "C0001.MP4"][0]
    assert fev["status"] == "needs_rename"

    bad = fastapi_client.post("/api/offload/resolve", json={
        "src": str(mount), "dst": item["dst"], "rel": CLIP, "action": "rename",
        "new_name": "../../evil.MP4"})
    assert bad.status_code == 400
    clash = fastapi_client.post("/api/offload/resolve", json={
        "src": str(mount), "dst": item["dst"], "rel": CLIP, "action": "rename",
        "new_name": "C0002.MP4"})
    assert clash.status_code == 400

    ok = fastapi_client.post("/api/offload/resolve", json={
        "src": str(mount), "dst": item["dst"], "rel": CLIP, "action": "rename",
        "new_name": item["suggested_name"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "done"
    assert (dst / "PRIVATE/M4ROOT/CLIP/C0001 (2).MP4").read_bytes() == b"card B clip1 NEW"
    assert (dst / CLIP).read_bytes() == b"card A clip1"


def test_api_skip_needs_confirm(fastapi_client, tmp_path, monkeypatch):
    _state_dir(monkeypatch, tmp_path)
    mount = tmp_path / "Volumes" / "Untitled"
    dst = tmp_path / "backup"; dst.mkdir()
    _card(mount, {CLIP: b"card A clip1"})
    _ = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]}).text
    _card(mount, {CLIP: b"card B clip1 NEW"})
    _ = fastapi_client.post("/api/offload", json={"src": str(mount), "dst": [str(dst)]}).text
    body = {"src": str(mount), "dst": str(dst), "rel": CLIP, "action": "skip"}
    assert fastapi_client.post("/api/offload/resolve", json=body).status_code == 400
    r = fastapi_client.post("/api/offload/resolve", json=dict(body, confirm=True))
    assert r.status_code == 200 and r.json()["status"] == "incomplete"
