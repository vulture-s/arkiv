from __future__ import annotations

import re
import subprocess
import sys
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

import mhl


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "mhl.py"
MHL_NS = {"m": "urn:ASC:MHL:v2.0", "d": "urn:ASC:MHL:DIRECTORY:v2.0"}
_NATIVE_DIR = REPO_ROOT / "tests" / "fixtures" / "native-mhl"
NATIVE_MHL = _NATIVE_DIR / "0001_ascmhl-native_2026-05-26_083540Z.mhl"
NATIVE_CHAIN = _NATIVE_DIR / "ascmhl_chain.xml"


def run_cli(args, cwd):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result


def make_fixture(root: Path) -> Path:
    (root / "A001").mkdir(parents=True)
    (root / "B002").mkdir()
    (root / "A001" / "clip_001.mp4").write_bytes(b"clip-one-0014")
    (root / "A001" / "clip_002.mp4").write_bytes(b"clip-two-0024")
    return root


def ensure_clean(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def parse_xml(path: Path):
    return ET.parse(path).getroot()


def test_create_writes_v2_hashlist_and_chain():
    fixture = REPO_ROOT / "temp" / "mhl-create-evidence"
    ensure_clean(fixture)
    fixture = make_fixture(fixture)
    result = run_cli(["create", "--source", str(fixture)], cwd=fixture)
    assert result.returncode == 0, result.stdout + result.stderr

    mhl_files = sorted((fixture / "ascmhl").glob("0001_mhl-create-evidence_*.mhl"))
    assert len(mhl_files) == 1
    mhl_path = mhl_files[0]
    chain_path = fixture / "ascmhl" / "ascmhl_chain.xml"
    assert chain_path.exists()
    assert re.match(r"^0001_mhl-create-evidence_\d{4}-\d{2}-\d{2}_\d{6}Z\.mhl$", mhl_path.name)

    root = parse_xml(mhl_path)
    assert root.tag == "{urn:ASC:MHL:v2.0}hashlist"
    assert root.attrib["version"] == "2.0"

    creator = root.find("m:creatorinfo", MHL_NS)
    assert creator is not None
    assert creator.findtext("m:creationdate", namespaces=MHL_NS)
    assert creator.findtext("m:hostname", namespaces=MHL_NS)
    tool = creator.find("m:tool", MHL_NS)
    assert tool is not None
    assert tool.text == "ascmhl"
    assert tool.attrib["version"] == "1.2"

    process = root.find("m:processinfo/m:process", MHL_NS)
    assert process is not None
    assert process.text == "ingest"
    roothash = root.find("m:processinfo/m:roothash", MHL_NS)
    assert roothash is not None
    root_content = roothash.find("m:content", MHL_NS)
    root_structure = roothash.find("m:structure", MHL_NS)
    assert root_content is not None
    assert root_structure is not None
    assert root_content.find("m:xxh3", MHL_NS) is not None
    assert root_structure.find("m:xxh3", MHL_NS) is not None

    hashes = root.find("m:hashes", MHL_NS)
    assert hashes is not None
    children = list(hashes)
    assert [child.tag.split("}", 1)[-1] for child in children] == [
        "hash",
        "hash",
        "directoryhash",
        "directoryhash",
    ]

    first_hash = children[0]
    first_path = first_hash.find("m:path", MHL_NS)
    assert first_path is not None
    assert first_path.text == "A001/clip_001.mp4"
    assert first_path.attrib["size"] == "13"
    assert "lastmodificationdate" in first_path.attrib
    first_algo = first_hash.find("m:xxh3", MHL_NS)
    assert first_algo is not None
    assert first_algo.attrib["action"] == "original"
    assert re.fullmatch(r"[0-9a-f]{16}", first_algo.text or "")
    assert "hashdate" in first_algo.attrib

    dir_hash = children[2]
    dir_path = dir_hash.find("m:path", MHL_NS)
    assert dir_path is not None
    assert dir_path.text == "A001"
    assert "size" not in dir_path.attrib
    assert dir_hash.find("m:content/m:xxh3", MHL_NS) is not None
    assert dir_hash.find("m:structure/m:xxh3", MHL_NS) is not None

    chain_root = parse_xml(chain_path)
    assert chain_root.tag == "{urn:ASC:MHL:DIRECTORY:v2.0}ascmhldirectory"
    chain_hashlist = chain_root.find("d:hashlist", MHL_NS)
    assert chain_hashlist is not None
    assert chain_hashlist.attrib["sequencenr"] == "1"
    assert chain_hashlist.findtext("d:path", namespaces=MHL_NS) == mhl_path.name
    c4_value = chain_hashlist.findtext("d:c4", namespaces=MHL_NS)
    assert c4_value is not None
    assert c4_value.startswith("c4")
    assert len(c4_value) == 90


def test_verify_detects_tamper():
    fixture = REPO_ROOT / "temp" / "mhl-verify-evidence"
    ensure_clean(fixture)
    fixture = make_fixture(fixture)
    create_result = run_cli(["create", "--source", str(fixture)], cwd=fixture)
    assert create_result.returncode == 0, create_result.stdout + create_result.stderr

    mhl_path = next((fixture / "ascmhl").glob("0001_mhl-verify-evidence_*.mhl"))
    verify_ok = run_cli(["verify", "--chain", "--mhl", str(mhl_path)], cwd=fixture)
    assert verify_ok.returncode == 0, verify_ok.stdout + verify_ok.stderr

    (fixture / "A001" / "clip_001.mp4").write_bytes(b"tampered-data!!")
    verify_bad = run_cli(["verify", "--chain", "--mhl", str(mhl_path)], cwd=fixture)
    assert verify_bad.returncode == 2, verify_bad.stdout + verify_bad.stderr


@pytest.mark.skipif(
    not NATIVE_CHAIN.exists(),
    reason="native ASC MHL reference fixture absent (tests/fixtures is gitignored — generate locally to run)",
)
def test_native_c4_reference_matches_chain():
    native_chain = parse_xml(NATIVE_CHAIN)
    native_hashlist = native_chain.find("d:hashlist", MHL_NS)
    assert native_hashlist is not None
    expected_c4 = native_hashlist.findtext("d:c4", namespaces=MHL_NS)
    assert expected_c4 is not None
    assert mhl._c4_from_file(NATIVE_MHL) == expected_c4
