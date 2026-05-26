import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "safe.directory=C:/Users/user/.arkiv", "-C", str(ROOT)]


def _bootstrap_mhl(tmp_path, monkeypatch):
    mhl_src = subprocess.run(
        GIT + ["show", "feat/13.1-mhl-v2:mhl.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    module_dir = tmp_path / "bootstrap"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "mhl.py").write_text(mhl_src, encoding="utf-8")
    monkeypatch.syspath_prepend(str(module_dir))
    sys.modules.pop("mhl", None)
    sys.modules.pop("offload", None)
    offload = importlib.import_module("offload")
    mhl = importlib.import_module("mhl")
    return offload, mhl


@pytest.fixture
def scratch(monkeypatch):
    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="arkiv-offload-", dir=str(temp_root)))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _write_tree(root, mapping):
    for rel, payload in mapping.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _patch_config_root(monkeypatch, root):
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "_ASCMHL_DIR", root / "ascmhl")


def _read_state(state_path):
    return json.loads(Path(state_path).read_text(encoding="utf-8"))


def test_two_destination_copy_and_mhl_verify(scratch, monkeypatch):
    offload, mhl = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)

    src = scratch / "src"
    dst1 = scratch / "dst1"
    dst2 = scratch / "dst2"
    _write_tree(
        src,
        {
            "A001/clip_001.mp4": b"alpha",
            "A001/clip_002.mp4": b"bravo",
            "B002/clip_003.mov": b"charlie",
            "B002/still_001.jpg": b"delta",
            "README.txt": b"echo",
        },
    )

    code, summary, state_path = offload.run_offload(
        src,
        [dst1, dst2],
        retry_limit=3,
        verify=True,
        emit_mhl=True,
        include_heic=True,
    )

    assert code == 0
    state = _read_state(state_path)
    assert state["destinations"][str(dst1)]["status"] == "done"
    assert state["destinations"][str(dst2)]["status"] == "done"
    assert summary[str(dst1)]["verified_files"] == 5
    assert summary[str(dst2)]["verified_files"] == 5

    for dst in (dst1, dst2):
        for rel in [
            "A001/clip_001.mp4",
            "A001/clip_002.mp4",
            "B002/clip_003.mov",
            "B002/still_001.jpg",
            "README.txt",
        ]:
            assert (dst / rel).exists()
        mhl_path = Path(summary[str(dst)]["mhl_path"])
        _patch_config_root(monkeypatch, dst)
        code, count, message = mhl.verify_mhl(mhl_path)
        assert code == 0
        assert count == 5
        assert message == "OK: 5 files verified"


def test_hash_mismatch_marks_unverified_after_retries(scratch, monkeypatch):
    offload, _ = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)

    src = scratch / "src"
    dst = scratch / "dst"
    _write_tree(src, {"A001/clip_001.mp4": b"alpha-bravo-charlie"})

    def mutate(src_path, partial_path, attempt, bytes_copied):
        data = bytearray(partial_path.read_bytes())
        if data:
            data[0] ^= 0x01
            partial_path.write_bytes(bytes(data))

    offload.TEST_MUTATOR = mutate
    code, summary, state_path = offload.run_offload(
        src,
        [dst],
        retry_limit=3,
        verify=True,
        emit_mhl=True,
    )
    offload.TEST_MUTATOR = None

    assert code == 2
    state = _read_state(state_path)
    file_state = state["files"][0]["destinations"][str(dst)]
    assert file_state["status"] == "unverified"
    assert file_state["attempts"] == 3
    assert state["destinations"][str(dst)]["failed_files"] == 1
    assert summary[str(dst)]["failed_files"] == 1
    assert not (dst / "A001" / "clip_001.mp4").exists()


def test_resume_picks_up_pending_from_state(scratch, monkeypatch):
    offload, _ = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)

    src = scratch / "src"
    dst = scratch / "dst"
    _write_tree(
        src,
        {
            "A001/clip_001.mp4": b"a" * 8192,
            "A001/clip_002.mp4": b"b" * 1024,
        },
    )
    state_path = scratch / "resume-state.json"

    triggered = {"value": False}

    def stop_mid_copy(src_path, partial_path, attempt, bytes_copied):
        if not triggered["value"] and bytes_copied >= 1024:
            triggered["value"] = True
            raise KeyboardInterrupt()

    offload.TEST_MUTATOR = stop_mid_copy
    with pytest.raises(KeyboardInterrupt):
        offload.run_offload(
            src,
            [dst],
            retry_limit=3,
            verify=False,
            emit_mhl=False,
            resume=state_path,
            chunk_size=1024,
        )

    state = _read_state(state_path)
    first_file = state["files"][0]["destinations"][str(dst)]
    assert first_file["status"] == "copying"
    assert first_file["bytes_copied"] >= 1024

    offload.TEST_MUTATOR = None
    code, summary, _ = offload.run_offload(
        src,
        [dst],
        retry_limit=3,
        verify=True,
        emit_mhl=True,
        resume=state_path,
        chunk_size=1024,
    )

    assert code == 0
    resumed = _read_state(state_path)
    assert resumed["destinations"][str(dst)]["status"] == "done"
    assert summary[str(dst)]["verified_files"] == 2
    assert (dst / "A001" / "clip_001.mp4").exists()
    assert (dst / "A001" / "clip_002.mp4").exists()


def test_source_unmount_cleans_partials_and_keeps_completed_files(scratch, monkeypatch):
    offload, _ = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)

    src = scratch / "src"
    dst = scratch / "dst"
    _write_tree(
        src,
        {
            "A001/clip_001.mp4": b"a" * 4096,
            "A001/clip_002.mp4": b"b" * 4096,
        },
    )

    def unmount_mid_copy(src_path, partial_path, attempt, bytes_copied):
        if src_path.name == "clip_002.mp4" and bytes_copied >= 1024:
            raise OSError("source unmounted")

    offload.TEST_MUTATOR = unmount_mid_copy
    code, summary, state_path = offload.run_offload(
        src,
        [dst],
        retry_limit=3,
        verify=False,
        emit_mhl=False,
        chunk_size=1024,
    )
    offload.TEST_MUTATOR = None

    assert code == 2
    assert (dst / "A001" / "clip_001.mp4").exists()
    assert not (dst / "A001" / "clip_002.mp4").exists()
    assert not list(dst.rglob("*.partial"))

    state = _read_state(state_path)
    assert state["files"][0]["destinations"][str(dst)]["status"] == "verified"
    assert state["files"][1]["destinations"][str(dst)]["status"] == "unverified"
    assert summary[str(dst)]["failed_files"] == 1


def test_sidecar_families_all_copy(scratch, monkeypatch):
    offload, mhl = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)

    src = scratch / "src"
    dst = scratch / "dst"
    files = {
        "A001/clip.MOV": b"mov",
        "A001/clip.HEIC": b"heic",
        "B002/scene.mp4": b"mp4",
        "B002/scene.srt": b"srt",
        "C003/take.wav": b"wav",
        "D004/roll.R3D": b"r3d",
        "D004/roll.R3D.HDRI": b"hdr",
        "D004/roll.hrm": b"hrm",
        "E005/camera.XML": b"xml",
        "README.txt": b"readme",
    }
    _write_tree(src, files)

    code, summary, state_path = offload.run_offload(
        src,
        [dst],
        retry_limit=3,
        verify=True,
        emit_mhl=True,
        include_heic=True,
    )

    assert code == 0
    state = _read_state(state_path)
    assert state["destinations"][str(dst)]["verified_files"] == len(files)
    for rel in files:
        assert (dst / rel).exists()

    _patch_config_root(monkeypatch, dst)
    mhl_path = Path(summary[str(dst)]["mhl_path"])
    code, count, message = mhl.verify_mhl(mhl_path)
    assert code == 0
    assert count == len(files)
    assert message == "OK: {0} files verified".format(len(files))
