"""How many times the MHL passes read every destination byte (#535).

On a 117 GB card over Wi-Fi SMB each full pass is ~2 h, so the pass count IS the
offload's wall time. These pin it: verify reads each file once (it used to be
twice), and a manifest written right after an offload reuses the hashes the copy
already took (it used to re-read everything).
"""
import importlib
import os
import time
from pathlib import Path

import pytest

mhl = importlib.import_module("mhl")
offload = importlib.import_module("offload")


def _tree(root: Path):
    (root / "A001").mkdir(parents=True)
    (root / "A001" / "clip_001.mp4").write_bytes(b"alpha" * 1000)
    (root / "A001" / "clip_002.mp4").write_bytes(b"bravo" * 1000)
    (root / "notes.txt").write_bytes(b"charlie")
    return root


@pytest.fixture
def counted(monkeypatch):
    calls = []
    real = mhl.hash_file

    def spy(path, algo):
        calls.append(Path(path).name)
        return real(path, algo)

    monkeypatch.setattr(mhl, "hash_file", spy)
    return calls


def test_verify_reads_each_file_once(tmp_path, counted):
    src = _tree(tmp_path / "card")
    mhl_path, _ = mhl.create_manifest(src, output=src / "ascmhl", primary_hash="xxh3")
    counted.clear()
    assert mhl.verify_manifest(mhl_path) == 0
    assert sorted(counted) == ["clip_001.mp4", "clip_002.mp4", "notes.txt"]


def test_verify_still_catches_a_changed_byte(tmp_path):
    src = _tree(tmp_path / "card")
    mhl_path, _ = mhl.create_manifest(src, output=src / "ascmhl", primary_hash="xxh3")
    (src / "A001" / "clip_002.mp4").write_bytes(b"BRAVO" * 1000)
    assert mhl.verify_manifest(mhl_path) == 2


def _known(root: Path, rel: str, algo="xxh3"):
    st = (root / rel).stat()
    return (st.st_size, st.st_mtime_ns, {algo: mhl.hash_file(root / rel, algo)})


def test_known_hashes_skip_the_reread_and_give_the_same_manifest(tmp_path, counted):
    a = _tree(tmp_path / "a")
    plain, _ = mhl.create_manifest(a, output=tmp_path / "out-plain", primary_hash="xxh3")
    known = {r: _known(a, r) for r in ("A001/clip_001.mp4", "A001/clip_002.mp4")}
    counted.clear()
    reused, _ = mhl.create_manifest(a, output=tmp_path / "out-reused", primary_hash="xxh3", known_hashes=known)
    assert counted == ["notes.txt"]  # only the file nobody had hashed
    root = lambda p: mhl._manifest_root(mhl._parse_manifest(p))
    assert root(reused) == root(plain)


def test_known_hash_is_ignored_once_the_file_changed(tmp_path, counted):
    a = _tree(tmp_path / "a")
    rel = "A001/clip_001.mp4"
    stale = _known(a, rel)
    time.sleep(0.01)
    (a / rel).write_bytes(b"ALPHA" * 1000)  # same size, new bytes, new mtime
    counted.clear()
    mhl_path, _ = mhl.create_manifest(a, output=a / "ascmhl", primary_hash="xxh3", known_hashes={rel: stale})
    assert "clip_001.mp4" in counted
    assert mhl.verify_manifest(mhl_path) == 0  # manifest describes the NEW bytes


def test_known_hash_is_ignored_when_mtime_differs_even_if_size_matches(tmp_path, counted):
    a = _tree(tmp_path / "a")
    rel = "A001/clip_001.mp4"
    size, mtime_ns, hashes = _known(a, rel)
    counted.clear()
    mhl.create_manifest(a, output=tmp_path / "out", primary_hash="xxh3",
                        known_hashes={rel: (size, mtime_ns + 1, {"xxh3": "0" * 16})})
    assert "clip_001.mp4" in counted


def test_known_hash_lookup_is_unicode_normalised(tmp_path, counted):
    # macOS / SMB can hand back NFD names; offload records rel paths in NFC.
    import unicodedata
    a = tmp_path / "a"
    a.mkdir()
    nfd = unicodedata.normalize("NFD", "那邊é.mp4")
    (a / nfd).write_bytes(b"x" * 100)
    child_rel = next(a.iterdir()).name
    st = (a / child_rel).stat()
    known = {unicodedata.normalize("NFC", child_rel): (st.st_size, st.st_mtime_ns,
                                                     {"xxh3": mhl.hash_file(a / child_rel, "xxh3")})}
    counted.clear()
    mhl.create_manifest(a, output=tmp_path / "out", primary_hash="xxh3", known_hashes=known)
    assert counted == []


def test_offload_mhl_phase_reads_destination_once(tmp_path, monkeypatch):
    """End to end: copy (reads each dst file back once to verify) → manifest (reuses
    those hashes) → verify (one more read). 2 reads per file, was 4."""
    src = _tree(tmp_path / "card")
    dst = tmp_path / "nas"
    dst.mkdir()
    reads = []
    real = mhl.hash_file

    def spy(path, algo):
        p = Path(path)
        if str(p).startswith(str(dst)):
            reads.append(p.name.replace(".partial", ""))
        return real(path, algo)

    monkeypatch.setattr(mhl, "hash_file", spy)
    monkeypatch.chdir(tmp_path)
    offload.run_offload(str(src), [str(dst)], progress="json")
    per_file = {n: reads.count(n) for n in ("clip_001.mp4", "clip_002.mp4", "notes.txt")}
    assert per_file == {"clip_001.mp4": 2, "clip_002.mp4": 2, "notes.txt": 2}, per_file
    assert (dst / "ascmhl").is_dir()
