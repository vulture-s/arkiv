#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
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


DEFAULT_HASH = "xxh3"
DEFAULT_RETRY_LIMIT = 3
DEFAULT_CHUNK_SIZE = 1 << 20
STATE_VERSION = 1
TEST_MUTATOR = None


def _now_utc():
    return datetime.now(timezone.utc)


def _ensure_parent(path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _normalize_relpath(path, root):
    rel = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    return unicodedata.normalize("NFC", rel.as_posix())


# ── naming / folder policy (DIT wrapper ①) ──────────────────────────────────
# `--organize "{date}/{camera}/{reel}"` lays files out by camera metadata instead
# of mirroring the card's structure — the thing Gate's folder logic got wrong.
# Tokens: {date} {camera} {reel} {stem} {ext}. The original filename is always
# appended, so the template defines FOLDERS only. Token values are sanitized to a
# single safe path segment (no separators / traversal) before substitution, so a
# camera string like "Sony/FX30" can't spawn an extra directory.
_ORGANIZE_TOKENS = ("date", "camera", "reel", "stem", "ext")
_UNSAFE_SEGMENT_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize_token(value):
    """Coerce a metadata value into one filesystem-safe path segment.
    Empty / None → 'UNKNOWN'. Never returns '', '.', '..', or a separator."""
    if value is None or str(value).strip() == "":
        return "UNKNOWN"
    v = unicodedata.normalize("NFC", str(value)).strip()
    v = _UNSAFE_SEGMENT_RE.sub("_", v)
    v = v.replace("..", "_").strip(". ")
    v = re.sub(r"\s+", " ", v)
    return v[:64] if v else "UNKNOWN"


def _exiftool_path():
    try:
        import config
        return getattr(config, "EXIFTOOL_PATH", "exiftool")
    except Exception:
        return "exiftool"


def _probe_camera_meta(path):
    """Best-effort {date, camera, reel} for naming templates. exiftool first, then a
    Sony XAVC NRT sidecar (`<stem>M01.XML`) fallback for camera/date, then file mtime
    for date. Never raises — an offload must not fail on a metadata hiccup."""
    meta = {"date": None, "camera": None, "reel": None}
    path = Path(path)
    try:
        out = subprocess.run(
            [_exiftool_path(), "-json", "-Make", "-Model", "-CreateDate",
             "-DateTimeOriginal", "-ReelName", "-CameraReelName", str(path)],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
        if out.returncode == 0 and out.stdout.strip():
            d = json.loads(out.stdout)[0]
            make, model = d.get("Make"), d.get("Model")
            if model:
                meta["camera"] = "{0} {1}".format(make, model) if make and make not in str(model) else str(model)
            elif make:
                meta["camera"] = str(make)
            raw_date = d.get("CreateDate") or d.get("DateTimeOriginal")
            if raw_date:
                meta["date"] = str(raw_date)[:10].replace(":", "-")  # "2026:03:09 .." → "2026-03-09"
            meta["reel"] = d.get("ReelName") or d.get("CameraReelName")
    except Exception:
        pass
    if not meta["camera"] or not meta["date"]:
        sidecar = path.with_name(path.stem + "M01.XML")
        if sidecar.exists():
            try:
                import xml.etree.ElementTree as ET
                root = ET.parse(sidecar).getroot()
                ns = {"x": root.tag.split("}")[0].lstrip("{")} if "}" in root.tag else {}
                dev = root.find(".//x:Device", ns) if ns else root.find(".//Device")
                if dev is not None and not meta["camera"]:
                    mk, md = dev.get("manufacturer"), dev.get("modelName")
                    meta["camera"] = "{0} {1}".format(mk, md) if mk and md else (md or mk)
                cd = root.find(".//x:CreationDate", ns) if ns else root.find(".//CreationDate")
                if cd is not None and not meta["date"] and cd.get("value"):
                    meta["date"] = cd.get("value")[:10]
            except Exception:
                pass
    if not meta["date"]:
        try:
            meta["date"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        except Exception:
            pass
    return meta


def _organize_relpath(src_file, template, meta):
    """Resolve a folder template + the file's metadata into a destination relpath
    (folders from the template, original filename appended)."""
    values = {
        "date": _sanitize_token(meta.get("date")),
        "camera": _sanitize_token(meta.get("camera")),
        "reel": _sanitize_token(meta.get("reel")),
        "stem": _sanitize_token(src_file.stem),
        "ext": _sanitize_token(src_file.suffix.lstrip(".")),
    }
    resolved = template
    for tok, val in values.items():
        resolved = resolved.replace("{" + tok + "}", val)
    resolved = re.sub(r"\{[^}]*\}", "UNKNOWN", resolved)  # any unknown token → UNKNOWN
    # Defense-in-depth: sanitize EVERY segment (template literals too, not just token
    # values). Normalize "\\"→"/" first so a Windows-style literal can't smuggle an
    # extra separator, then strip drive colons / unsafe chars / traversal per segment.
    # Without this a literal like "C:\\out\\..\\{date}" would escape dst_root on Windows.
    resolved = resolved.replace("\\", "/")
    parts = []
    for raw in resolved.split("/"):
        seg = _UNSAFE_SEGMENT_RE.sub("_", raw).replace("..", "_").strip(". ")
        if seg not in ("", ".", ".."):
            parts.append(seg)
    folder = "/".join(parts)
    name = unicodedata.normalize("NFC", src_file.name)
    return (folder + "/" + name) if folder else name


def _validate_organize_template(template):
    if not any(("{" + tok + "}") in template for tok in _ORGANIZE_TOKENS):
        raise ValueError(
            "--organize template must contain at least one of {0}".format(
                ", ".join("{" + t + "}" for t in _ORGANIZE_TOKENS)))
    if template.startswith("/") or template.startswith("\\"):
        raise ValueError("--organize template must be relative (no leading slash)")
    if ":" in template:
        raise ValueError("--organize template must not contain ':' (drive letters / streams)")


def _fallback_hasher(algo):
    if algo == "xxh3":
        if xxhash is None:
            raise RuntimeError("xxhash module required for xxh3 hashing")
        return xxhash.xxh3_64()
    if algo == "md5":
        return hashlib.md5()
    if algo == "sha1":
        return hashlib.sha1()
    if algo == "sha256":
        return hashlib.sha256()
    raise ValueError("unsupported algo: {0}".format(algo))


def _hash_file(path, algo=DEFAULT_HASH):
    if mhl is not None and hasattr(mhl, "hash_file"):
        return mhl.hash_file(path, algo=algo)
    hasher = _fallback_hasher(algo)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(DEFAULT_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().lower()


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


def _ensure_file_records(state, source_files, dsts, organize=None):
    if state.get("files"):
        return state
    state["files"] = []
    rel_seen = {}  # rel → source, only meaningful under --organize (mirror can't collide)
    for src_file in source_files:
        if organize:
            rel = _organize_relpath(src_file, organize, _probe_camera_meta(src_file))
            # Case-fold the key: on a case-insensitive destination (default macOS /
            # Windows) "C0001.MP4" and "c0001.mp4" are the same file — exact-string
            # matching would miss that and the second copy would overwrite the first.
            key = rel.casefold()
            if key in rel_seen:
                raise ValueError(
                    "--organize collision: '{0}' and '{1}' both map to '{2}' "
                    "(case-insensitive). Add {{stem}} or {{reel}} to the template "
                    "to disambiguate.".format(rel_seen[key], src_file, rel))
            rel_seen[key] = src_file
        else:
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


def _write_mhl(dst_root, hash_algo, op="offload"):
    if mhl is None or not hasattr(mhl, "create_manifest"):
        raise RuntimeError("mhl.create_manifest required for MHL emit")
    dst_root = Path(dst_root).expanduser().resolve(strict=False)
    output_dir = dst_root / "ascmhl"
    mhl_path, _chain_path = mhl.create_manifest(
        source=dst_root,
        output=output_dir,
        primary_hash=hash_algo,
        op=op,
    )
    return mhl_path


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
            with src_path.open("rb") as src_handle, partial_path.open("wb") as dst_handle:
                hasher = _fallback_hasher(hash_algo)
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
            dst_hash = _hash_file(partial_path, hash_algo)
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


def _verify_emitted_mhl(dst_root, mhl_path):
    if mhl is None or not hasattr(mhl, "verify_manifest"):
        return None
    return mhl.verify_manifest(mhl_path)


def run_offload(src, dsts, hash_algo=DEFAULT_HASH, include_heic=False, resume=None, retry_limit=DEFAULT_RETRY_LIMIT, chunk_size=DEFAULT_CHUNK_SIZE, verify=True, emit_mhl=True, dry_run=False, progress="tui", organize=None):
    src_root = Path(src).expanduser().resolve(strict=False)
    dst_roots = [Path(dst).expanduser().resolve(strict=False) for dst in dsts]
    if not src_root.exists():
        raise FileNotFoundError("source missing: {0}".format(src_root))
    if not dst_roots:
        raise ValueError("at least one destination is required")
    if organize:
        _validate_organize_template(organize)

    state_path = Path(resume).expanduser().resolve(strict=False) if resume else Path.cwd() / "offload-state.json"
    state = _load_state(state_path) if resume else None
    if state is None:
        state = _state_template(src_root, dst_roots, hash_algo, retry_limit, include_heic, chunk_size)
        if organize:
            state["organize"] = organize
    else:
        # Resuming: the layout is frozen in the loaded state (rels already computed).
        # Omitting --organize adopts the stored layout (no need to re-pass the flag);
        # passing a *different* template would print a new header but silently NOT
        # apply, so that is refused.
        stored = state.get("organize")
        if organize is None:
            organize = stored
        elif (organize or None) != (stored or None):
            raise ValueError(
                "--organize mismatch on resume: state was built with {0!r}, "
                "requested {1!r}. Resume with the original template, or omit "
                "--organize to reuse it.".format(stored, organize))
    source_files = _collect_sources(src_root, include_heic=include_heic)
    state = _ensure_file_records(state, source_files, dst_roots, organize=organize)
    _save_state(state_path, state)

    if organize and (dry_run or progress == "tui"):
        print("Organize layout ({0}):".format(organize))
        for fe in state["files"][:50]:
            print("  {0}  →  {1}".format(Path(fe["source"]).name, fe["rel"]))
        if len(state["files"]) > 50:
            print("  … and {0} more".format(len(state["files"]) - 50))

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
        if emit_mhl and verified_rel_paths:
            mhl_path = _write_mhl(dst_root, hash_algo, op="offload")
            dst_state["mhl_path"] = str(mhl_path)
            _save_state(state_path, state)
            if verify:
                verify_result = _verify_emitted_mhl(dst_root, mhl_path)
                if verify_result is not None and verify_result != 0:
                    raise RuntimeError("mhl verify failed for {0}: exit code {1}".format(mhl_path, verify_result))

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
    parser.add_argument(
        "--organize", default=None, metavar="TEMPLATE",
        help='Lay files out by camera metadata instead of mirroring the card. '
             'Folder template with tokens {date}/{camera}/{reel}/{stem}/{ext} '
             '(original filename always appended), e.g. "{date}/{camera}/{reel}". '
             'Pair with --dry-run to preview the layout. Metadata via ExifTool + '
             'Sony XAVC sidecar; missing values → UNKNOWN.')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code, summary, state_path = run_offload(
            args.src,
            args.dst,
            hash_algo=args.hash_algo,
            include_heic=args.include_heic,
            resume=args.resume,
            retry_limit=args.retry_limit,
            verify=not args.no_verify,
            emit_mhl=not args.no_mhl,
            dry_run=args.dry_run,
            progress=args.progress,
            organize=args.organize,
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
