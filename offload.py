#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
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


def _meta_from_exif_dict(d):
    """Extract {date, camera, reel} from one exiftool JSON record (or {})."""
    meta = {"date": None, "camera": None, "reel": None}
    if not d:
        return meta
    make, model = d.get("Make"), d.get("Model")
    if model:
        meta["camera"] = "{0} {1}".format(make, model) if make and make not in str(model) else str(model)
    elif make:
        meta["camera"] = str(make)
    raw_date = d.get("CreateDate") or d.get("DateTimeOriginal")
    if raw_date:
        meta["date"] = str(raw_date)[:10].replace(":", "-")  # "2026:03:09 .." → "2026-03-09"
    meta["reel"] = d.get("ReelName") or d.get("CameraReelName")
    return meta


def _apply_meta_fallbacks(meta, path):
    """In place: Sony XAVC NRT sidecar (`<stem>M01.XML`) fallback for camera/date, then
    file mtime for date. Never raises — an offload must not fail on a metadata hiccup."""
    path = Path(path)
    if not meta.get("camera") or not meta.get("date"):
        sidecar = path.with_name(path.stem + "M01.XML")
        if sidecar.exists():
            try:
                import xml.etree.ElementTree as ET
                root = ET.parse(sidecar).getroot()
                ns = {"x": root.tag.split("}")[0].lstrip("{")} if "}" in root.tag else {}
                dev = root.find(".//x:Device", ns) if ns else root.find(".//Device")
                if dev is not None and not meta.get("camera"):
                    mk, md = dev.get("manufacturer"), dev.get("modelName")
                    meta["camera"] = "{0} {1}".format(mk, md) if mk and md else (md or mk)
                cd = root.find(".//x:CreationDate", ns) if ns else root.find(".//CreationDate")
                if cd is not None and not meta.get("date") and cd.get("value"):
                    meta["date"] = cd.get("value")[:10]
            except Exception:
                pass
    if not meta.get("date"):
        try:
            meta["date"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        except Exception:
            pass
    return meta


def _exiftool_batch(paths):
    """ONE exiftool subprocess for many files → {abspath: exif_dict}; {} on failure.
    The per-file subprocess spawn was the bottleneck for previews / large-card organize
    (a 400-file card = 400 spawns); this batches it into a single call."""
    paths = list(paths)
    if not paths:
        return {}
    try:
        out = subprocess.run(
            [_exiftool_path(), "-json", "-Make", "-Model", "-CreateDate",
             "-DateTimeOriginal", "-ReelName", "-CameraReelName"] + [str(p) for p in paths],
            capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
        # exiftool returns nonzero when *some* files error but still emits JSON for the
        # rest — accept any JSON output we got.
        if not out.stdout.strip():
            return {}
        result = {}
        for d in json.loads(out.stdout):
            sf = d.get("SourceFile")  # exiftool -json always includes SourceFile
            if sf:
                result[os.path.abspath(sf)] = d
        return result
    except Exception:
        return {}


def _probe_camera_meta(path):
    """Single-file {date, camera, reel} for naming templates. Never raises."""
    d = _exiftool_batch([path]).get(os.path.abspath(str(path)), {})
    return _apply_meta_fallbacks(_meta_from_exif_dict(d), path)


def _probe_camera_meta_batch(paths):
    """Bulk {str(path): {date, camera, reel}} with ONE exiftool spawn for all files
    (vs one per file). Sidecar/mtime fallbacks stay per-file but are cheap (no
    subprocess). Used by previews and large-card organize."""
    paths = list(paths)
    exif = _exiftool_batch(paths)
    return {str(p): _apply_meta_fallbacks(_meta_from_exif_dict(exif.get(os.path.abspath(str(p)), {})), p)
            for p in paths}


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


def preview_layout(src, organize=None, include_heic=False, limit=0):
    """Return a layout preview without copying anything — powers the DIT Offload UI.

    Result: {"src": <root>, "count": N, "organize": template|None,
             "files": [{"source": abs, "rel": dest-relpath, "size_mb": f}, ...]}.
    For --organize each rel is the camera-metadata folder path; otherwise the mirror
    of the source tree. Pure / read-only (no copy, no state file)."""
    if organize:
        _validate_organize_template(organize)
    src_root = Path(src).expanduser().resolve(strict=False)
    if not src_root.exists():
        raise FileNotFoundError("source missing: {0}".format(src_root))
    files = _collect_sources(src_root, include_heic=include_heic)
    if limit and limit > 0:
        files = files[:limit]
    meta_map = _probe_camera_meta_batch(files) if organize else {}  # one exiftool spawn
    out = []
    for f in files:
        rel = (_organize_relpath(f, organize, meta_map[str(f)]) if organize
               else _normalize_relpath(f, src_root))
        try:
            size_mb = round(f.stat().st_size / 1048576, 1)
        except OSError:
            size_mb = None
        out.append({"source": str(f), "rel": rel, "size_mb": size_mb})
    return {"src": str(src_root), "count": len(out), "organize": organize or None, "files": out}


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


# OS bookkeeping that macOS / Windows write onto a card just by mounting or
# browsing it (Finder → .DS_Store, AppleDouble ._*, Spotlight, fseventsd, the
# Trash, Windows' System Volume Information). They are not footage: copying them
# made "the same card, opened in Finder" fingerprint as a different card (no
# resume) and turned the rewritten .DS_Store into a destination conflict on a
# re-run (#497 review B1). Excluded from enumeration, so from fingerprint, copy,
# conflict checks and the resume set alike.
_OS_METADATA_DIRS = frozenset((
    ".fseventsd", ".spotlight-v100", ".trashes", ".temporaryitems",
    ".documentrevisions-v100", "system volume information", "$recycle.bin",
))
_OS_METADATA_FILES = frozenset((".ds_store", "thumbs.db", "desktop.ini", ".volumeicon.icns", ".apdisk"))


def _is_os_metadata(path, root):
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        rel_parts = path.parts
    if any(part.lower() in _OS_METADATA_DIRS for part in rel_parts[:-1]):
        return True
    name = path.name
    return name.lower() in _OS_METADATA_FILES or name.startswith("._")


def _collect_sources(src, include_heic=False):
    root = Path(src).expanduser().resolve(strict=False)
    if root.is_file():
        return [root]
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # Relative parts only: a card mounted under (or a project folder named)
        # ".../ascmhl/..." used to drop EVERY file and report done with 0 files.
        if any(part == "ascmhl" for part in path.relative_to(root).parts[:-1]):
            continue
        if _is_os_metadata(path, root):
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
    # R5-17 (#19): write atomically (tmp on the same dir + os.replace) so a crash
    # or SIGTERM mid-write can't leave a half-written JSON that _load_state then
    # fails to parse — the resume path depends on this file always being valid.
    path = Path(path)
    _ensure_parent(path)
    data = json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(str(tmp), str(path))


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


def _stat_sig(path):
    st = path.stat()
    return st.st_size, st.st_mtime_ns


_SAMPLE_BYTES = 64 * 1024


def _content_sample(path):
    """sha1 over size + first/last 64 KiB. relpath+size+mtime alone let two
    cards whose clips share name, size and mtime (unset camera clocks,
    constant-bitrate codecs) fingerprint and resume as the SAME card — card B
    was skipped as verified with code 0 (dual-track audit of #497). Two small
    reads per file, never the whole clip. This narrows the collision, it does
    not close it: two clips identical in name, size, mtime AND first/last 64 KiB
    still look alike (only a full-content hash would). None = unreadable."""
    path = Path(path)
    try:
        size = path.stat().st_size
        h = hashlib.sha1(str(size).encode("ascii"))
        with path.open("rb") as fh:
            h.update(fh.read(_SAMPLE_BYTES))
            if size > _SAMPLE_BYTES:
                fh.seek(max(size - _SAMPLE_BYTES, _SAMPLE_BYTES))
                h.update(fh.read(_SAMPLE_BYTES))
    except OSError:
        # One unreadable clip must not block the whole card (it used to be a
        # 400 / traceback); the copy step records that file as failed.
        return None
    return h.hexdigest()


def source_fingerprint(source_files, src_root):
    """Identity of a card's CONTENT (not its mount path): sha1 over every file's
    relpath + size + mtime_ns. Two cards mounted at the same /Volumes/Untitled get
    different fingerprints; the same card re-inserted gets the same one."""
    h = hashlib.sha1()
    root = Path(src_root)
    for f in source_files:
        size, mtime_ns = _stat_sig(f)
        try:
            rel = _normalize_relpath(f, root)
        except ValueError:
            rel = str(f)
        h.update("{0}\0{1}\0{2}\0{3}\n".format(rel, size, mtime_ns, _content_sample(f)).encode("utf-8"))
    return h.hexdigest()


def _assert_state_matches_source(state, source_files, src_root):
    """Refuse to resume a state that was built from a DIFFERENT card.

    Audit 2026-10-09 (HIGH): the resume state used to be trusted blindly — once it
    had `files`, the source was never re-enumerated. A second card at the same
    mount point (every Sony card mounts as /Volumes/Untitled) then reused card A's
    records: card B's new files were never seen, its same-named C0001 was skipped
    as "already verified", and the run reported done / code 0. A DIT who formats
    on "done" loses card B. So: the source path, the set of files, and each file's
    size (+ mtime when the state recorded it) must all match."""
    problems = []
    stored_src = state.get("source")
    if stored_src and Path(stored_src) != Path(src_root):
        problems.append("source path {0!r} != {1!r}".format(stored_src, str(src_root)))
    records = {fe["source"]: fe for fe in state.get("files", [])}
    current = {str(f): f for f in source_files}
    missing = sorted(set(records) - set(current))
    extra = sorted(set(current) - set(records))
    if missing:
        problems.append("{0} file(s) in state not on source (e.g. {1})".format(
            len(missing), Path(missing[0]).name))
    if extra:
        problems.append("{0} file(s) on source not in state (e.g. {1})".format(
            len(extra), Path(extra[0]).name))
    for key in sorted(set(records) & set(current)):
        rec = records[key]
        size, mtime_ns = _stat_sig(current[key])
        if rec.get("size") != size or (rec.get("mtime_ns") is not None and rec["mtime_ns"] != mtime_ns):
            problems.append("{0} changed (size/mtime differ)".format(Path(key).name))
            break
        if "sample" not in rec:
            problems.append("state predates content sampling (no 'sample' per file)")
            break
        cur_sample = _content_sample(current[key])
        if rec["sample"] is not None and cur_sample is not None and rec["sample"] != cur_sample:
            problems.append("{0} content differs (same name/size/mtime, different bytes)".format(Path(key).name))
            break
    if problems:
        raise ValueError(
            "resume state does not match this source — it was built from a different "
            "card or the card changed: {0}. Refusing to resume (nothing was copied). "
            "Start a fresh offload without --resume, or point --resume at a new "
            "state file.".format("; ".join(problems)))


def _ensure_file_records(state, source_files, dsts, organize=None):
    if state.get("files"):
        return state
    state["files"] = []
    rel_seen = {}  # rel → source, only meaningful under --organize (mirror can't collide)
    meta_map = _probe_camera_meta_batch(source_files) if organize else {}  # one exiftool spawn
    for src_file in source_files:
        if organize:
            rel = _organize_relpath(src_file, organize, meta_map[str(src_file)])
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
        size, mtime_ns = _stat_sig(src_file)
        state["files"].append(
            {
                "rel": rel,
                "source": str(src_file),
                "size": size,
                "mtime_ns": mtime_ns,
                "sample": _content_sample(src_file),
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


def _dest_still_matches(final_path, file_entry):
    """Resume may skip a file only if the destination still holds THIS clip.
    `exists()` alone accepted a different drive mounted at the same path (two
    shuttle drives with the same name) holding a different C0001 — done, code 0
    (dual-track audit of #497). Size + head/tail sample is cheap; anything that
    fails falls through to _copy_single_file, which hash-compares and never
    overwrites."""
    try:
        if not final_path.is_file() or final_path.stat().st_size != file_entry.get("size"):
            return False
        sample = file_entry.get("sample")
        # No sample (source was unreadable when recorded) → don't skip; the copy
        # step hash-compares and never overwrites.
        return sample is not None and _content_sample(final_path) == sample
    except OSError:
        return False


def _write_mhl(dst_root, hash_algo, op="offload", previous_paths=None):
    if mhl is None or not hasattr(mhl, "create_manifest"):
        raise RuntimeError("mhl.create_manifest required for MHL emit")
    dst_root = Path(dst_root).expanduser().resolve(strict=False)
    output_dir = dst_root / "ascmhl"
    mhl_path, _chain_path = mhl.create_manifest(
        source=dst_root,
        output=output_dir,
        primary_hash=hash_algo,
        op=op,
        **({"previous_paths": previous_paths} if previous_paths else {})
    )
    return mhl_path


def _mhl_changed_vs_history(current_mhl):
    if mhl is None or not hasattr(mhl, "changed_against_history") or not Path(current_mhl).exists():
        return []
    return mhl.changed_against_history(current_mhl)


def _check_destination_mount(dst_root):
    try:
        import health
    except Exception:
        return True
    return health._check_mount(dst_root)


# ── same-name conflicts → needs_rename (Hevin 2026-10-09 23:14) ─────────────
# Exit codes: 0 done · 1 some destination failed (another is OK) · 2 all failed ·
# 3 only name conflicts are left (needs_rename) · 4 refused before copying (CLI) ·
# 5 the user skipped a conflict — that clip is NOT backed up, the card is not done.
EXIT_NEEDS_RENAME = 3
EXIT_INCOMPLETE = 5
_HASH_PREFIX_LEN = 12


def _iso_mtime(path):
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _size_or_none(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def _mark_needs_rename(file_state, src_path, final_path, existing_hash, src_hash, why):
    file_state["status"] = "needs_rename"
    file_state["partial"] = None
    file_state["conflict"] = {
        "existing": {
            "path": str(final_path),
            "size": _size_or_none(final_path),
            "mtime": _iso_mtime(final_path),
            "hash_prefix": (existing_hash or "")[:_HASH_PREFIX_LEN] or None,
        },
        "incoming": {
            "path": str(src_path),
            "size": _size_or_none(src_path),
            "mtime": _iso_mtime(src_path),
            "hash_prefix": (src_hash or "")[:_HASH_PREFIX_LEN] or None,
        },
        "why": why,
    }
    file_state["error"] = (
        "destination already has a different file with this name: {0} ({1}); not "
        "overwritten — choose a new name for the incoming clip".format(final_path, why))


def _dst_rel(file_entry, file_state):
    """Where this clip lives on THIS destination: the card layout, unless the user
    renamed it there to resolve a conflict."""
    return file_state.get("dst_rel") or file_entry["rel"]


def _taken_rels(state, dst_key):
    """Every destination-relative name this card's run uses on `dst_key`,
    case-folded (default macOS / Windows volumes are case-insensitive)."""
    taken = set()
    for fe in state.get("files", []):
        if fe.get("rel"):
            taken.add(fe["rel"].casefold())
        fs = fe.get("destinations", {}).get(dst_key) or {}
        if fs.get("dst_rel"):
            taken.add(fs["dst_rel"].casefold())
        sug = (fs.get("conflict") or {}).get("suggested_rel")
        if sug:
            taken.add(sug.casefold())
    return taken


def _name_free(dst_root, rel, taken):
    if rel.casefold() in taken:
        return False
    target = Path(dst_root) / rel
    return not target.exists() and not target.with_name(target.name + ".partial").exists()


def _suggest_rename(dst_root, rel, taken):
    """`C0001.MP4` → `C0001 (2).MP4`, `(3)`, … — the first one that is neither on
    the destination nor used by any other clip of this run. Predictable (Finder
    convention), and never a name that would collide again."""
    parent, _, name = rel.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    for n in range(2, 100000):
        cand_name = "{0} ({1}){2}".format(stem, n, ("." + ext) if ext else "")
        cand = (parent + "/" + cand_name) if parent else cand_name
        if _name_free(dst_root, cand, taken):
            return cand
    raise RuntimeError("no free name for {0}".format(rel))


_BAD_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def _validate_new_name(new_name):
    """A user-typed replacement FILE NAME (not a path). Refuse rather than
    sanitize: silently rewriting what the user typed would put the clip under a
    name they never saw."""
    if not isinstance(new_name, str):
        raise ValueError("new name is required")
    name = unicodedata.normalize("NFC", new_name)
    if not name.strip():
        raise ValueError("new name is empty")
    if name != name.strip():
        raise ValueError("new name must not start or end with spaces")
    if _BAD_NAME_RE.search(name):
        raise ValueError("new name must be a plain file name (no / \\ : * ? \" < > | or control characters)")
    if name in (".", "..") or name.startswith("."):
        raise ValueError("new name must not start with '.'")
    low = name.lower()
    if low.endswith(".partial") or low.endswith(".mhl") or low == "ascmhl":
        raise ValueError("new name uses a reserved suffix (.partial / .mhl / ascmhl)")
    if len(name.encode("utf-8")) > 255:
        raise ValueError("new name is too long")
    return name


def _dst_status(failed, needs_rename, skipped, mhl_error):
    if mhl_error:
        return "failed"
    if failed:
        return "partial"
    if needs_rename:
        return "needs_rename"
    if skipped:
        return "incomplete"
    return "done"


def overall_status(summary):
    """One word for the whole card across every destination. Only "done" means
    every clip is on every drive, hash-verified, with a manifest — the only state
    in which a DIT may format the card."""
    statuses = [s.get("status") for s in summary.values()]
    if not statuses:
        return "failed"
    if all(st == "done" for st in statuses):
        return "done"
    if any(st in ("failed", "partial") for st in statuses):
        return "failed"
    if any(st == "needs_rename" for st in statuses):
        return "needs_rename"
    return "incomplete"


def _exit_code(summary):
    status = overall_status(summary)
    if status == "done":
        return 0
    if status == "failed":
        return 1 if any(s.get("status") == "done" for s in summary.values()) else 2
    return EXIT_NEEDS_RENAME if status == "needs_rename" else EXIT_INCOMPLETE


def _conflict_item(state, file_entry, dst_key):
    fs = file_entry["destinations"][dst_key]
    c = fs.get("conflict") or {}
    return {
        "dst": dst_key,
        "rel": file_entry["rel"],
        "source": file_entry["source"],
        "card": Path(state.get("source") or "").name,
        "existing": c.get("existing"),
        "incoming": c.get("incoming"),
        "suggested_name": c.get("suggested_name"),
        "suggested_rel": c.get("suggested_rel"),
        "error": fs.get("error"),
    }


def _dst_summary_from_state(state, dst_key):
    dst_state = state["destinations"][dst_key]
    verified, failed, skipped, renamed, items = 0, 0, [], [], []
    for fe in state.get("files", []):
        fs = fe["destinations"].get(dst_key) or {}
        st = fs.get("status")
        if st == "verified":
            verified += 1
            if fs.get("renamed_from"):
                renamed.append({"from": fs["renamed_from"], "to": fs["dst_rel"]})
        elif st == "needs_rename":
            items.append(_conflict_item(state, fe, dst_key))
        elif st == "skipped_unbacked":
            skipped.append(fe["rel"])
        else:
            failed += 1
    mhl_error = dst_state.get("mhl_error")
    status = _dst_status(failed, len(items), len(skipped), mhl_error)
    dst_state["status"] = status
    dst_state["verified_files"] = verified
    dst_state["failed_files"] = failed
    return {
        "verified_files": verified,
        "failed_files": failed,
        "conflict_files": [i["rel"] for i in items],
        "needs_rename": items,
        "skipped_unbacked": skipped,
        "renamed": renamed,
        "mhl_path": dst_state.get("mhl_path"),
        "status": status,
        "error": mhl_error,
    }


def _copy_single_file(src_path, dst_root, rel_path, file_state, hash_algo, retry_limit, chunk_size, state_path, state, dst_key):
    dst_root = Path(dst_root).expanduser().resolve(strict=False)
    final_path = dst_root / rel_path
    partial_path = final_path.with_name(final_path.name + ".partial")
    _ensure_parent(final_path)

    # Never clobber (audit 2026-10-09 HIGH): a file already at the destination is
    # either THIS clip from an earlier run (identical bytes → verified, nothing to
    # copy) or a DIFFERENT clip that happens to share the name — a second card's
    # C0001.MP4, or an --organize template without {stem}/{reel}. The second case
    # used to be os.replace'd over the first card's verified backup with code 0.
    # It is never overwritten. Hevin 2026-10-09 23:14: it is not a silent skip
    # either — the file is marked `needs_rename` with what a human needs to decide
    # (both sides' size / mtime / hash prefix) and the run does not read as done
    # until someone picks a new name (resolve_conflict) or explicitly skips it.
    if final_path.exists():
        conflict = None
        existing_hash = None
        src_hash = None
        try:
            existing_hash = _hash_file(final_path, hash_algo)
            if final_path.stat().st_size != src_path.stat().st_size:
                conflict = "size differs"
            else:
                src_hash = _hash_file(src_path, hash_algo)
                if existing_hash == src_hash:
                    file_state["status"] = "verified"
                    file_state["src_hash"] = src_hash
                    file_state["dst_hash"] = existing_hash
                    file_state["partial"] = None
                    file_state["error"] = None
                    file_state.pop("conflict", None)
                    _save_state(state_path, state)
                    return True
                conflict = "content differs ({0} {1} != {2})".format(hash_algo, existing_hash, src_hash)
        except OSError as exc:
            conflict = "could not compare: {0}".format(exc)
        _mark_needs_rename(file_state, src_path, final_path, existing_hash, src_hash, conflict)
        _save_state(state_path, state)
        return False

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
            if final_path.exists():
                # Appeared while we were copying (another run / tool). Same rule as
                # above: never replace an existing file.
                existing_hash = _hash_file(final_path, hash_algo)
                partial_path.unlink()
                if existing_hash != src_hash:
                    _mark_needs_rename(file_state, src_path, final_path, existing_hash, src_hash,
                                       "appeared during copy with different content")
                    _save_state(state_path, state)
                    return False
            else:
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


def _emit(progress, event):
    """Emit a single-line JSON progress event on stdout in --progress json mode (so a
    caller — the /api/offload stream / a CLI consumer — can show live progress). No-op
    in tui mode. Single line so a line-based reader can parse each event."""
    if progress == "json":
        print(json.dumps(event, ensure_ascii=False), flush=True)


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
    if not source_files:
        # "done, 0 files" reads as a finished card to a DIT about to format it.
        raise ValueError(
            "no files to offload under {0} (HEIC excluded unless --include-heic; "
            "OS metadata and ascmhl/ are skipped) — refusing to report an empty "
            "offload as done".format(src_root))
    if state.get("files"):
        _assert_state_matches_source(state, source_files, src_root)
    state = _ensure_file_records(state, source_files, dst_roots, organize=organize)
    _save_state(state_path, state)

    if organize and (dry_run or progress == "tui"):
        print("Organize layout ({0}):".format(organize))
        for fe in state["files"][:50]:
            print("  {0}  →  {1}".format(Path(fe["source"]).name, fe["rel"]))
        if len(state["files"]) > 50:
            print("  … and {0} more".format(len(state["files"]) - 50))

    summary = {}

    for dst_root in dst_roots:
        dst_key = str(dst_root)
        dst_state = state["destinations"][dst_key]
        dst_state["status"] = "running"
        dst_state["verified_files"] = 0
        dst_state["failed_files"] = 0
        dst_state["mhl_error"] = None
        _save_state(state_path, state)

        if not _check_destination_mount(dst_root):
            dst_state["status"] = "failed"
            dst_state["error"] = "destination mount unavailable"
            dst_state["failed_files"] = len(source_files)
            _save_state(state_path, state)
            summary[dst_key] = dst_state
            continue

        verified_rel_paths = []
        failed = 0
        conflicts = []
        needs_rename = []
        skipped_unbacked = []
        taken = _taken_rels(state, dst_key)
        total = len(state["files"])
        _emit(progress, {"type": "dst_start", "dst": dst_key, "total": total})
        for idx, file_entry in enumerate(state["files"], 1):
            src_path = Path(file_entry["source"])
            rel_path = file_entry["rel"]
            if rel_path is None:
                rel_path = _normalize_relpath(src_path, src_root)
                file_entry["rel"] = rel_path
            file_state = file_entry["destinations"][dst_key]
            # A clip the user renamed on this drive lives at its new name there.
            rel_path = _dst_rel(file_entry, file_state)
            if file_state["status"] == "verified" and _dest_still_matches(dst_root / rel_path, file_entry):
                verified_rel_paths.append(rel_path)
                _emit(progress, {"type": "file", "dst": dst_key, "index": idx, "total": total,
                                 "name": src_path.name, "status": "skipped"})
                continue
            if dry_run:
                file_state["status"] = "skipped"
                _emit(progress, {"type": "file", "dst": dst_key, "index": idx, "total": total,
                                 "name": src_path.name, "status": "skipped"})
                continue
            prev_suggestion = (file_state.get("conflict") or {}).get("suggested_rel")
            if file_state.get("dst_rel") and file_state["status"] != "verified":
                # A renamed copy that no longer matches: start over from the card
                # layout (the copy step below never overwrites either name).
                file_state.pop("dst_rel", None)
                file_state.pop("renamed_from", None)
                rel_path = file_entry["rel"]
            ok = _copy_single_file(src_path, dst_root, rel_path, file_state, hash_algo, retry_limit, chunk_size, state_path, state, dst_key)
            ev = {"type": "file", "dst": dst_key, "index": idx, "total": total,
                  "name": src_path.name, "status": "verified" if ok else "failed"}
            if ok:
                verified_rel_paths.append(rel_path)
            elif file_state.get("status") == "needs_rename":
                # Not a copy failure and not a skip: the run stops short of "done"
                # and asks a human for a name (resolve_conflict). The suggestion
                # is kept stable across re-runs while it is still free.
                others = taken - {prev_suggestion.casefold()} if prev_suggestion else taken
                if prev_suggestion and _name_free(dst_root, prev_suggestion, others):
                    sug = prev_suggestion
                else:
                    sug = _suggest_rename(dst_root, rel_path, taken)
                taken.add(sug.casefold())
                file_state["conflict"]["suggested_rel"] = sug
                file_state["conflict"]["suggested_name"] = sug.rpartition("/")[2]
                _save_state(state_path, state)
                conflicts.append(rel_path)
                needs_rename.append(_conflict_item(state, file_entry, dst_key))
                ev["status"] = "needs_rename"
                ev["reason"] = "conflict"
                ev["error"] = file_state.get("error")
            else:
                failed += 1
            _emit(progress, ev)

        dst_state["verified_files"] = len(verified_rel_paths)
        dst_state["failed_files"] = failed
        # The copy is done; the CARD is not. `status` stays "running" through the
        # MHL passes below — writing "done" here is what let a run cancelled during
        # those passes persist a state that says finished with `mhl_path: null`,
        # which is indistinguishable from a real completion. For a DIT the manifest
        # IS the deliverable (chain of custody), so "copied" must not read as "done".
        final_status = _dst_status(failed, len(needs_rename), len(skipped_unbacked), None)
        _save_state(state_path, state)

        mhl_path = None
        mhl_error = None
        if emit_mhl and verified_rel_paths:
            # Two full re-reads of every byte happen below (write, then verify) and
            # neither emits anything on its own. Without these markers the UI sits
            # on "file N/N" for as long as the hashing takes — 35 minutes for 89 GB
            # in the 2026-09-06 field run — and looks hung while it is working.
            #
            # A manifest failure is scoped to THIS destination. It used to raise
            # straight out of the `for dst_root` loop, which cost three things at
            # once: (1) every REMAINING destination was skipped, so a hash mismatch
            # on drive 1 silently cancelled the drive-2 copy — and the second copy
            # is the entire point of a two-drive offload; (2) `summary[dst_key]` was
            # never assigned, so the router synthesised `{"type":"done","summary":{}}`
            # and the UI showed "exit 1" with no row saying which drive or why —
            # the RuntimeError text went to the merged stdout, where the ndjson
            # reader's `JSON.parse` dropped it; (3) `dst_state["status"]` stayed
            # "running", which is the same state a user-cancelled run leaves behind.
            # A verify failure IS the chain-of-custody event this tool exists to
            # report, so it must be the loudest thing in the summary, not the
            # quietest. Mirrors the `_check_destination_mount` path above.
            try:
                _emit(progress, {"type": "phase", "dst": dst_key, "phase": "mhl_write",
                                 "files": len(verified_rel_paths)})
                mhl_path = _write_mhl(dst_root, hash_algo, op="offload")
                dst_state["mhl_path"] = str(mhl_path)
                _save_state(state_path, state)
                # Chain of custody across runs: a clip recorded by an earlier
                # generation must still hash the same as when it was FIRST
                # recorded. A new generation alone would just record the new hash
                # and the chain would stay "valid" — and comparing only against the
                # previous generation alerts once, then goes green (#497 review).
                changed = _mhl_changed_vs_history(mhl_path)
                if changed:
                    raise RuntimeError(
                        "mhl history mismatch: {0} file(s) differ from their first "
                        "recorded hash: {1}".format(
                            len(changed),
                            ", ".join("{0} (first in {1})".format(p, g) for p, g in changed[:10])))
                if verify:
                    _emit(progress, {"type": "phase", "dst": dst_key, "phase": "mhl_verify",
                                     "files": len(verified_rel_paths)})
                    verify_result = _verify_emitted_mhl(dst_root, mhl_path)
                    if verify_result is not None and verify_result != 0:
                        raise RuntimeError("mhl verify failed for {0}: exit code {1}".format(mhl_path, verify_result))
            except Exception as exc:
                mhl_error = "{0}: {1}".format(type(exc).__name__, exc)
                final_status = "failed"
                dst_state["error"] = mhl_error
                dst_state["mhl_error"] = mhl_error
                _emit(progress, {"type": "phase", "dst": dst_key, "phase": "mhl_failed",
                                 "files": len(verified_rel_paths), "error": mhl_error})

        dst_state["status"] = final_status
        _save_state(state_path, state)

        summary[dst_key] = {
            "verified_files": len(verified_rel_paths),
            "failed_files": failed,
            "conflict_files": conflicts,
            "needs_rename": needs_rename,
            "skipped_unbacked": skipped_unbacked,
            "renamed": [{"from": fe["rel"], "to": fe["destinations"][dst_key]["dst_rel"]}
                        for fe in state["files"]
                        if fe["destinations"][dst_key].get("status") == "verified"
                        and fe["destinations"][dst_key].get("renamed_from")],
            "mhl_path": str(mhl_path) if mhl_path else None,
            "status": dst_state["status"],
            "error": mhl_error,
        }
        # A manifest failure counts as a failed destination even though every byte
        # copied and verified: without the manifest there is no chain of custody.
        # Reporting a manifest-less drive as OK is the failure this whole branch
        # exists to prevent. _exit_code() also keeps a needs_rename / skipped card
        # off 0: only "every clip on every drive, verified, with a manifest" is.

    exit_code = _exit_code(summary)
    return exit_code, summary, state_path


def resolve_conflict(state_path, dst, rel, action, new_name=None, confirm=False,
                     verify=True, emit_mhl=True):
    """Settle ONE `needs_rename` clip on ONE destination.

    action="rename": copy just that clip from the card to `new_name` (a plain
    file name, same folder as its card layout), with the normal copy rules —
    hash-verified, never overwrites — then write a new MHL generation that
    records the rename (<previouspath> = the card-layout path).
    action="skip": only with confirm=True; the clip is marked `skipped_unbacked`
    and the card can never read as done.

    Returns {"status": overall card status, "summary": {dst: …}, "file": {…}}.
    Raises ValueError for anything the user must fix (bad / taken name, clip
    not pending, card changed or not mounted) — nothing is written then."""
    state_path = Path(state_path).expanduser().resolve(strict=False)
    state = _load_state(state_path)
    if not state:
        raise ValueError("no offload state for this card: {0}".format(state_path))
    dst_root = Path(dst).expanduser().resolve(strict=False)
    dst_key = str(dst_root)
    if dst_key not in state.get("destinations", {}):
        raise ValueError("destination {0} is not part of this offload".format(dst_key))
    file_entry = next((fe for fe in state.get("files", []) if fe.get("rel") == rel), None)
    if file_entry is None:
        raise ValueError("{0} is not a clip of this offload".format(rel))
    file_state = file_entry["destinations"][dst_key]
    if file_state.get("status") != "needs_rename":
        raise ValueError("{0} has no pending name conflict on {1} (status: {2})".format(
            rel, dst_key, file_state.get("status")))

    if action == "skip":
        if not confirm:
            raise ValueError("skipping leaves this clip NOT backed up — confirm=True is required")
        file_state["status"] = "skipped_unbacked"
        file_state["error"] = "skipped by user after a name conflict — NOT backed up on this drive"
        _save_state(state_path, state)
    elif action == "rename":
        name = _validate_new_name(new_name)
        parent = rel.rpartition("/")[0]
        new_rel = (parent + "/" + name) if parent else name
        target = (dst_root / new_rel).resolve(strict=False)
        try:
            target.relative_to(dst_root)
        except ValueError:
            raise ValueError("new name escapes the destination folder")
        taken = _taken_rels(state, dst_key)
        own = (file_state.get("conflict") or {}).get("suggested_rel")
        if own:
            taken.discard(own.casefold())  # its own suggestion is fair game
        if not _name_free(dst_root, new_rel, taken):
            raise ValueError("{0} already exists on the destination or is taken by another "
                             "clip of this card — pick another name".format(new_rel))
        src_path = Path(file_entry["source"])
        try:
            sig_ok = src_path.is_file() and src_path.stat().st_size == file_entry.get("size") \
                and (file_entry.get("sample") is None or _content_sample(src_path) == file_entry["sample"])
        except OSError:
            sig_ok = False
        if not sig_ok:
            raise ValueError("source clip {0} is missing or changed — re-insert the same card".format(src_path))
        if not _check_destination_mount(dst_root):
            raise ValueError("destination mount unavailable: {0}".format(dst_root))
        conflict = file_state.get("conflict")
        file_state["attempts"] = 0
        ok = _copy_single_file(src_path, dst_root, new_rel, file_state, state.get("hash_algo") or DEFAULT_HASH,
                               state.get("retry_limit") or DEFAULT_RETRY_LIMIT,
                               state.get("chunk_size") or DEFAULT_CHUNK_SIZE, state_path, state, dst_key)
        if ok:
            file_state["dst_rel"] = new_rel
            file_state["renamed_from"] = rel
            file_state.pop("conflict", None)
            _save_state(state_path, state)
            if emit_mhl:
                dst_state = state["destinations"][dst_key]
                try:
                    mhl_path = _write_mhl(dst_root, state.get("hash_algo") or DEFAULT_HASH,
                                          op="offload-rename", previous_paths={new_rel: rel})
                    dst_state["mhl_path"] = str(mhl_path)
                    changed = _mhl_changed_vs_history(mhl_path)
                    if changed:
                        raise RuntimeError("mhl history mismatch: {0}".format(
                            ", ".join(p for p, _g in changed[:10])))
                    if verify:
                        rc = _verify_emitted_mhl(dst_root, mhl_path)
                        if rc is not None and rc != 0:
                            raise RuntimeError("mhl verify failed for {0}: exit code {1}".format(mhl_path, rc))
                    dst_state["mhl_error"] = None
                    dst_state["error"] = None
                except Exception as exc:
                    dst_state["mhl_error"] = "{0}: {1}".format(type(exc).__name__, exc)
                    dst_state["error"] = dst_state["mhl_error"]
                _save_state(state_path, state)
        elif file_state.get("status") == "needs_rename":
            # The name was taken between the check and the copy (another tool).
            # Nothing was overwritten; keep the original conflict for the dialog.
            if conflict:
                file_state["conflict"] = conflict
            _save_state(state_path, state)
            raise ValueError("{0} appeared on the destination while copying — pick another name".format(new_rel))
    else:
        raise ValueError("action must be 'rename' or 'skip'")

    summary = {k: _dst_summary_from_state(state, k) for k in state["destinations"]}
    _save_state(state_path, state)
    return {
        "status": overall_status(summary),
        "summary": summary,
        "file": {"rel": rel, "dst": dst_key, "status": file_state.get("status"),
                 "dst_rel": file_state.get("dst_rel"), "error": file_state.get("error")},
    }


# ── card-watcher (DIT wrapper ②) ────────────────────────────────────────────
# `--watch --dst X [--organize T]` waits for a camera card to mount, then offloads
# it automatically (copy + hash-verify + MHL, never deletes the source) with the ①
# naming policy. The engine layer's "insert → it just copies" half of the Gate
# replacement. A card is a freshly-mounted volume with a DCIM/ folder or media files;
# only NEW mounts (vs the baseline at start) trigger, so already-plugged disks don't.
_CARD_MEDIA_EXTS = {
    ".mp4", ".mov", ".m4v", ".mts", ".mxf", ".avi", ".insv", ".360",  # video
    ".braw", ".r3d", ".ari",                                          # cinema raw
    ".jpg", ".jpeg", ".heic", ".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf",  # stills
    ".wav", ".aif", ".aiff",                                          # audio
}


def _looks_like_card(mountpoint):
    """True if a mountpoint looks like a camera card: the universal DCIM/ folder, or
    media files in a shallow scan. Never raises."""
    try:
        if os.path.isdir(os.path.join(mountpoint, "DCIM")):
            return True
        scanned = 0
        for entry in os.scandir(mountpoint):
            if entry.is_file(follow_symlinks=False) and \
               os.path.splitext(entry.name)[1].lower() in _CARD_MEDIA_EXTS:
                return True
            scanned += 1
            if scanned > 200:  # shallow — don't walk a huge disk root
                break
    except OSError:
        return False
    return False


def _media_volumes(_partitions=None):
    """Currently-mounted volumes that look like camera cards. `_partitions` is an
    injection point for tests; defaults to psutil.disk_partitions()."""
    parts = _partitions
    if parts is None:
        try:
            import psutil
            parts = [p.mountpoint for p in psutil.disk_partitions(all=False)]
        except Exception:
            return set()
    out = set()
    for mp in parts:
        # skip the obvious system roots; cards mount under /Volumes (mac), a drive
        # letter (Windows), or /media|/mnt (Linux) — the new-mount diff also guards this
        if not mp or mp in ("/",) or os.path.normpath(mp) == os.path.normpath(os.path.expanduser("~")):
            continue
        if os.path.isdir(mp) and _looks_like_card(mp):
            out.add(os.path.normpath(mp))
    return out


def _all_mounts(_partitions=None):
    """All current mountpoints (no card filter), normpath'd. Test seam via _partitions."""
    parts = _partitions
    if parts is None:
        try:
            import psutil
            parts = [p.mountpoint for p in psutil.disk_partitions(all=False)]
        except Exception:
            return set()
    return {os.path.normpath(p) for p in parts if p}


def run_card_watch(dsts, organize=None, interval=3.0, hash_algo=DEFAULT_HASH,
                   include_heic=False, retry_limit=DEFAULT_RETRY_LIMIT, verify=True,
                   emit_mhl=True, once=False, _loops=None, _list_fn=None, _mounts_fn=None):
    """Poll for newly-appeared camera cards and offload each to ``dsts`` with the ①
    naming policy. Returns the list of (volume, exit_code) handled (for tests).
    Copy-only — never touches the source card. `once`/`_loops`/`_list_fn`/`_mounts_fn`
    are test seams.

    A volume is offloaded once per continuous mount: ``handled`` is pruned by the RAW
    mount set (not the card-detection result), so a transient _looks_like_card() miss
    on a still-plugged card can't re-trigger an offload (Codex). Only an unplug→replug
    (the path leaving the raw mount set) re-arms it."""
    if not dsts:
        raise ValueError("--watch requires at least one --dst")
    if organize:
        _validate_organize_template(organize)
    list_fn = _list_fn or _media_volumes
    mounts_fn = _mounts_fn or _all_mounts
    # Baseline: every volume mounted at startup is ignored — only new inserts trigger.
    handled = set(mounts_fn())
    print("Watching for camera cards… dst={0} organize={1} (already-mounted ignored)".format(
        list(dsts), organize or "mirror"))
    results = []
    i = 0
    while True:
        try:
            raw = set(mounts_fn())
            cards = set(list_fn())
        except Exception as exc:
            print("[card-watch] volume scan error: {0}".format(exc))
            raw, cards = set(), set()
        if not raw:
            # A real system always has at least one mount ("/"), so an empty raw set
            # means the probe transiently failed (or raised above). Treat it as "no
            # information": keep `handled` intact and skip this cycle, rather than
            # pruning everything and re-offloading still-mounted cards (Codex).
            i += 1
            if once or (_loops is not None and i >= _loops):
                break
            time.sleep(interval)
            continue
        handled &= raw  # forget unmounted volumes → a replug re-arms; a flicker does not
        for vol in sorted(cards - handled):
            print("\n[card-watch] detected card: {0} → offloading (copy + verify + MHL)".format(vol))
            handled.add(vol)  # mark before running so a slow offload can't double-fire
            try:
                code, _summary, _sp = run_offload(
                    vol, dsts, hash_algo=hash_algo, include_heic=include_heic,
                    retry_limit=retry_limit, verify=verify, emit_mhl=emit_mhl,
                    organize=organize)
                print("[card-watch] {0} done (exit {1})".format(vol, code))
                results.append((vol, code))
            except Exception as exc:
                print("[card-watch] offload failed for {0}: {1}".format(vol, exc))
                results.append((vol, 1))
        i += 1
        if once or (_loops is not None and i >= _loops):
            break
        time.sleep(interval)
    return results


def build_parser():
    parser = argparse.ArgumentParser(description="arkiv offload")
    parser.add_argument("--src", default=None, help="Source card / directory (required unless --watch)")
    parser.add_argument("--dst", action="append", required=True)
    parser.add_argument("--watch", action="store_true",
                        help="DIT wrapper ②: wait for a camera card to mount, then auto-offload it "
                             "(copy + verify + MHL, never deletes the source) with --organize. "
                             "Already-mounted volumes are ignored; only new inserts trigger.")
    parser.add_argument("--interval", type=float, default=3.0, help="--watch poll seconds (default 3)")
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
    if args.watch:
        try:
            run_card_watch(
                args.dst, organize=args.organize, interval=args.interval,
                hash_algo=args.hash_algo, include_heic=args.include_heic,
                retry_limit=args.retry_limit, verify=not args.no_verify,
                emit_mhl=not args.no_mhl)
        except ValueError as exc:
            print(str(exc))
            return 4
        except KeyboardInterrupt:
            print("\n[card-watch] stopped")
        return 0
    if not args.src:
        print("--src is required (or use --watch)")
        return 4
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
    if args.progress == "json":
        # single line so the stream's line reader parses it as the terminal event
        print(json.dumps({"type": "done", "code": code, "state": str(state_path), "summary": summary,
                          "status": overall_status(summary),
                          "needs_rename": [item for s in summary.values()
                                           for item in (s.get("needs_rename") or [])]},
                         ensure_ascii=False), flush=True)
    else:
        print(json.dumps({"state": str(state_path), "summary": summary}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
