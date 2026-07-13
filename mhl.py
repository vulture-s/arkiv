from __future__ import annotations

import argparse
import hashlib
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import xxhash


MHL_NAMESPACE = "urn:ASC:MHL:v2.0"
CHAIN_NAMESPACE = "urn:ASC:MHL:DIRECTORY:v2.0"
TOOL_NAME = "ascmhl"
TOOL_VERSION = "1.2"
DEFAULT_HASH_ALGO = "xxh3"
DEFAULT_IGNORE_PATTERNS = [".DS_Store", "ascmhl", "ascmhl/"]
MHL_FILENAME_RE = re.compile(r"^(\d{4,})_(.+)_(\d{4}-\d{2}-\d{2}_\d{6}Z)\.mhl$")


@dataclass
class HashValue:
    algo: str
    value: str
    action: Optional[str]
    hashdate: Optional[datetime]


@dataclass
class FileRecord:
    rel_path: str
    size: int
    mtime: datetime
    hashes: List[HashValue]


@dataclass
class DirectoryRecord:
    rel_path: str
    mtime: datetime
    content_hashes: List[HashValue]
    structure_hashes: List[HashValue]


@dataclass
class ChainEntry:
    sequence: int
    mhl_name: str
    c4: str


def _local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _local_from_timestamp(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()


def _format_iso(dt: datetime, microseconds: bool = False) -> str:
    dt = dt.astimezone()
    if microseconds:
        return dt.isoformat(timespec="microseconds")
    return dt.replace(microsecond=0).isoformat(timespec="seconds")


def _xml_text(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})


def _posix_path(path: str) -> str:
    return Path(path).as_posix()


def _supported_hash_algorithms(primary: str, secondary: Optional[str]) -> List[str]:
    algos = [primary]
    if secondary:
        algos.append(secondary)
    deduped: List[str] = []
    for algo in algos:
        if algo not in deduped:
            deduped.append(algo)
    return deduped


def _hasher_for(algo: str):
    if algo == "md5":
        return hashlib.md5()
    if algo == "sha1":
        return hashlib.sha1()
    if algo == "sha256":
        return hashlib.sha256()
    if algo == "xxh64":
        return xxhash.xxh64()
    if algo == "xxh3":
        return xxhash.xxh3_64()
    if algo == "c4":
        return hashlib.sha512()
    raise ValueError(f"Unsupported hash algorithm: {algo}")


def _digest_bytes(algo: str, data: bytes) -> bytes:
    if algo == "c4":
        hasher = _hasher_for(algo)
        hasher.update(data)
        return hasher.digest()
    hasher = _hasher_for(algo)
    hasher.update(data)
    return bytes.fromhex(hasher.hexdigest())


def hash_bytes(data: bytes, algo: str) -> str:
    if algo == "c4":
        hasher = hashlib.sha512()
        hasher.update(data)
        return _sha512_to_c4(hasher.hexdigest())
    hasher = _hasher_for(algo)
    hasher.update(data)
    return hasher.hexdigest()


def hash_file(path: Path, algo: str) -> str:
    if algo == "c4":
        hasher = hashlib.sha512()
    else:
        hasher = _hasher_for(algo)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    if algo == "c4":
        return _sha512_to_c4(hasher.hexdigest())
    return hasher.hexdigest()


def hash_of_hash_list(hash_list: Sequence[str], algo: str) -> str:
    if algo == "c4":
        hasher = hashlib.sha512()
        for value in sorted(hash_list):
            hasher.update(_hash_from_hash_string("c4", value))
        return _sha512_to_c4(hasher.hexdigest())
    hasher = _hasher_for(algo)
    for value in sorted(hash_list):
        hasher.update(bytes.fromhex(value))
    return hasher.hexdigest()


def _sha512_to_c4(sha512_hex: str) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    base = 58
    total = int(sha512_hex, 16)
    encoded = ""
    while total:
        total, remainder = divmod(total, base)
        encoded = alphabet[remainder] + encoded
    return "c4" + encoded.rjust(88, "1")


def _c4_from_file(path: Path) -> str:
    hasher = hashlib.sha512()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return _sha512_to_c4(hasher.hexdigest())


def _hash_from_hash_string(algo: str, hash_string: str) -> bytes:
    if algo == "c4":
        alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        value = 0
        for char in hash_string[2:]:
            value = value * 58 + alphabet.index(char)
        return value.to_bytes(64, byteorder="big")
    return bytes.fromhex(hash_string)


def _compute_directory_hashes(
    directory: Path,
    rel_dir: str,
    algos: Sequence[str],
    file_records: List[FileRecord],
    directory_records: List[DirectoryRecord],
    manifest_entries: List[Tuple[str, object]],
) -> Dict[str, Tuple[str, str]]:
    content_hashes: Dict[str, List[str]] = {algo: [] for algo in algos}
    structure_hashes: Dict[str, List[str]] = {algo: [] for algo in algos}
    entries = sorted(list(directory.iterdir()), key=lambda path: path.name.lower())

    for child in entries:
        if _should_ignore(child, rel_dir):
            continue

        child_rel = child.name if not rel_dir else rel_dir + "/" + child.name
        if child.is_dir():
            child_hashes = _compute_directory_hashes(child, child_rel, algos, file_records, directory_records, manifest_entries)
            for algo in algos:
                content_hashes[algo].append(child_hashes[algo][0])
                child_structure_bytes = _hash_from_hash_string(algo, child_hashes[algo][1])
                structure_hashes[algo].append(hash_bytes(child.name.encode("utf-8") + child_structure_bytes, algo))
            record_time = _local_now()
            directory_records.append(
                DirectoryRecord(
                    rel_path=child_rel,
                    mtime=_local_from_timestamp(child.stat().st_mtime),
                    content_hashes=[
                        HashValue(
                            algo=algo,
                            value=child_hashes[algo][0],
                            action=None,
                            hashdate=record_time,
                        )
                        for algo in algos
                    ],
                    structure_hashes=[
                        HashValue(
                            algo=algo,
                            value=child_hashes[algo][1],
                            action=None,
                            hashdate=record_time,
                        )
                        for algo in algos
                    ],
                )
            )
            manifest_entries.append(("directoryhash", directory_records[-1]))
        else:
            file_hashes = {algo: hash_file(child, algo) for algo in algos}
            size = child.stat().st_size
            mtime = _local_from_timestamp(child.stat().st_mtime)
            record_time = _local_now()
            file_records.append(
                FileRecord(
                    rel_path=child_rel,
                    size=size,
                    mtime=mtime,
                    hashes=[
                        HashValue(algo=algo, value=file_hashes[algo], action="original", hashdate=record_time)
                        for algo in algos
                    ],
                )
            )
            manifest_entries.append(("hash", file_records[-1]))
            for algo in algos:
                content_hashes[algo].append(file_hashes[algo])
                structure_hashes[algo].append(
                    hash_bytes(child.name.encode("utf-8") + _hash_from_hash_string(algo, file_hashes[algo]), algo)
                )

    return {
        algo: (
            hash_of_hash_list(content_hashes[algo], algo),
            hash_of_hash_list(structure_hashes[algo], algo),
        )
        for algo in algos
    }


def _should_ignore(path: Path, rel_dir: str) -> bool:
    if path.name == ".DS_Store":
        return True
    # fable-audit round-5 #18: a killed offload leaves a truncated "<name>.partial"
    # (offload writes bytes there, then os.replace's onto the final name on success).
    # It must NEVER be hashed into the manifest — otherwise ascMHL records the
    # truncated bytes as verified original content and every future verify passes on
    # a broken clip. Excluding it here means a leftover .partial is simply ignored.
    if path.name.endswith(".partial"):
        return True
    parts = path.parts
    if "ascmhl" in parts:
        return True
    if rel_dir and rel_dir.startswith("ascmhl"):
        return True
    return False


def _render_hash_values(values: Sequence[HashValue], indent: str) -> List[str]:
    lines: List[str] = []
    for value in sorted(values, key=lambda item: item.algo):
        attrs = []
        if value.action:
            attrs.append(f'action="{_xml_text(value.action)}"')
        if value.hashdate:
            attrs.append(f'hashdate="{_xml_text(_format_iso(value.hashdate, microseconds=True))}"')
        attr_text = ""
        if attrs:
            attr_text = " " + " ".join(attrs)
        lines.append(f"{indent}<{value.algo}{attr_text}>{_xml_text(value.value)}</{value.algo}>")
    return lines


def _render_file_record(record: FileRecord, indent_level: int) -> List[str]:
    indent = "  " * indent_level
    lines = [f"{indent}<hash>"]
    path_attrs = [f'size="{record.size}"', f'lastmodificationdate="{_xml_text(_format_iso(record.mtime))}"']
    lines.append(
        f"{indent}  <path {' '.join(path_attrs)}>{_xml_text(_posix_path(record.rel_path))}</path>"
    )
    lines.extend(_render_hash_values(record.hashes, indent + "  "))
    lines.append(f"{indent}</hash>")
    return lines


def _render_directory_record(record: DirectoryRecord, indent_level: int) -> List[str]:
    indent = "  " * indent_level
    lines = [f"{indent}<directoryhash>"]
    lines.append(
        f"{indent}  <path lastmodificationdate=\"{_xml_text(_format_iso(record.mtime))}\">{_xml_text(_posix_path(record.rel_path))}</path>"
    )
    lines.append(f"{indent}  <content>")
    lines.extend(_render_hash_values(record.content_hashes, indent + "    "))
    lines.append(f"{indent}  </content>")
    lines.append(f"{indent}  <structure>")
    lines.extend(_render_hash_values(record.structure_hashes, indent + "    "))
    lines.append(f"{indent}  </structure>")
    lines.append(f"{indent}</directoryhash>")
    return lines


def _render_manifest(
    creator_time: datetime,
    root_hashes: Dict[str, Tuple[str, str]],
    manifest_entries: Sequence[Tuple[str, object]],
    op: str,
) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<hashlist version="2.0" xmlns="urn:ASC:MHL:v2.0">']
    lines.append("  <creatorinfo>")
    lines.append(f"    <creationdate>{_xml_text(_format_iso(creator_time))}</creationdate>")
    lines.append(f"    <hostname>{_xml_text(socket.gethostname())}</hostname>")
    lines.append(f'    <tool version="{_xml_text(TOOL_VERSION)}">{_xml_text(TOOL_NAME)}</tool>')
    lines.append("  </creatorinfo>")
    lines.append(f"  <processinfo>")
    lines.append(f"    <process>{_xml_text(op)}</process>")
    lines.append("    <roothash>")
    lines.append("      <content>")
    root_hash_time = _local_now()
    for algo in sorted(root_hashes):
        content, _ = root_hashes[algo]
        lines.append(
            f'        <{algo} hashdate="{_xml_text(_format_iso(root_hash_time, microseconds=True))}">{_xml_text(content)}</{algo}>'
        )
    lines.append("      </content>")
    lines.append("      <structure>")
    for algo in sorted(root_hashes):
        _, structure = root_hashes[algo]
        lines.append(
            f'        <{algo} hashdate="{_xml_text(_format_iso(root_hash_time, microseconds=True))}">{_xml_text(structure)}</{algo}>'
        )
    lines.append("      </structure>")
    lines.append("    </roothash>")
    lines.append("    <ignore>")
    for pattern in DEFAULT_IGNORE_PATTERNS:
        lines.append(f"      <pattern>{_xml_text(pattern)}</pattern>")
    lines.append("    </ignore>")
    lines.append("  </processinfo>")
    lines.append("  <hashes>")

    for kind, record in manifest_entries:
        if kind == "hash":
            lines.extend(_render_file_record(record, 2))
        else:
            lines.extend(_render_directory_record(record, 2))

    lines.append("  </hashes>")
    lines.append("</hashlist>")
    return "\n".join(lines) + "\n"


def _render_chain(entries: Sequence[ChainEntry]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<ascmhldirectory xmlns="urn:ASC:MHL:DIRECTORY:v2.0">']
    for entry in entries:
        lines.append(f'  <hashlist sequencenr="{entry.sequence}">')
        lines.append(f"    <path>{_xml_text(entry.mhl_name)}</path>")
        lines.append(f"    <c4>{_xml_text(entry.c4)}</c4>")
        lines.append("  </hashlist>")
    lines.append("</ascmhldirectory>")
    return "\n".join(lines) + "\n"


def _read_chain(chain_path: Path) -> List[ChainEntry]:
    if not chain_path.exists():
        return []
    tree = ET.parse(chain_path)
    root = tree.getroot()
    entries: List[ChainEntry] = []
    for hashlist_node in root.findall("{urn:ASC:MHL:DIRECTORY:v2.0}hashlist"):
        seq = int(hashlist_node.attrib.get("sequencenr", "0"))
        path_node = hashlist_node.find("{urn:ASC:MHL:DIRECTORY:v2.0}path")
        c4_node = hashlist_node.find("{urn:ASC:MHL:DIRECTORY:v2.0}c4")
        if path_node is None or c4_node is None:
            continue
        entries.append(ChainEntry(seq, path_node.text or "", c4_node.text or ""))
    entries.sort(key=lambda item: item.sequence)
    return entries


def _latest_generation_number(chain_path: Path, output_dir: Path) -> int:
    chain_entries = _read_chain(chain_path)
    if chain_entries:
        return max(entry.sequence for entry in chain_entries)
    latest = 0
    for child in output_dir.glob("*.mhl"):
        match = MHL_FILENAME_RE.match(child.name)
        if match:
            latest = max(latest, int(match.group(1)))
    return latest


def _output_paths(source: Path, output: Optional[Path]) -> Tuple[Path, Path, str, int]:
    if output is None:
        output_dir = source / "ascmhl"
        output_file = None
    else:
        if output.suffix.lower() == ".mhl":
            output_dir = output.parent
            output_file = output
        elif output.name.lower() == "ascmhl":
            output_dir = output
            output_file = None
        else:
            output_dir = output / "ascmhl"
            output_file = None
    output_dir.mkdir(parents=True, exist_ok=True)
    chain_path = output_dir / "ascmhl_chain.xml"
    if output_file is not None:
        match = MHL_FILENAME_RE.match(output_file.name)
        sequence = int(match.group(1)) if match else _latest_generation_number(chain_path, output_dir) + 1
        return output_dir, chain_path, output_file.name, sequence
    sequence = _latest_generation_number(chain_path, output_dir) + 1
    filename = f"{sequence:04d}_{source.name}_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%SZ')}.mhl"
    return output_dir, chain_path, filename, sequence


def create_manifest(
    source: Path,
    output: Optional[Path] = None,
    primary_hash: str = DEFAULT_HASH_ALGO,
    secondary_hash: Optional[str] = None,
    op: str = "ingest",
) -> Tuple[Path, Path]:
    source = source.resolve()
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"source directory not found: {source}")

    algos = _supported_hash_algorithms(primary_hash, secondary_hash)
    output_dir, chain_path, filename, sequence = _output_paths(source, output)
    mhl_path = output_dir / filename

    file_records: List[FileRecord] = []
    directory_records: List[DirectoryRecord] = []
    manifest_entries: List[Tuple[str, object]] = []
    root_hashes = _compute_directory_hashes(source, "", algos, file_records, directory_records, manifest_entries)

    creator_time = _local_now().replace(microsecond=0)
    manifest_xml = _render_manifest(creator_time, root_hashes, manifest_entries, op)
    mhl_path.write_text(manifest_xml, encoding="utf-8")

    chain_entries = _read_chain(chain_path)
    chain_entries.append(ChainEntry(sequence=sequence, mhl_name=mhl_path.name, c4=_c4_from_file(mhl_path)))
    chain_xml = _render_chain(chain_entries)
    chain_path.write_text(chain_xml, encoding="utf-8")
    return mhl_path, chain_path


def _parse_manifest(mhl_path: Path):
    tree = ET.parse(mhl_path)
    root = tree.getroot()
    if not root.tag.endswith("hashlist"):
        raise ValueError("not an MHL v2 hashlist")
    return root


def _manifest_root(root) -> Dict[str, Tuple[str, str]]:
    ns = "{urn:ASC:MHL:v2.0}"
    processinfo = root.find(ns + "processinfo")
    if processinfo is None:
        raise ValueError("missing processinfo")
    roothash = processinfo.find(ns + "roothash")
    if roothash is None:
        return {}
    result: Dict[str, Tuple[str, str]] = {}
    content = roothash.find(ns + "content")
    structure = roothash.find(ns + "structure")
    if content is not None:
        for child in list(content):
            algo = child.tag.split("}", 1)[-1]
            result.setdefault(algo, ["", ""])  # type: ignore[list-item]
            result[algo][0] = child.text or ""  # type: ignore[index]
    if structure is not None:
        for child in list(structure):
            algo = child.tag.split("}", 1)[-1]
            result.setdefault(algo, ["", ""])  # type: ignore[list-item]
            result[algo][1] = child.text or ""  # type: ignore[index]
    return {algo: (value[0], value[1]) for algo, value in result.items()}


def _manifest_entries(root) -> Tuple[List[FileRecord], List[DirectoryRecord]]:
    ns = "{urn:ASC:MHL:v2.0}"
    hashes = root.find(ns + "hashes")
    if hashes is None:
        return [], []
    file_records: List[FileRecord] = []
    directory_records: List[DirectoryRecord] = []
    for node in list(hashes):
        tag = node.tag.split("}", 1)[-1]
        path_node = node.find(ns + "path")
        if path_node is None:
            continue
        rel_path = path_node.text or ""
        mtime_text = path_node.attrib.get("lastmodificationdate", "")
        mtime = datetime.fromisoformat(mtime_text) if mtime_text else _local_now()
        if tag == "hash":
            size = int(path_node.attrib.get("size", "0"))
            hashes_values: List[HashValue] = []
            for child in list(node):
                if child.tag.split("}", 1)[-1] == "path":
                    continue
                hashes_values.append(
                    HashValue(
                        algo=child.tag.split("}", 1)[-1],
                        value=child.text or "",
                        action=child.attrib.get("action"),
                        hashdate=datetime.fromisoformat(child.attrib["hashdate"]) if "hashdate" in child.attrib else None,
                    )
                )
            file_records.append(FileRecord(rel_path=rel_path, size=size, mtime=mtime, hashes=hashes_values))
        elif tag == "directoryhash":
            content_hashes: List[HashValue] = []
            structure_hashes: List[HashValue] = []
            section_node = node.find(ns + "content")
            if section_node is not None:
                for child in list(section_node):
                    content_hashes.append(
                        HashValue(
                            algo=child.tag.split("}", 1)[-1],
                            value=child.text or "",
                            action=child.attrib.get("action"),
                            hashdate=datetime.fromisoformat(child.attrib["hashdate"]) if "hashdate" in child.attrib else None,
                        )
                    )
            section_node = node.find(ns + "structure")
            if section_node is not None:
                for child in list(section_node):
                    structure_hashes.append(
                        HashValue(
                            algo=child.tag.split("}", 1)[-1],
                            value=child.text or "",
                            action=child.attrib.get("action"),
                            hashdate=datetime.fromisoformat(child.attrib["hashdate"]) if "hashdate" in child.attrib else None,
                        )
                    )
            directory_records.append(
                DirectoryRecord(rel_path=rel_path, mtime=mtime, content_hashes=content_hashes, structure_hashes=structure_hashes)
            )
    return file_records, directory_records


def _recompute_from_tree(source_root: Path, manifest_algos: Sequence[str]) -> Dict[str, Tuple[str, str]]:
    file_records: List[FileRecord] = []
    directory_records: List[DirectoryRecord] = []
    manifest_entries: List[Tuple[str, object]] = []
    return _compute_directory_hashes(source_root, "", manifest_algos, file_records, directory_records, manifest_entries)


def verify_manifest(mhl_path: Path, chain: bool = False, strict: bool = False) -> int:
    try:
        root = _parse_manifest(mhl_path)
    except FileNotFoundError:
        return 3
    except Exception:
        return 1

    source_root = mhl_path.parent.parent if mhl_path.parent.name == "ascmhl" else mhl_path.parent
    if not source_root.exists():
        return 3

    try:
        manifest_root_hashes = _manifest_root(root)
        manifest_files, manifest_dirs = _manifest_entries(root)
        algos = sorted(manifest_root_hashes) if manifest_root_hashes else [DEFAULT_HASH_ALGO]
        recomputed_root = _recompute_from_tree(source_root, algos)
    except FileNotFoundError:
        return 3
    except Exception:
        return 1

    for algo, values in manifest_root_hashes.items():
        if algo not in recomputed_root:
            return 1
        if recomputed_root[algo] != values:
            return 2

    actual_file_map: Dict[str, FileRecord] = {}
    actual_dir_map: Dict[str, DirectoryRecord] = {}
    file_records: List[FileRecord] = []
    directory_records: List[DirectoryRecord] = []
    _compute_directory_hashes(source_root, "", algos, file_records, directory_records, [])
    for record in file_records:
        actual_file_map[record.rel_path] = record
    for record in directory_records:
        actual_dir_map[record.rel_path] = record

    for record in manifest_files:
        actual = actual_file_map.get(record.rel_path)
        if actual is None:
            return 3
        actual_values = {item.algo: item.value for item in actual.hashes}
        for item in record.hashes:
            if actual_values.get(item.algo) != item.value:
                return 2

    for record in manifest_dirs:
        actual = actual_dir_map.get(record.rel_path)
        if actual is None:
            return 3
        actual_content = {item.algo: item.value for item in actual.content_hashes}
        actual_structure = {item.algo: item.value for item in actual.structure_hashes}
        for item in record.content_hashes:
            if actual_content.get(item.algo) != item.value:
                return 2
        for item in record.structure_hashes:
            if actual_structure.get(item.algo) != item.value:
                return 2

    if chain:
        chain_path = mhl_path.parent / "ascmhl_chain.xml"
        if not chain_path.exists():
            return 4
        try:
            chain_entries = _read_chain(chain_path)
        except Exception:
            return 4
        match = next((entry for entry in chain_entries if entry.mhl_name == mhl_path.name), None)
        if match is None:
            return 4
        if _c4_from_file(mhl_path) != match.c4:
            return 4

    if strict:
        allowed = {
            "creatorinfo",
            "creationdate",
            "hostname",
            "tool",
            "author",
            "location",
            "comment",
            "processinfo",
            "hashes",
            "hash",
            "directoryhash",
            "path",
            "content",
            "structure",
            "process",
            "roothash",
            "ignore",
            "pattern",
        }
        for node in root.iter():
            tag = node.tag.split("}", 1)[-1]
            if tag not in allowed and tag not in _supported_hash_algorithms("xxh3", "md5") + ["sha1", "sha256", "c4"]:
                return 1

    return 0


def _find_default_mhl() -> Optional[Path]:
    cwd = Path.cwd()
    candidates = sorted(cwd.glob("ascmhl/*.mhl"))
    if not candidates:
        return None
    return candidates[-1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mhl.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--source", required=True)
    create_parser.add_argument("--hash", dest="hash_algo", default=DEFAULT_HASH_ALGO)
    create_parser.add_argument("--secondary", dest="secondary_hash")
    create_parser.add_argument("--output")
    create_parser.add_argument("--op", default="ingest")
    create_parser.add_argument("--author")

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--chain", action="store_true")
    verify_parser.add_argument("--mhl")
    verify_parser.add_argument("--strict", action="store_true")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            source = Path(args.source)
            output = Path(args.output) if args.output else None
            create_manifest(source, output=output, primary_hash=args.hash_algo, secondary_hash=args.secondary_hash, op=args.op)
            return 0
        if args.command == "verify":
            mhl_path = Path(args.mhl) if args.mhl else _find_default_mhl()
            if mhl_path is None:
                return 3
            return verify_manifest(mhl_path, chain=args.chain, strict=args.strict)
    except FileNotFoundError:
        return 3
    except ValueError:
        return 1
    except Exception:
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
