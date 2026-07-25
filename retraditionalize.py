"""Phase 9.8b BACKFILL — retro-convert existing Simplified zh transcripts to Taiwan
Traditional so the pre-9.8b NAS 5yr+ backlog stops missing a 記憶體 query on a
內存-indexed clip. The 9.8b write-path (transcribe.py → zh_convert) only converts NEW
transcribes; every row transcribed before it stayed Simplified.

Run via `python ingest.py --retraditionalize [--dry-run]`, then `python embed.py
--rebuild` to re-index the semantic store on the now-Traditional text.

SAFETY (the reason this is gated, learned 2026-07-18): running whole-row s2twp over
the library CORRUPTS already-Traditional text. opencc's Simplified→Traditional phrase
maps assume Simplified input, so on valid Traditional they re-segment wrongly
(系統→係統, 音樂類型→型別, 設備→裝置, 只是→隻是) — and even neutral s2t carries a phrase
layer that does this. So we GATE on zh_convert.classify_zh: ONLY genuine
Mainland-Simplified rows (a Simplified char AND no Traditional-only char) reach the
converter, which is the exact write-path treatment. already-Traditional and mixed rows
are SKIPPED and COUNTED (non-silent) — never fed to the phrase layer, never corrupted.

Idempotent by construction: a converted row is Traditional afterward, so a re-run
classifies it "traditional" and skips it. Timing-safe: zh_convert copies every
start/end/score verbatim, so an idiom that changes a token's length can't shift a
timestamp.
"""
import json

import db
import zh_convert


def _reserialize(original, converted_list):
    """Re-dump a segments/words list, preserving the original NULL/empty sentinel
    when there was no data to convert (don't turn a NULL column into '[]')."""
    if original in (None, "", "null"):
        return original
    return json.dumps(converted_list, ensure_ascii=False)


def convert_row(transcript, lang, segments_json, words_json, converter):
    """Convert one row's text columns through `converter` (convert_result for genuine
    Simplified rows → s2twp idioms; convert_result_charwise for mixed rows → char-safe,
    no idioms). Returns (new_transcript, new_segments_json, new_words_json, changed).
    Malformed JSON in a column degrades to an empty list (that column simply isn't
    converted) rather than raising — a bad legacy blob must not abort the whole
    backfill."""
    try:
        segments = json.loads(segments_json) if segments_json else []
    except (ValueError, TypeError):
        segments = []
    try:
        words = json.loads(words_json) if words_json else []
    except (ValueError, TypeError):
        words = []
    new_t, _lang, new_segs, new_words = converter(transcript, lang, segments, words)
    new_sj = _reserialize(segments_json, new_segs)
    new_wj = _reserialize(words_json, new_words)
    changed = (
        new_t != transcript or new_sj != segments_json or new_wj != words_json
    )
    return new_t, new_sj, new_wj, changed


# classification → (converter, count-bucket). "traditional"/"empty" aren't here: they
# are skipped, never converted.
_CONVERTERS = {
    "simplified": (zh_convert.convert_result, "media_converted"),          # s2twp + idioms
    "mixed": (zh_convert.convert_result_charwise, "media_converted_mixed"),  # char-safe, no idioms
}


def _new_counts():
    return {
        "media_scanned": 0,
        "media_converted": 0,        # genuine Simplified rows, full s2twp idioms
        "media_converted_mixed": 0,  # mixed rows, char-level safe (no idioms)
        "media_skip_traditional": 0,
        "media_skip_empty": 0,
        "archive_scanned": 0,
        "archive_converted": 0,
    }


def backfill(dry_run=False):
    """Convert every qualifying (genuine-Simplified) zh row in both the ACTIVE media
    columns and the per-language transcript archive, inside ONE transaction (atomic —
    a mid-run error rolls the whole thing back). dry_run performs no writes and reports
    the same counts, so `--dry-run` previews exactly what a real run would touch.
    Returns a counts dict."""
    counts = _new_counts()
    with db.get_conn() as conn:
        for row in db.iter_zh_media(_conn=conn):
            counts["media_scanned"] += 1
            cls = zh_convert.classify_zh(row["transcript"])
            route = _CONVERTERS.get(cls)
            if route is None:  # traditional / empty → never fed to any converter
                counts["media_skip_" + cls] += 1
                continue
            converter, bucket = route
            new_t, new_sj, new_wj, changed = convert_row(
                row["transcript"], row["lang"], row["segments_json"], row["words_json"], converter
            )
            if not changed:
                # classified for conversion but the converter was a no-op (opencc missing
                # → identity). Count as a traditional-style skip, never a conversion.
                counts["media_skip_traditional"] += 1
                continue
            counts[bucket] += 1
            if not dry_run:
                db.update_media_transcript_fields(
                    row["id"], new_t, new_sj, new_wj, _conn=conn
                )

        for row in db.iter_zh_transcript_archive(_conn=conn):
            counts["archive_scanned"] += 1
            route = _CONVERTERS.get(zh_convert.classify_zh(row["transcript"]))
            if route is None:
                continue
            converter, _bucket = route
            new_t, new_sj, new_wj, changed = convert_row(
                row["transcript"], row["lang"], row["segments_json"], row["words_json"], converter
            )
            if changed:
                counts["archive_converted"] += 1
                if not dry_run:
                    db.upsert_transcript(
                        row["media_id"], row["lang"], new_t, new_sj, new_wj, _conn=conn
                    )

        if dry_run:
            # No write helpers were called, so nothing is pending; roll back anyway to
            # make "this transaction changes nothing" explicit and defensive.
            conn.rollback()
    return counts


def format_summary(counts, dry_run=False):
    """Human-readable one-block summary for the CLI."""
    head = "Retraditionalize (Phase 9.8b backfill)" + (" — DRY RUN (no writes)" if dry_run else "")
    verb = "would convert" if dry_run else "converted"
    return (
        f"{head}\n"
        f"  media: {counts['media_scanned']} zh scanned, {verb} "
        f"{counts['media_converted']} Simplified (s2twp idioms) + "
        f"{counts['media_converted_mixed']} mixed (char-safe, no idioms)\n"
        f"         skipped {counts['media_skip_traditional']} already-Traditional, "
        f"{counts['media_skip_empty']} empty\n"
        f"  archive: {counts['archive_scanned']} zh scanned, {verb} {counts['archive_converted']}\n"
        + ("" if dry_run else "  → run `python embed.py --rebuild` to re-index the semantic store.")
    )
