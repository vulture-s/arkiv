"""Phase 9.8b BACKFILL — retro-convert existing Simplified zh transcripts.

The load-bearing test is `test_already_traditional_row_is_never_corrupted`: whole-row
s2twp re-segments valid Traditional text (系統→係統, 音樂類型→型別, 設備→裝置), so the
backfill MUST gate on zh_convert.classify_zh and leave already-Traditional rows byte-for-
byte untouched. The rest verify genuine Simplified rows get the full write-path treatment
(idioms everywhere, timings verbatim), the archive table is converted too, dry-run writes
nothing, and a second run is a no-op (idempotent)."""
import importlib
import json

import pytest

zh = importlib.import_module("zh_convert")
db = importlib.import_module("db")
retrad = importlib.import_module("retraditionalize")

_HAVE_OPENCC = zh._converter("s2t") is not None
_skip_no_opencc = pytest.mark.skipif(not _HAVE_OPENCC, reason="opencc not installed")

# id=1 genuine Mainland-Simplified whisper output (no Traditional-only char)
_SIMP_TRANSCRIPT = "我把视频存到内存，用软件打开"
_SIMP_SEGMENTS = [
    {"start": 0.0, "end": 1.5, "text": "我把视频存到内存"},
    {"start": 1.5, "end": 2.8, "text": "用软件打开"},
]
_SIMP_WORDS = [
    {"word": "内存", "start": 0.4, "end": 0.9, "score": 0.91},
    {"word": "软件", "start": 1.6, "end": 2.0, "score": 0.88},
]
# id=2 already-Traditional Taiwan text that s2twp WOULD corrupt if fed to it
_TRAD_TRANSCRIPT = "這個音樂類型的設備系統只是測試"


def _insert_media(conn, mid, lang, transcript, segments=None, words=None):
    conn.execute(
        "INSERT INTO media (id, path, filename, ext, lang, transcript, segments_json, words_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            mid, "/tmp/clip_{0}.mp4".format(mid), "clip_{0}.mp4".format(mid), ".mp4",
            lang, transcript,
            json.dumps(segments, ensure_ascii=False) if segments is not None else None,
            json.dumps(words, ensure_ascii=False) if words is not None else None,
        ),
    )


def _seed(tmp_db):
    with db.get_conn() as conn:
        _insert_media(conn, 1, "zh", _SIMP_TRANSCRIPT, _SIMP_SEGMENTS, _SIMP_WORDS)
        _insert_media(conn, 2, "zh", _TRAD_TRANSCRIPT)            # already Traditional
        _insert_media(conn, 3, "zh", "这是繁體字混合的視頻")        # mixed (simp+trad)
        _insert_media(conn, 4, "en", "this is memory software")   # non-zh
    # archive: a Simplified zh version for media 1
    db.upsert_transcript(1, "zh", _SIMP_TRANSCRIPT,
                         json.dumps(_SIMP_SEGMENTS, ensure_ascii=False),
                         json.dumps(_SIMP_WORDS, ensure_ascii=False))


# ── classifier ───────────────────────────────────────────────────────────────
@_skip_no_opencc
def test_classify_zh_four_buckets():
    assert zh.classify_zh(_SIMP_TRANSCRIPT) == "simplified"
    assert zh.classify_zh(_TRAD_TRANSCRIPT) == "traditional"
    assert zh.classify_zh("这是繁體字混合的視頻") == "mixed"
    assert zh.classify_zh("   ") == "empty"


# ── genuine Simplified row gets the full write-path treatment ─────────────────
@_skip_no_opencc
def test_simplified_row_converted_with_idioms_and_timing_safe(tmp_db):
    _seed(tmp_db)
    counts = retrad.backfill()
    assert counts["media_converted"] == 1          # only id=1
    rec = db.get_record_by_id(1)
    # Taiwan idioms across transcript
    assert "記憶體" in rec["transcript"] and "軟體" in rec["transcript"] and "影片" in rec["transcript"]
    assert "内存" not in rec["transcript"] and "视频" not in rec["transcript"]
    segs = json.loads(rec["segments_json"])
    assert "記憶體" in segs[0]["text"]              # segment idioms
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 1.5   # timings verbatim
    words = json.loads(rec["words_json"])
    assert words[0]["word"] == "記憶體"             # word token idioms (內存→記憶體)
    assert words[0]["start"] == 0.4 and words[0]["end"] == 0.9 and words[0]["score"] == 0.91


# ── THE safety test: already-Traditional text is never corrupted ──────────────
@_skip_no_opencc
def test_already_traditional_row_is_never_corrupted(tmp_db):
    _seed(tmp_db)
    retrad.backfill()
    rec = db.get_record_by_id(2)
    assert rec["transcript"] == _TRAD_TRANSCRIPT     # byte-for-byte unchanged
    # the specific s2twp mis-conversions that a naive whole-row run would introduce
    for corruption in ("型別", "裝置", "係統", "隻是"):
        assert corruption not in rec["transcript"]


@_skip_no_opencc
def test_charwise_is_corruption_free_and_carries_no_idioms():
    # already-Traditional bait: char-wise must be exact identity (no re-segmentation)
    assert zh.to_traditional_charwise(_TRAD_TRANSCRIPT) == _TRAD_TRANSCRIPT
    # Simplified chars fixed, but NO phrase idioms (软件→軟件, never 軟體; 内存→內存, never 記憶體)
    assert zh.to_traditional_charwise("内存软件") == "內存軟件"


@_skip_no_opencc
def test_convert_result_charwise_timing_safe():
    segs = [{"start": 0.0, "end": 1.0, "text": "内存"}]
    words = [{"word": "软件", "start": 0.0, "end": 0.5, "score": 0.9}]
    t, _l, s, w = zh.convert_result_charwise("内存软件", "zh", segs, words)
    assert t == "內存軟件"
    assert s[0]["text"] == "內存" and s[0]["start"] == 0.0 and s[0]["end"] == 1.0
    assert w[0]["word"] == "軟件" and w[0]["start"] == 0.0 and w[0]["score"] == 0.9


@_skip_no_opencc
def test_mixed_row_converted_char_safe_no_idioms(tmp_db):
    _seed(tmp_db)
    counts = retrad.backfill()
    assert counts["media_converted_mixed"] == 1
    rec = db.get_record_by_id(3)["transcript"]
    assert rec == "這是繁體字混合的視頻"        # 这→這 fixed, everything else byte-identical
    assert "影片" not in rec                     # mixed gets NO idioms (視頻 stays 視頻)
    assert db.get_record_by_id(4)["transcript"] == "this is memory software"  # en never scanned


@_skip_no_opencc
def test_archive_table_is_converted(tmp_db):
    _seed(tmp_db)
    counts = retrad.backfill()
    assert counts["archive_converted"] == 1
    arch = db.get_transcript(1, "zh")
    assert "記憶體" in arch["transcript"]


@_skip_no_opencc
def test_dry_run_writes_nothing(tmp_db):
    _seed(tmp_db)
    counts = retrad.backfill(dry_run=True)
    assert counts["media_converted"] == 1            # reports what WOULD convert
    assert db.get_record_by_id(1)["transcript"] == _SIMP_TRANSCRIPT   # but nothing written


@_skip_no_opencc
def test_idempotent_second_run_is_noop(tmp_db):
    _seed(tmp_db)
    retrad.backfill()
    snap = {i: db.get_record_by_id(i)["transcript"] for i in (1, 3)}   # simplified + mixed
    counts2 = retrad.backfill()
    assert counts2["media_converted"] == 0 and counts2["media_converted_mixed"] == 0
    for i in (1, 3):
        assert db.get_record_by_id(i)["transcript"] == snap[i]         # both stable


@_skip_no_opencc
def test_null_segments_stay_null(tmp_db):
    """A Simplified media row with NULL segments/words must not gain a '[]' blob."""
    with db.get_conn() as conn:
        _insert_media(conn, 10, "zh", _SIMP_TRANSCRIPT)   # no segments/words
    retrad.backfill()
    rec = db.get_record_by_id(10)
    assert "記憶體" in rec["transcript"]
    assert rec["segments_json"] is None and rec["words_json"] is None
