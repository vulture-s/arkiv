#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import config

try:
    import xxhash
except Exception:  # pragma: no cover - optional dependency in some envs
    xxhash = None


MHL_NS = "http://www.mhl.media"
ET.register_namespace("", MHL_NS)

PRIMARY_ALGOS = {
    "xxh3": "xxh3-128",
    "md5": "md5",
    "sha1": "sha1",
    "sha256": "sha256",
}
CHECKSUM_TO_CLI = {value: key for key, value in PRIMARY_ALGOS.items()}
DEFAULT_SECONDARY = "md5"
DATE_FMT = "%Y-%m-%d"
FILENAME_RE = re.compile(r"^(?P<num>\d{3})_(?P<op>[A-Za-z0-9-]+)_(?P<date>\d{4}-\d{2}-\d{2})\.mhl$")


def _tag(name):
    return "{%s}%s" % (MHL_NS, name)


def _now_utc():
    return datetime.now(timezone.utc)


def _utc_iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_root():
    return Path(config.PROJECT_ROOT).expanduser().resolve()


def _ascmhl_dir():
    return Path(getattr(config, "_ASCMHL_DIR", _project_root() / "ascmhl"))


def _is_under(path, root):
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _is_excluded_from_source(path, ascmhl_dir):
    if path.suffix.lower() == ".mhl":
        return True
    if _is_under(path, ascmhl_dir):
        return True
    if path.name == "chain.xml":
        return True
    return False


def normalize_mhl_path(abs_path, project_root):
    rel = abs_path.resolve(strict=False).relative_to(project_root.resolve(strict=False))
    return unicodedata.normalize("NFC", rel.as_posix())


def hash_file(path, algo="xxh3-128", chunk_size=1 << 20):
    if algo == "xxh3-128":
        if xxhash is not None:
            h = xxhash.xxh3_128()
        else:
            h = hashlib.blake2b(digest_size=16)
    elif algo == "md5":
        h = hashlib.md5()
    elif algo == "sha1":
        h = hashlib.sha1()
    elif algo == "sha256":
        h = hashlib.sha256()
    else:
        raise ValueError("unsupported algo: {0}".format(algo))

    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _collect_files(source, project_root, ascmhl_dir):
    source = source.expanduser().resolve(strict=False)
    if source.is_file():
        return [source]
    files = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded_from_source(path, ascmhl_dir):
            continue
        files.append(path)
    files.sort(key=lambda p: normalize_mhl_path(p, project_root))
    return files


def _next_sequence_number(output_dir):
    max_num = 0
    if output_dir.exists():
        for path in output_dir.glob("*.mhl"):
            match = FILENAME_RE.match(path.name)
            if match:
                num = int(match.group("num"))
                if num > max_num:
                    max_num = num
    return max_num + 1


def _ensure_parent(path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _build_xml(source_files, project_root, op, author, device, primary_algo, secondary_algo, timestamp):
    action = "verified" if op in ("offload", "verify") else "original"
    root = ET.Element(_tag("mhl"), {"version": "2.0"})
    creator = ET.SubElement(root, _tag("creatorinfo"))
    ET.SubElement(creator, _tag("tool"), {"name": "arkiv", "version": "0.3.0"})
    ET.SubElement(creator, _tag("author")).text = author
    ET.SubElement(creator, _tag("location")).text = "./ascmhl/"
    ET.SubElement(creator, _tag("creationdate")).text = _utc_iso(timestamp)

    process = ET.SubElement(root, _tag("processinfo"))
    ET.SubElement(process, _tag("operation")).text = op
    ET.SubElement(process, _tag("timestamp")).text = _utc_iso(timestamp)
    ET.SubElement(process, _tag("device")).text = device

    hashlist = ET.SubElement(root, _tag("hashlist"))
    for path in source_files:
        rel = normalize_mhl_path(path, project_root)
        entry = ET.SubElement(hashlist, _tag("hash"), {"file": rel, "action": action})
        ET.SubElement(entry, _tag("checksum"), {"type": primary_algo}).text = hash_file(path, primary_algo)
        if secondary_algo and secondary_algo != primary_algo:
            ET.SubElement(entry, _tag("checksum"), {"type": secondary_algo}).text = hash_file(path, secondary_algo)

    return ET.ElementTree(root)


def create_mhl(source=None, output=None, op="ingest", primary_hash="xxh3", secondary_hash=DEFAULT_SECONDARY, author=None, now=None):
    project_root = _project_root()
    ascmhl_dir = _ascmhl_dir()
    source_path = Path(source) if source is not None else project_root
    source_path = source_path.expanduser().resolve(strict=False)
    if not source_path.exists():
        raise FileNotFoundError("source missing: {0} (PROJECT_ROOT={1})".format(source_path, project_root))
    if not _is_under(source_path, project_root):
        raise ValueError("source must be inside PROJECT_ROOT: {0} (PROJECT_ROOT={1})".format(source_path, project_root))

    source_files = _collect_files(source_path, project_root, ascmhl_dir)
    if now is None:
        now = _now_utc()
    if author is None:
        author = os.getenv("USER") or os.getenv("USERNAME") or "unknown"
    primary_algo = PRIMARY_ALGOS[primary_hash]
    secondary_algo = PRIMARY_ALGOS.get(secondary_hash) if secondary_hash else None

    if output is None:
        seq = _next_sequence_number(ascmhl_dir)
        output = ascmhl_dir / "{0:03d}_{1}_{2}.mhl".format(seq, op, now.astimezone(timezone.utc).strftime(DATE_FMT))
    else:
        output = Path(output)

    tree = _build_xml(source_files, project_root, op, author, "local", primary_algo, secondary_algo, now)
    _ensure_parent(output)
    tree.write(str(output), encoding="utf-8", xml_declaration=True)
    return output, len(source_files)


def _parse_mhl(mhl_path):
    try:
        tree = ET.parse(str(mhl_path))
    except ET.ParseError as exc:
        line, col = getattr(exc, "position", (None, None))
        raise ValueError("XML parse error in {0} at {1}:{2}".format(mhl_path, line, col))
    root = tree.getroot()
    ns = {"m": MHL_NS}
    hashes = []
    for hash_el in root.findall("m:hashlist/m:hash", ns):
        rel = hash_el.attrib.get("file", "")
        action = hash_el.attrib.get("action", "")
        checksums = []
        for checksum_el in hash_el.findall("m:checksum", ns):
            ctype = checksum_el.attrib.get("type", "").lower()
            cval = (checksum_el.text or "").strip().lower()
            checksums.append((ctype, cval))
        hashes.append({"file": rel, "action": action, "checksums": checksums})
    return hashes


def _resolve_hash_target(file_ref, project_root):
    candidate = Path(file_ref)
    if candidate.is_absolute():
        return candidate
    return project_root / candidate


def _verify_single_mhl(mhl_path, project_root):
    hashes = _parse_mhl(mhl_path)
    verified = 0
    for item in hashes:
        file_ref = item["file"]
        file_path = _resolve_hash_target(file_ref, project_root)
        if not file_path.exists():
            return 2, verified, "missing file: {0}".format(file_ref), {}
        for ctype, stored in item["checksums"]:
            computed = hash_file(file_path, ctype)
            if stored.lower() != computed.lower():
                return 1, verified, "hash mismatch: {0} ({1}) stored={2} computed={3}".format(file_ref, ctype, stored, computed), {}
        verified += 1
    primary_map = {}
    for item in hashes:
        if item["checksums"]:
            primary_map[item["file"]] = item["checksums"][0][1]
    return 0, verified, "OK: {0} files verified".format(verified), primary_map


def _latest_mhl(ascmhl_dir):
    files = sorted(ascmhl_dir.glob("*.mhl"))
    if not files:
        return None
    return files[-1]


def verify_mhl(mhl_path=None, chain=False, strict=False):
    project_root = _project_root()
    ascmhl_dir = _ascmhl_dir()
    if chain:
        mhl_paths = sorted(ascmhl_dir.glob("*.mhl"))
    else:
        if mhl_path is None:
            mhl_path = _latest_mhl(ascmhl_dir)
            if mhl_path is None:
                raise FileNotFoundError("no MHL files found in {0}".format(ascmhl_dir))
        else:
            mhl_path = Path(mhl_path)
            if not mhl_path.exists():
                alt = ascmhl_dir / mhl_path.name
                if alt.exists():
                    mhl_path = alt
                else:
                    candidate = project_root / mhl_path
                    if candidate.exists():
                        mhl_path = candidate
                    else:
                        raise FileNotFoundError("MHL file missing: {0}".format(mhl_path))
        mhl_paths = [mhl_path]

    total_verified = 0
    previous_primary = None
    previous_name = None
    for path in mhl_paths:
        try:
            code, verified, message, primary_map = _verify_single_mhl(path, project_root)
        except ValueError as exc:
            return 3, total_verified, str(exc)
        if code != 0:
            return code, total_verified, message
        total_verified += verified
        if strict and previous_primary is not None:
            for rel_path, prev_hash in previous_primary.items():
                if rel_path in primary_map and primary_map[rel_path] != prev_hash:
                    return 1, total_verified, "chain mismatch: {0} changed between {1} and {2}".format(rel_path, previous_name, path.name)
        previous_primary = primary_map
        previous_name = path.name
    return 0, total_verified, "OK: {0} files verified".format(total_verified)


def _build_parser():
    parser = argparse.ArgumentParser(description="arkiv ASC MHL v2 generation/verify")
    subparsers = parser.add_subparsers(dest="command")

    create = subparsers.add_parser("create", help="Create ASC MHL v2")
    create.add_argument("--source", default=str(config.PROJECT_ROOT), help="Source directory (default: PROJECT_ROOT)")
    create.add_argument("--hash", dest="primary_hash", default="xxh3", choices=list(PRIMARY_ALGOS.keys()))
    create.add_argument("--secondary", default=DEFAULT_SECONDARY, choices=list(PRIMARY_ALGOS.keys()))
    create.add_argument("--output", default=None, help="Output path (default: PROJECT_ROOT/ascmhl/NNN_<op>_<date>.mhl)")
    create.add_argument("--op", default="ingest", choices=["ingest", "offload", "verify"])
    create.add_argument("--author", default=None, help="Author name")

    verify = subparsers.add_parser("verify", help="Verify one MHL or a chain")
    verify.add_argument("--chain", action="store_true", help="Verify all MHL files in chain order")
    verify.add_argument("--mhl", default=None, help="Specific MHL file to verify")
    verify.add_argument("--strict", action="store_true", help="Check chain consistency across generations")

    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "create":
        try:
            output, count = create_mhl(
                source=args.source,
                output=args.output,
                op=args.op,
                primary_hash=args.primary_hash,
                secondary_hash=args.secondary,
                author=args.author,
            )
        except ValueError as exc:
            print(str(exc))
            return 4
        except FileNotFoundError as exc:
            print(str(exc))
            return 4
        except PermissionError as exc:
            print(str(exc))
            return 0
        except OSError as exc:
            print(str(exc))
            return 4
        print("Wrote {0} ({1} files)".format(output, count))
        return 0
    if args.command == "verify":
        try:
            code, count, message = verify_mhl(args.mhl, chain=args.chain, strict=args.strict)
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        print(message)
        return code
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
