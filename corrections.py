"""Per-project correction dictionary — one dictionary, two application paths.

A correction rule (``{from, to, scope, pre, post}``) lives in
``PROJECT_ROOT/.arkiv/corrections.json`` (beside ``vocabulary.txt``). One
dictionary feeds two paths so a name like 富田→Furutech is maintained once:

  pre  — the ``to`` term enters the Whisper hotword list (``initial_prompt``),
         preventing the mis-hearing in *future* transcriptions. This is the
         FatSub "專有名詞" path; arkiv already hot-reloads ``vocabulary.txt``,
         so ``hotword_terms()`` just merges the dictionary's pre-terms in.

  post — ``from``→``to`` is applied to *already-stored* transcripts (batch
         recorrect). Fixes the entire NAS backlog's search recall in seconds
         without touching audio — the cheap, default, arkiv-native path
         (Phase 9.6b). Stronger than FatSub's search-replace, which only
         auto-applies to future clips; this rewrites the past library.

Why one dictionary instead of FatSub's two unrelated lists: a proper-noun fix
usually wants BOTH — feed the hotword AND repair the old clips. One row, two
switches (``pre`` / ``post``).

RP-4 discipline (no silent destruction): batch recorrect is preview-first
(``scan()`` lists every hit, writes nothing) and reversible (``apply()`` writes
a timestamped backup of the pre-correction transcript/segments/words before any
UPDATE; ``revert()`` restores it exactly).

Scope semantics (per rule, default ``global``):
  global — substring replace anywhere. The workhorse for distinctive multi-char
           renames (富田→Furutech). The dry-run preview is the safety net.
  word   — standalone occurrence only: the match must NOT be flanked by another
           CJK ideograph or word char on either side. The guard for short /
           ambiguous terms so a rule never bleeds into a longer word
           (e.g. a 松→鬆 rule must not touch 馬拉松 — the tag-dedup red line).
  line   — segment/line-initial only (after optional leading whitespace).

All operations target the ACTIVE project (``config.PROJECT_ROOT`` /
``db.get_conn()``), matching every other arkiv endpoint. Batch-targeting an
arbitrary project DB is deferred (no such entry point exists anywhere yet).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import config

SCOPES = ("global", "word", "line")

# A "word"/"line" boundary char: another CJK ideograph or an ASCII word char.
# A match flanked by one of these is part of a longer token, so word-scope skips
# it. CJK range covers the common ideograph block qwen/Whisper emit.
_BOUNDARY = r"\w一-鿿"


def corrections_path() -> Path:
    """``PROJECT_ROOT/.arkiv/corrections.json`` for the active project."""
    return Path(config.PROJECT_ROOT) / ".arkiv" / "corrections.json"


def _backups_dir() -> Path:
    return Path(config.PROJECT_ROOT) / ".arkiv" / "corrections-backups"


# ── rule load / save / validate ──────────────────────────────────────────────

def _clean_rule(raw: Dict) -> Optional[Dict]:
    """Validate + normalize one raw rule. Returns None if unusable.

    A rule needs a non-empty ``from``; ``to`` may be empty (a deletion). ``scope``
    falls back to ``global``; ``pre`` defaults off (don't silently grow the
    hotword list), ``post`` defaults on (recorrect is the rule's main job).
    """
    if not isinstance(raw, dict):
        return None
    frm = raw.get("from")
    to = raw.get("to", "")
    if not isinstance(frm, str) or not frm:
        return None
    if not isinstance(to, str):
        return None
    scope = raw.get("scope", "global")
    if scope not in SCOPES:
        scope = "global"
    return {
        "from": frm,
        "to": to,
        "scope": scope,
        "pre": bool(raw.get("pre", False)),
        "post": bool(raw.get("post", True)),
    }


def load_rules() -> List[Dict]:
    """Read + validate rules from ``corrections.json``. Missing / corrupt → []
    (non-fatal, matching ``vocabulary.txt`` tolerance)."""
    path = corrections_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw_rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(raw_rules, list):
        return []
    out = []
    for raw in raw_rules:
        rule = _clean_rule(raw)
        if rule is not None:
            out.append(rule)
    return out


def save_rules(rules: Iterable[Dict]) -> List[Dict]:
    """Validate + atomically write rules. Returns the cleaned list that was
    persisted (caller can echo it back)."""
    cleaned = [r for r in (_clean_rule(x) for x in rules) if r is not None]
    path = corrections_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"version": 1, "rules": cleaned}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)  # atomic on POSIX + Windows
    return cleaned


def hotword_terms() -> List[str]:
    """The ``to`` terms of rules with ``pre=True`` — merged into the Whisper
    hotword list by ``transcribe._custom_terms()``. De-duplicated, order kept."""
    seen = set()
    out = []
    for r in load_rules():
        if r.get("pre") and r["to"] and r["to"] not in seen:
            seen.add(r["to"])
            out.append(r["to"])
    return out


# ── correction application (pure, testable) ──────────────────────────────────

def _apply_rule(text: str, rule: Dict) -> Tuple[str, int]:
    """Apply one rule to one text blob. Returns ``(new_text, n_hits)``."""
    frm, to, scope = rule["from"], rule["to"], rule.get("scope", "global")
    if not text or not frm or frm == to:
        return text, 0
    if scope == "global":
        n = text.count(frm)
        return (text.replace(frm, to), n) if n else (text, 0)
    if scope == "word":
        pat = re.compile(
            r"(?<![" + _BOUNDARY + r"])" + re.escape(frm) + r"(?![" + _BOUNDARY + r"])"
        )
    else:  # line — segment/line-initial, after optional leading whitespace
        pat = re.compile(r"(?m)^([ \t]*)" + re.escape(frm))
    matches = pat.findall(text)
    if not matches:
        return text, 0
    if scope == "line":
        new = pat.sub(lambda m: m.group(1) + to, text)
    else:
        new = pat.sub(to, text)
    return new, len(matches)


def apply_rules_to_text(text: str, rules: Iterable[Dict]) -> Tuple[str, int]:
    """Apply every (post) rule in order. Returns ``(new_text, total_hits)``."""
    total = 0
    for rule in rules:
        text, n = _apply_rule(text, rule)
        total += n
    return text, total


def _correct_segments(segments_json: Optional[str], rules: List[Dict]) -> Tuple[Optional[str], int]:
    """Apply rules to each segment's ``text`` field, preserving timestamps.
    Returns ``(new_json_or_unchanged, hits)``. Malformed JSON is left untouched."""
    if not segments_json:
        return segments_json, 0
    try:
        segs = json.loads(segments_json)
    except ValueError:
        return segments_json, 0
    if not isinstance(segs, list):
        return segments_json, 0
    hits = 0
    changed = False
    for seg in segs:
        if isinstance(seg, dict) and isinstance(seg.get("text"), str):
            new_text, n = apply_rules_to_text(seg["text"], rules)
            if n:
                seg["text"] = new_text
                hits += n
                changed = True
    if not changed:
        return segments_json, 0
    return json.dumps(segs, ensure_ascii=False), hits


def _correct_words(words_json: Optional[str], rules: List[Dict]) -> Tuple[Optional[str], int]:
    """Whole-token rename in ``words_json`` only — replace a word token that
    EXACTLY equals a rule's ``from`` (after strip). Multi-token names (富田 split
    across tokens) are deliberately left as-is: the handoff blesses words_json as
    "近似可接受", and a partial token rewrite would corrupt the timing array.
    Search/SRT use transcript+segments, not words, so this is cosmetic."""
    if not words_json:
        return words_json, 0
    try:
        words = json.loads(words_json)
    except ValueError:
        return words_json, 0
    if not isinstance(words, list):
        return words_json, 0
    # exact whole-token map from rules (later rule wins, matching ordered apply)
    swap = {}
    for r in rules:
        swap[r["from"]] = r["to"]
    hits = 0
    changed = False
    for w in words:
        if isinstance(w, dict) and isinstance(w.get("word"), str):
            key = w["word"].strip()
            if key in swap and swap[key] != w["word"].strip():
                # preserve surrounding whitespace the tokenizer kept
                w["word"] = w["word"].replace(key, swap[key])
                hits += 1
                changed = True
    if not changed:
        return words_json, 0
    return json.dumps(words, ensure_ascii=False), hits


# ── DB-facing scan / apply / revert ──────────────────────────────────────────

def _iter_transcribed():
    """Yield ``(id, filename, transcript, segments_json, words_json)`` for every
    media row with a non-empty transcript in the active project DB."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, transcript, segments_json, words_json "
            "FROM media WHERE transcript IS NOT NULL AND transcript != ''"
        ).fetchall()
    for row in rows:
        yield row


def scan(rules: Optional[List[Dict]] = None) -> Dict:
    """Dry-run preview. Counts hits per rule and per media WITHOUT writing.
    Hit count is measured on the transcript blob (what search matches on)."""
    rules = [r for r in (rules if rules is not None else load_rules()) if r.get("post")]
    per_rule = {i: 0 for i in range(len(rules))}
    affected = []  # [{id, filename, hits, samples:[{rule, before, after}]}]
    for row in _iter_transcribed():
        transcript = row["transcript"] or ""
        media_hits = 0
        samples = []
        for i, rule in enumerate(rules):
            _, n = _apply_rule(transcript, rule)
            if n:
                per_rule[i] += n
                media_hits += n
                samples.append({"from": rule["from"], "to": rule["to"], "count": n})
        if media_hits:
            affected.append({
                "id": row["id"],
                "filename": row["filename"],
                "hits": media_hits,
                "rules": samples,
            })
    return {
        "rules": [
            {"from": rules[i]["from"], "to": rules[i]["to"],
             "scope": rules[i]["scope"], "hits": per_rule[i]}
            for i in range(len(rules))
        ],
        "media_affected": len(affected),
        "total_hits": sum(per_rule.values()),
        "affected": affected,
    }


def _write_backup(rows: List[Dict], rules: List[Dict]) -> str:
    """Persist pre-correction state of the affected rows. Returns backup name."""
    import time
    name = "recorrect-{0}.json".format(time.strftime("%Y%m%dT%H%M%S", time.gmtime()))
    bdir = _backups_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rules": rules,
        "media": rows,
    }
    path = bdir / name
    # if two applies land in the same second, suffix to avoid clobbering a backup
    n = 1
    while path.exists():
        path = bdir / name.replace(".json", "-{0}.json".format(n))
        n += 1
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.name


def apply(rules: Optional[List[Dict]] = None) -> Dict:
    """Apply post-rules to transcript + segments_json (+ whole-token words_json)
    for every affected media, in ONE transaction. Writes a backup of the
    pre-correction state first (RP-4 reversibility). Returns a summary including
    the backup name to feed ``revert()``."""
    import db
    rules = [r for r in (rules if rules is not None else load_rules()) if r.get("post")]
    if not rules:
        return {"media_updated": 0, "total_hits": 0, "backup": None, "rule_count": 0}

    backup_rows = []   # pre-correction snapshot
    updates = []       # (id, new_transcript, new_segments, new_words)
    total_hits = 0
    for row in _iter_transcribed():
        new_t, n_t = apply_rules_to_text(row["transcript"] or "", rules)
        new_s, n_s = _correct_segments(row["segments_json"], rules)
        new_w, _n_w = _correct_words(row["words_json"], rules)
        if n_t or n_s:  # transcript or segments changed → this row is affected
            backup_rows.append({
                "id": row["id"],
                "transcript": row["transcript"],
                "segments_json": row["segments_json"],
                "words_json": row["words_json"],
            })
            updates.append((row["id"], new_t, new_s, new_w))
            total_hits += n_t

    if not updates:
        return {"media_updated": 0, "total_hits": 0, "backup": None,
                "rule_count": len(rules)}

    backup_name = _write_backup(backup_rows, rules)
    with db.get_conn() as conn:
        conn.executemany(
            "UPDATE media SET transcript=?, segments_json=?, words_json=? WHERE id=?",
            [(t, s, w, mid) for (mid, t, s, w) in updates],
        )
    return {
        "media_updated": len(updates),
        "total_hits": total_hits,
        "backup": backup_name,
        "rule_count": len(rules),
    }


def list_backups() -> List[str]:
    """Backup names, newest first."""
    bdir = _backups_dir()
    if not bdir.exists():
        return []
    return sorted((p.name for p in bdir.glob("recorrect-*.json")), reverse=True)


def revert(backup_name: Optional[str] = None) -> Dict:
    """Restore transcript/segments/words from a backup (latest if unspecified).
    Exact inverse of the UPDATE ``apply()`` performed."""
    import db
    backups = list_backups()
    if not backups:
        return {"restored": 0, "backup": None, "error": "no backups"}
    name = backup_name or backups[0]
    path = _backups_dir() / name
    if not path.exists() or name not in backups:
        return {"restored": 0, "backup": None, "error": "backup not found"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"restored": 0, "backup": name, "error": "backup unreadable"}
    media = payload.get("media", [])
    rows = [
        (m.get("transcript"), m.get("segments_json"), m.get("words_json"), m["id"])
        for m in media if isinstance(m, dict) and "id" in m
    ]
    if rows:
        with db.get_conn() as conn:
            conn.executemany(
                "UPDATE media SET transcript=?, segments_json=?, words_json=? WHERE id=?",
                rows,
            )
    return {"restored": len(rows), "backup": name}
