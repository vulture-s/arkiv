import importlib
from datetime import datetime, timezone

import xml.etree.ElementTree as ET


def _patch_project_root(monkeypatch, tmp_path):
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "_ASCMHL_DIR", tmp_path / "ascmhl")


def _write_sample_tree(root):
    (root / "A001").mkdir(parents=True, exist_ok=True)
    (root / "B002").mkdir(parents=True, exist_ok=True)
    files = {
        root / "A001" / "clip_001.mp4": b"alpha",
        root / "A001" / "clip_002.mp4": b"bravo",
        root / "B002" / "clip_003.mov": b"charlie",
        root / "B002" / "still_001.jpg": b"delta",
        root / "README.txt": b"echo",
    }
    for path, data in files.items():
        path.write_bytes(data)
    return files


def test_db_schema_has_mhl_columns(tmp_db):
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(media)").fetchall()}
    assert {"file_hash", "hash_algo", "hash_verified_at"}.issubset(cols)


def test_create_emits_valid_xml_and_relative_paths(tmp_path, monkeypatch):
    _patch_project_root(monkeypatch, tmp_path)
    _write_sample_tree(tmp_path)
    mhl = importlib.import_module("mhl")
    importlib.reload(mhl)

    out, count = mhl.create_mhl(
        source=tmp_path,
        op="ingest",
        primary_hash="xxh3",
        secondary_hash="md5",
        author="hevin",
        now=datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc),
    )

    assert count == 5
    assert out.name == "001_ingest_2026-05-26.mhl"
    ET.parse(str(out))
    text = out.read_text(encoding="utf-8")
    assert 'file="A001/clip_001.mp4"' in text
    assert 'file="./A001/clip_001.mp4"' not in text
    assert 'file="/' not in text
    assert '<mhl xmlns="http://www.mhl.media" version="2.0">' in text


def test_verify_chain_passes_three_generations(tmp_path, monkeypatch):
    _patch_project_root(monkeypatch, tmp_path)
    _write_sample_tree(tmp_path)
    mhl = importlib.import_module("mhl")
    importlib.reload(mhl)

    fixed_now = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)
    mhl.create_mhl(source=tmp_path, op="ingest", author="hevin", now=fixed_now)
    mhl.create_mhl(source=tmp_path, op="offload", author="hevin", now=fixed_now)
    mhl.create_mhl(source=tmp_path, op="verify", author="hevin", now=fixed_now)

    code, count, message = mhl.verify_mhl(chain=True)
    assert code == 0
    assert count == 15
    assert message == "OK: 15 files verified"


def test_verify_missing_file_returns_exit_2(tmp_path, monkeypatch):
    _patch_project_root(monkeypatch, tmp_path)
    _write_sample_tree(tmp_path)
    mhl = importlib.import_module("mhl")
    importlib.reload(mhl)

    fixed_now = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)
    out, _ = mhl.create_mhl(source=tmp_path, op="ingest", author="hevin", now=fixed_now)
    (tmp_path / "A001" / "clip_001.mp4").unlink()

    code, count, message = mhl.verify_mhl(out)
    assert code == 2
    assert count < 5
    assert "missing file" in message


def test_cli_create_and_verify_round_trip(tmp_path, monkeypatch):
    _patch_project_root(monkeypatch, tmp_path)
    _write_sample_tree(tmp_path)
    mhl = importlib.import_module("mhl")
    importlib.reload(mhl)

    fixed_now = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)
    output, _ = mhl.create_mhl(source=tmp_path, op="ingest", author="hevin", now=fixed_now)
    code, count, message = mhl.verify_mhl(output)
    assert code == 0
    assert count == 5
    assert message == "OK: 5 files verified"
