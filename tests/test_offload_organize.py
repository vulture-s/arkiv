"""Tests for the DIT naming/folder policy — offload.py --organize (DIT wrapper ①).

`--organize "{date}/{camera}/{reel}"` lays files out by camera metadata instead of
mirroring the card, with the original filename appended. Token values are sanitized
to one safe path segment (no separators / traversal) and same-destination collisions
are refused (DIT must never silently overwrite).
"""
import importlib
from pathlib import Path

import pytest

offload = importlib.import_module("offload")


# ── _sanitize_token ─────────────────────────────────────────────────────────
def test_sanitize_strips_separators():
    assert "/" not in offload._sanitize_token("Sony/FX30")
    assert "\\" not in offload._sanitize_token("a\\b")


def test_sanitize_blocks_traversal():
    assert ".." not in offload._sanitize_token("../../etc")
    assert offload._sanitize_token("..") == "UNKNOWN" or ".." not in offload._sanitize_token("..")


def test_sanitize_empty_and_none_become_unknown():
    assert offload._sanitize_token("") == "UNKNOWN"
    assert offload._sanitize_token(None) == "UNKNOWN"
    assert offload._sanitize_token("   ") == "UNKNOWN"


def test_sanitize_caps_length():
    assert len(offload._sanitize_token("x" * 200)) <= 64


# ── _organize_relpath ───────────────────────────────────────────────────────
def test_organize_builds_folders_keeps_filename():
    rel = offload._organize_relpath(
        Path("/card/DCIM/C0001.MP4"), "{date}/{camera}/{reel}",
        {"date": "2026-03-09", "camera": "Sony FX30", "reel": "A001"})
    assert rel == "2026-03-09/Sony FX30/A001/C0001.MP4"


def test_organize_missing_meta_is_unknown():
    rel = offload._organize_relpath(Path("/card/x.mov"), "{date}/{camera}", {})
    assert rel == "UNKNOWN/UNKNOWN/x.mov"


def test_organize_camera_with_slash_stays_one_segment():
    rel = offload._organize_relpath(Path("/card/x.mov"), "{camera}", {"camera": "Sony/FX30"})
    assert rel == "Sony_FX30/x.mov"          # not a nested directory


def test_organize_unknown_token_falls_back():
    rel = offload._organize_relpath(Path("/card/x.mov"), "{bogus}/{date}", {"date": "2026-01-01"})
    assert rel == "UNKNOWN/2026-01-01/x.mov"


def test_organize_stem_and_ext_tokens():
    rel = offload._organize_relpath(Path("/card/clip.MP4"), "{ext}/{stem}",
                                    {"date": None, "camera": None, "reel": None})
    assert rel == "MP4/clip/clip.MP4"


# ── _validate_organize_template ─────────────────────────────────────────────
def test_validate_rejects_tokenless_template():
    with pytest.raises(ValueError):
        offload._validate_organize_template("static/folder")


def test_validate_rejects_absolute_template():
    with pytest.raises(ValueError):
        offload._validate_organize_template("/{date}/{camera}")


def test_validate_accepts_templated():
    offload._validate_organize_template("{date}/{camera}/{reel}")  # no raise


def test_validate_rejects_drive_colon():
    # Codex bug #1: a drive/ADS colon must be rejected.
    with pytest.raises(ValueError):
        offload._validate_organize_template("C:/{date}")


# ── Codex bug #1: template literals can't escape dst_root (Windows backslash/drive)
def test_organize_relpath_backslash_literal_cannot_escape():
    rel = offload._organize_relpath(
        Path("/card/x.mov"), "out\\..\\..\\outside/{date}",
        {"date": "2026-01-01", "camera": None, "reel": None})
    # backslashes normalized, traversal stripped — stays inside, no ".." survives
    assert ".." not in rel
    assert "\\" not in rel
    assert rel.endswith("/x.mov")
    parts = rel.split("/")
    assert ".." not in parts and parts[0] not in ("", "..")


def test_organize_relpath_drive_literal_sanitized():
    rel = offload._organize_relpath(Path("/card/x.mov"), "{date}",
                                    {"date": "C:foo", "camera": None, "reel": None})
    assert ":" not in rel        # drive colon scrubbed out of the value


# ── _probe_camera_meta: Sony XAVC sidecar fallback ──────────────────────────
def test_probe_camera_meta_sidecar_fallback(tmp_path):
    mp4 = tmp_path / "FX30.5399.MP4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # exiftool extracts no Make/Model
    (tmp_path / "FX30.5399M01.XML").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">'
        '<Device manufacturer="Sony" modelName="ILME-FX30" serialNo="05000452"/>'
        '<CreationDate value="2026-03-09T13:13:09+08:00"/>'
        '</NonRealTimeMeta>', encoding="utf-8")
    meta = offload._probe_camera_meta(mp4)
    assert "FX30" in (meta["camera"] or "")
    assert meta["date"] == "2026-03-09"


def test_probe_camera_meta_never_raises_on_missing_file(tmp_path):
    # No exception, date falls back to None/mtime; camera None.
    meta = offload._probe_camera_meta(tmp_path / "does_not_exist.mov")
    assert isinstance(meta, dict) and "camera" in meta


# ── collision refusal + end-to-end layout ───────────────────────────────────
def test_organize_collision_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(offload, "_probe_camera_meta",
                        lambda p: {"date": "2026-01-01", "camera": "CamX", "reel": "R1"})
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    f1 = tmp_path / "a" / "C0001.MP4"; f2 = tmp_path / "b" / "C0001.MP4"
    f1.write_bytes(b"x"); f2.write_bytes(b"y")          # same name + same metadata → same dest
    state = {"source": str(tmp_path), "files": []}
    with pytest.raises(ValueError, match="collision"):
        offload._ensure_file_records(state, [f1, f2], [str(tmp_path / "dst")],
                                     organize="{date}/{camera}/{reel}")


def test_run_offload_organize_lays_out_files(monkeypatch, tmp_path):
    monkeypatch.setattr(offload, "_probe_camera_meta",
                        lambda p: {"date": "2026-03-09", "camera": "Sony FX30", "reel": "A001"})
    src = tmp_path / "card"; src.mkdir()
    (src / "C0001.MP4").write_bytes(b"hello")
    (src / "C0002.MP4").write_bytes(b"world")
    dst = tmp_path / "dst"; dst.mkdir()
    code, summary, _ = offload.run_offload(
        str(src), [str(dst)], organize="{date}/{camera}/{reel}",
        emit_mhl=False, progress="json", resume=str(tmp_path / "state.json"))
    assert (dst / "2026-03-09" / "Sony FX30" / "A001" / "C0001.MP4").read_bytes() == b"hello"
    assert (dst / "2026-03-09" / "Sony FX30" / "A001" / "C0002.MP4").read_bytes() == b"world"


# ── Codex bug #2: case-insensitive collision refused ────────────────────────
def test_organize_case_insensitive_collision_refused(monkeypatch, tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    f1 = tmp_path / "a" / "C0001.MP4"; f2 = tmp_path / "b" / "c0001.mp4"
    f1.write_bytes(b"x"); f2.write_bytes(b"y")
    # same metadata; names differ only by case → same file on macOS/Windows
    monkeypatch.setattr(offload, "_probe_camera_meta",
                        lambda p: {"date": "2026-01-01", "camera": "CamX", "reel": "R1"})
    state = {"source": str(tmp_path), "files": []}
    with pytest.raises(ValueError, match="collision"):
        offload._ensure_file_records(state, [f1, f2], [str(tmp_path / "dst")],
                                     organize="{date}/{camera}/{reel}")


# ── Codex bug #3: resume with a different --organize is refused ──────────────
def test_run_offload_resume_organize_mismatch_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(offload, "_probe_camera_meta",
                        lambda p: {"date": "2026-03-09", "camera": "Sony FX30", "reel": "A001"})
    src = tmp_path / "card"; src.mkdir(); (src / "C0001.MP4").write_bytes(b"hello")
    dst = tmp_path / "dst"; dst.mkdir()
    state_p = str(tmp_path / "state.json")
    offload.run_offload(str(src), [str(dst)], organize="{date}/{camera}", emit_mhl=False,
                        progress="json", resume=state_p)
    # resume the SAME state with a different template → refuse
    with pytest.raises(ValueError, match="mismatch"):
        offload.run_offload(str(src), [str(dst)], organize="{reel}/{camera}", emit_mhl=False,
                            progress="json", resume=state_p)
    # resume WITHOUT --organize → adopts the stored layout (no raise, no re-pass needed)
    code, _summary, _ = offload.run_offload(str(src), [str(dst)], emit_mhl=False,
                                            progress="json", resume=state_p)
    assert (dst / "2026-03-09" / "Sony FX30" / "C0001.MP4").exists()


def test_run_offload_without_organize_mirrors_source(tmp_path):
    src = tmp_path / "card"; (src / "DCIM").mkdir(parents=True)
    (src / "DCIM" / "C0001.MP4").write_bytes(b"hi")
    dst = tmp_path / "dst"; dst.mkdir()
    offload.run_offload(str(src), [str(dst)], emit_mhl=False, progress="json",
                        resume=str(tmp_path / "state.json"))
    assert (dst / "DCIM" / "C0001.MP4").read_bytes() == b"hi"   # mirror layout unchanged
