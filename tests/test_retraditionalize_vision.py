"""Vision retraditionalize backfill — retro-convert Simplified qwen3-vl frame
descriptions (frames.description + media.frame_tags) to Taiwan Traditional. The vision
write-path (vision.py) historically never routed through zh_convert, so descriptions were
stored raw Simplified (audit 2026-07-30: 175 frames / 159 clips). Gates on classify_zh
with the SAME safety as the transcript backfill: already-Traditional descriptions must
never be corrupted by the s2twp phrase layer; non-zh (English) descriptions are skipped."""
import importlib
import json

import pytest

zh = importlib.import_module("zh_convert")
db = importlib.import_module("db")
retrad = importlib.import_module("retraditionalize")

_HAVE_OPENCC = zh._converter("s2t") is not None
_skip_no_opencc = pytest.mark.skipif(not _HAVE_OPENCC, reason="opencc not installed")

_SIMP_DESC = "一只手正在整理黑色的电缆线，这些电缆插在一台白色设备上"   # genuine Simplified
_TRAD_DESC = "這個音樂類型的設備系統只是測試"                        # already-Traditional (s2twp bait)
_MIXED_DESC = "这是繁體字混合的畫面"                                # mixed (simp 这 + trad-only 體/畫)
_EN_DESC = "a hand organizing black cables on a white device"       # non-zh


def _insert_media(conn, mid, frame_tags=None):
    conn.execute(
        "INSERT INTO media (id, path, filename, ext, frame_tags) VALUES (?,?,?,?,?)",
        (mid, "/tmp/clip_{0}.mp4".format(mid), "clip_{0}.mp4".format(mid), ".mp4",
         json.dumps(frame_tags, ensure_ascii=False) if frame_tags is not None else None),
    )


def _insert_frame(conn, fid, mid, idx, description):
    conn.execute(
        "INSERT INTO frames (id, media_id, frame_index, timestamp_s, description) "
        "VALUES (?,?,?,?,?)",
        (fid, mid, idx, float(idx), description),
    )


def _frame_desc(fid):
    with db.get_conn() as conn:
        return conn.execute("SELECT description FROM frames WHERE id=?", (fid,)).fetchone()[0]


def _seed(tmp_db):
    # media 1's frame_tags rollup blob mixes a Simplified frame + an already-Traditional
    # frame — the blob writer must convert the first and leave the second byte-identical.
    ft_blob = [
        {"description": _SIMP_DESC, "tags": ["电缆"]},
        {"description": _TRAD_DESC, "tags": []},
    ]
    with db.get_conn() as conn:
        _insert_media(conn, 1, frame_tags=ft_blob)
        _insert_media(conn, 2)
        _insert_media(conn, 3)
        _insert_media(conn, 4)
        _insert_frame(conn, 101, 1, 0, _SIMP_DESC)    # simplified → convert
        _insert_frame(conn, 102, 2, 0, _TRAD_DESC)    # already-Traditional → untouched
        _insert_frame(conn, 103, 3, 0, _MIXED_DESC)   # mixed → char-safe
        _insert_frame(conn, 104, 4, 0, _EN_DESC)      # english → skipped


@_skip_no_opencc
def test_simplified_frame_description_converted(tmp_db):
    _seed(tmp_db)
    counts = retrad.backfill()
    assert counts["frames_converted"] == 2               # id 101 (simplified) + 103 (mixed)
    f101 = _frame_desc(101)
    assert "電纜" in f101                                 # s2twp: 电缆→電纜 (设备→裝置, 台→臺)
    assert zh.classify_zh(f101) == "traditional"          # no Simplified residue at all
    assert "设备" not in f101 and "电缆" not in f101


@_skip_no_opencc
def test_already_traditional_frame_never_corrupted(tmp_db):
    _seed(tmp_db)
    retrad.backfill()
    assert _frame_desc(102) == _TRAD_DESC                # byte-for-byte unchanged
    for corruption in ("型別", "裝置", "係統", "隻是"):
        assert corruption not in _frame_desc(102)


@_skip_no_opencc
def test_mixed_frame_char_safe_no_idioms(tmp_db):
    _seed(tmp_db)
    retrad.backfill()
    assert _frame_desc(103) == "這是繁體字混合的畫面"     # 这→這 only, rest byte-identical


@_skip_no_opencc
def test_english_frame_description_skipped(tmp_db):
    _seed(tmp_db)
    retrad.backfill()
    assert _frame_desc(104) == _EN_DESC                  # non-zh never converted


@_skip_no_opencc
def test_frame_tags_blob_converted(tmp_db):
    _seed(tmp_db)
    counts = retrad.backfill()
    assert counts["frame_tags_media_converted"] == 1
    blob = json.loads(db.get_record_by_id(1)["frame_tags"])
    assert zh.classify_zh(blob[0]["description"]) == "traditional"  # Simplified frame converted
    assert "電纜" in blob[0]["description"]
    assert blob[1]["description"] == _TRAD_DESC          # Traditional frame in blob untouched


@_skip_no_opencc
def test_dry_run_writes_nothing(tmp_db):
    _seed(tmp_db)
    counts = retrad.backfill(dry_run=True)
    assert counts["frames_converted"] == 2               # reports what WOULD convert
    assert _frame_desc(101) == _SIMP_DESC                # but nothing written


@_skip_no_opencc
def test_idempotent_second_run_is_noop(tmp_db):
    _seed(tmp_db)
    retrad.backfill()
    snap = {i: _frame_desc(i) for i in (101, 103)}
    counts2 = retrad.backfill()
    assert counts2["frames_converted"] == 0 and counts2["frame_tags_media_converted"] == 0
    for i in (101, 103):
        assert _frame_desc(i) == snap[i]
