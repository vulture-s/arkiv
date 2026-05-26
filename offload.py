#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import mhl
except Exception:  # pragma: no cover - optional in local bootstrap tests
    mhl = None

try:
    import xxhash
except Exception:  # pragma: no cover - optional dependency
    xxhash = None


MHL_NS = "http://www.mhl.media"
ET.register_namespace("", MHL_NS)
DEFAULT_HASH = "xxh3-128"
DEFAULT_RETRY_LIMIT = 3
DEFAULT_CHUNK_SIZE = 1 << 20
STATE_VERSION = 1
TEST_MUTATOR = None


def _tag(name):
    return "{%s}%s" % (MHL_NS, name)


def _now_utc():
    return datetime.now(timezone.utc)


def _utc_iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_parent(path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _normalize_relpath(path, root):
    rel = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    return unicodedata.normalize("NFC", rel.as_posix())


def _hash_file(path, algo=DEFAULT_HASH, chunk_size=DEFAULT_CHUNK_SIZE):
    if mhl is not None and hasattr(mhl, "hash_file"):
        return mhl.hash_file(path, algo=algo, chunk_size=chunk_size)
    if algo == "xxh3-128":
        h = xxhash.xxh3_128() if xxhash is not None else hashlib.blake2b(digest_size=16)
    elif algo == "md5":
        h = hashlib.md5()
    elif algo == "sha1":
        h = hashlib.sha1()
    elif algo == "sha256":
        h = hashlib.sha256()
    else:
        raise ValueError("unsupported algo: {0}".format(algo))
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _collect_sources(src, include_heic=False):
    root = Path(src).expanduser().resolve(strict=False)
    if root.is_file():
        return [root]
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part == "ascmhl" for part in path.parts):
            continue
        if path.name.endswith(".partial"):
            continue
        if path.suffix.lower() == ".mhl":
            continue
        if not include_heic and path.suffix.lower() == ".heic":
            continue
        files.append(path)
    files.sort(key=lambda p: p.as_posix().lower())
    return files


def _state_template(source, dsts, hash_algo, retry_limit, include_heic, chunk_size):
    return {
        "version": STATE_VERSION,
        "source": str(Path(source).expanduser().resolve(strict=False)),
        "hash_algo": hash_algo,
        "retry_limit": retry_limit,
        "include_heic": bool(include_heic),
        "chunk_size": chunk_size,
        "files": [],
        "destinations": {
            str(Path(dst).expanduser().resolve(strict=False)): {
                "status": "pending",
                "verified_files": 0,
                "failed_files": 0,
                "mhl_path": None,
            }
            for dst in dsts
        },
    }


def _load_state(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path, state):
    path = Path(path)
    _ensure_parent(path)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _build_file_records(source_files, dsts):
    files = []
    for src_file in source_files:
        files.append(
            {
                "rel": None,
                "source": str(src_file),
                "size": src_file.stat().st_size,
                "destinations": {
                    str(Path(dst).expanduser().resolve(strict=False)): {
                        "status": "pending",
                        "attempts": 0,
                        "bytes_copied": 0,
                        "src_hash": None,
                        "dst_hash": None,
                        "partial": None,
                        "error": None,
                    }
                    for dst in dsts
                },
            }
        )
    return files


def _ensure_file_records(state, source_files, dsts):
    if state.get("files"):
        return state
    state["files"] = []
    for src_file in source_files:
        rel = _normalize_relpath(src_file, Path(state["source"]))
        state["files"].append(
            {
                "rel": rel,
                "source": str(src_file),
                "size": src_file.stat().st_size,
                "destinations": {
                    str(Path(dst).expanduser().resolve(strict=False)): {
                        "status": "pending",
                        "attempts": 0,
                        "bytes_copied": 0,
                        "src_hash": None,
                        "dst_hash": None,
                        "partial": None,
                        "error": None,
                    }
                    for dst in dsts
                },
            }
        )
    return state


def _next_sequence_number(output_dir):
    max_num = 0
    if output_dir.exists():
        for path in output_dir.glob("*.mhl"):
            parts = path.name.split("_", 1)
            if len(parts) != 2:
                continue
            prefix = parts[0]
            if prefix.isdigit():
                max_num = max(max_num, int(prefix))
    return max_num + 1


def _mhl_output_path(dst_root, op, now):
    ascmhl_dir = Path(dst_root) / "ascmhl"
    seq = _next_sequence_number(ascmhl_dir)
    return ascmhl_dir / "{0:03d}_{1}_{2}.mhl".format(seq, op, now.astimezone(timezone.utc).strftime("%Y-%m-%d"))


def _write_mhl(dst_root, verified_rel_paths, hash_algo, author="arkiv", op="offload", now=None):
    now = now or _now_utc()
    dst_root = Path(dst_root).expanduser().resolve(strict=False)
    output = _mhl_output_path(dst_root, op, now)
    root = ET.Element(_tag("mhl"), {"version": "2.0"})
    creator = ET.SubElement(root, _tag("creatorinfo"))
    ET.SubElement(creator, _tag("tool"), {"name": "arkiv", "version": "0.3.0"})
    ET.SubElement(creator, _tag("author")).text = author
    ET.SubElement(creator, _tag("location")).text = "./ascmhl/"
    ET.SubElement(creator, _tag("creationdate")).text = _utc_iso(now)

    process = ET.SubElement(root, _tag("processinfo"))
    ET.SubElement(process, _tag("operation")).text = op
    ET.SubElement(process, _tag("timestamp")).text = _utc_iso(now)
    ET.SubElement(process, _tag("device")).text = "local"

    hashlist = ET.SubElement(root, _tag("hashlist"))
    for rel in verified_rel_paths:
        abs_path = dst_root / rel
        entry = ET.SubElement(hashlist, _tag("hash"), {"file": rel, "action": "verified"})
        ET.SubElement(entry, _tag("checksum"), {"type": hash_algo}).text = _hash_file(abs_path, hash_algo)

    _ensure_parent(output)
    ET.ElementTree(root).write(str(output), encoding="utf-8", xml_declaration=True)
    return output


def _check_destination_mount(dst_root):
    try:
        import health
    except Exception:
        return True
    return health._check_mount(dst_root)


def _copy_single_file(src_path, dst_root, rel_path, file_state, hash_algo, retry_limit, chunk_size, state_path, state, dst_key):
    dst_root = Path(dst_root).expanduser().resolve(strict=False)
    final_path = dst_root / rel_path
    partial_path = final_path.with_name(final_path.name + ".partial")
    _ensure_parent(final_path)

    for attempt in range(file_state["attempts"] + 1, retry_limit + 1):
        file_state["attempts"] = attempt
        file_state["status"] = "copying"
        file_state["partial"] = str(partial_path)
        file_state["error"] = None
        file_state["bytes_copied"] = 0
        file_state["src_hash"] = None
        file_state["dst_hash"] = None
        _save_state(state_path, state)

        try:
            if partial_path.exists():
                partial_path.unlink()
        except OSError:
            pass

        try:
            src_hash = None
            with src_path.open("rb") as src_handle, partial_path.open("wb") as dst_handle:
                hasher = xxhash.xxh3_128() if hash_algo == "xxh3-128" and xxhash is not None else None
                if hash_algo == "md5":
                    hasher = hashlib.md5()
                elif hash_algo == "sha1":
                    hasher = hashlib.sha1()
                elif hash_algo == "sha256":
                    hasher = hashlib.sha256()
                elif hasher is None:
                    if hash_algo == "xxh3-128":
                        hasher = hashlib.blake2b(digest_size=16)
                    else:
                        raise ValueError("unsupported algo: {0}".format(hash_algo))
                while True:
                    chunk = src_handle.read(chunk_size)
                    if not chunk:
                        break
                    dst_handle.write(chunk)
                    hasher.update(chunk)
                    file_state["bytes_copied"] += len(chunk)
                    _save_state(state_path, state)
                    if callable(TEST_MUTATOR):
                        TEST_MUTATOR(src_path, partial_path, attempt, file_state["bytes_copied"])
                dst_handle.flush()
                os.fsync(dst_handle.fileno())
                src_hash = hasher.hexdigest().lower()
            if callable(TEST_MUTATOR):
                TEST_MUTATOR(src_path, partial_path, attempt, file_state["bytes_copied"])
            dst_hash = _hash_file(partial_path, hash_algo, chunk_size=chunk_size)
            file_state["src_hash"] = src_hash
            file_state["dst_hash"] = dst_hash
            if src_hash != dst_hash:
                raise ValueError(
                    "hash mismatch: {0} ({1}) stored={2} computed={3}".format(
                        rel_path, hash_algo, src_hash, dst_hash
                    )
                )
            os.replace(str(partial_path), str(final_path))
            file_state["status"] = "verified"
            file_state["partial"] = None
            file_state["error"] = None
            _save_state(state_path, state)
            return True
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            try:
                if partial_path.exists():
                    partial_path.unlink()
            except OSError:
                pass
            file_state["error"] = str(exc)
            file_state["status"] = "unverified" if attempt >= retry_limit else "retrying"
            _save_state(state_path, state)
            if attempt >= retry_limit:
                return False
    return False


@contextmanager
def _patched_config_project_root(root):
    try:
        import config
    except Exception:
        yield None
        return
    old_project_root = getattr(config, "PROJECT_ROOT", None)
    old_ascmhl = getattr(config, "_ASCMHL_DIR", None)
    config.PROJECT_ROOT = Path(root)
    config._ASCMHL_DIR = Path(root) / "ascmhl"
    try:
        yield config
    finally:
        if old_project_root is not None:
            config.PROJECT_ROOT = old_project_root
        if old_ascmhl is not None:
            config._ASCMHL_DIR = old_ascmhl


def _verify_emitted_mhl(dst_root, mhl_path):
    if mhl is None or not hasattr(mhl, "verify_mhl"):
        return None
    with _patched_config_project_root(dst_root):
        return mhl.verify_mhl(mhl_path)


def run_offload(src, dsts, hash_algo=DEFAULT_HASH, include_heic=False, resume=None, retry_limit=DEFAULT_RETRY_LIMIT, chunk_size=DEFAULT_CHUNK_SIZE, verify=True, emit_mhl=True, dry_run=False, progress="tui"):
    src_root = Path(src).expanduser().resolve(strict=False)
    dst_roots = [Path(dst).expanduser().resolve(strict=False) for dst in dsts]
    if not src_root.exists():
        raise FileNotFoundError("source missing: {0}".format(src_root))
    if not dst_roots:
        raise ValueError("at least one destination is required")

    state_path = Path(resume).expanduser().resolve(strict=False) if resume else Path.cwd() / "offload-state.json"
    state = _load_state(state_path) if resume else None
    if state is None:
        state = _state_template(src_root, dst_roots, hash_algo, retry_limit, include_heic, chunk_size)
    source_files = _collect_sources(src_root, include_heic=include_heic)
    state = _ensure_file_records(state, source_files, dst_roots)
    _save_state(state_path, state)

    summary = {}
    all_ok = True
    any_ok = False

    for dst_root in dst_roots:
        dst_key = str(dst_root)
        dst_state = state["destinations"][dst_key]
        dst_state["status"] = "running"
        dst_state["verified_files"] = 0
        dst_state["failed_files"] = 0
        _save_state(state_path, state)

        if not _check_destination_mount(dst_root):
            dst_state["status"] = "failed"
            dst_state["error"] = "destination mount unavailable"
            dst_state["failed_files"] = len(source_files)
            _save_state(state_path, state)
            summary[dst_key] = dst_state
            all_ok = False
            continue

        verified_rel_paths = []
        failed = 0
        for file_entry in state["files"]:
            src_path = Path(file_entry["source"])
            rel_path = file_entry["rel"]
            if rel_path is None:
                rel_path = _normalize_relpath(src_path, src_root)
                file_entry["rel"] = rel_path
            file_state = file_entry["destinations"][dst_key]
            if file_state["status"] == "verified" and (dst_root / rel_path).exists():
                verified_rel_paths.append(rel_path)
                continue
            if dry_run:
                file_state["status"] = "skipped"
                continue
            ok = _copy_single_file(src_path, dst_root, rel_path, file_state, hash_algo, retry_limit, chunk_size, state_path, state, dst_key)
            if ok:
                verified_rel_paths.append(rel_path)
            else:
                failed += 1

        dst_state["verified_files"] = len(verified_rel_paths)
        dst_state["failed_files"] = failed
        dst_state["status"] = "done" if failed == 0 else "partial"
        _save_state(state_path, state)

        mhl_path = None
        if emit_mhl:
            mhl_path = _write_mhl(dst_root, verified_rel_paths, hash_algo, now=_now_utc())
            dst_state["mhl_path"] = str(mhl_path)
            _save_state(state_path, state)
            if verify:
                verify_result = _verify_emitted_mhl(dst_root, mhl_path)
                if verify_result is not None:
                    code, _, message = verify_result
                    if code != 0:
                        raise RuntimeError("mhl verify failed for {0}: {1}".format(mhl_path, message))

        summary[dst_key] = {
            "verified_files": len(verified_rel_paths),
            "failed_files": failed,
            "mhl_path": str(mhl_path) if mhl_path else None,
            "status": dst_state["status"],
        }
        if failed == 0:
            any_ok = True
        else:
            all_ok = False

    if all_ok:
        exit_code = 0
    elif any_ok:
        exit_code = 1
    else:
        exit_code = 2
    return exit_code, summary, state_path


def build_parser():
    parser = argparse.ArgumentParser(description="arkiv offload")
    parser.add_argument("--src", required=True)
    parser.add_argument("--dst", action="append", required=True)
    parser.add_argument("--hash", dest="hash_algo", default="xxh3", choices=["xxh3", "md5", "sha1", "sha256"])
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--no-mhl", action="store_true")
    parser.add_argument("--include-heic", action="store_true")
    parser.add_argument("--resume", default=None)
    parser.add_argument("--progress", default="tui", choices=["json", "tui"])
    parser.add_argument("--retry-limit", type=int, default=DEFAULT_RETRY_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    hash_algo = {
        "xxh3": "xxh3-128",
        "md5": "md5",
        "sha1": "sha1",
        "sha256": "sha256",
    }[args.hash_algo]
    try:
        code, summary, state_path = run_offload(
            args.src,
            args.dst,
            hash_algo=hash_algo,
            include_heic=args.include_heic,
            resume=args.resume,
            retry_limit=args.retry_limit,
            verify=not args.no_verify,
            emit_mhl=not args.no_mhl,
            dry_run=args.dry_run,
            progress=args.progress,
        )
    except ValueError as exc:
        print(str(exc))
        return 4
    except FileNotFoundError as exc:
        print(str(exc))
        return 4
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print(json.dumps({"state": str(state_path), "summary": summary}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
