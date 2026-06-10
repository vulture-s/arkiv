"""Phase 12 — export.py corpus/JSONL CLI tests (real SQLite via tmp_db)."""
import importlib
import json

import pytest

import db


@pytest.fixture
def ex(tmp_db):
    export = importlib.import_module("export")
    return importlib.reload(export)


def _seed(sample_record):
    # 2 zh + 1 en, plus one zh with an empty transcript.
    db.upsert(sample_record(path="/m/a.mp4", filename="a.mp4", lang="zh",
                            transcript="第一段中文逐字稿。"))
    db.upsert(sample_record(path="/m/b.mp4", filename="b.mp4", lang="zh",
                            transcript="第二段中文逐字稿。"))
    db.upsert(sample_record(path="/m/c.mp4", filename="c.mp4", lang="en",
                            transcript="English transcript here."))
    db.upsert(sample_record(path="/m/d.mp4", filename="d.mp4", lang="zh",
                            transcript=""))


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------
def test_corpus_merges_transcripts_blank_line_separated(ex, sample_record):
    _seed(sample_record)
    corpus = ex.build_corpus()
    assert "第一段中文逐字稿。" in corpus
    assert "English transcript here." in corpus
    assert "\n\n" in corpus  # blank-line separated


def test_corpus_lang_filter(ex, sample_record):
    _seed(sample_record)
    zh = ex.build_corpus(lang="zh")
    assert "第一段中文逐字稿。" in zh
    assert "English transcript here." not in zh


def test_corpus_is_plain_text_no_json_residue(ex, sample_record):
    # Acceptance anchor: `corpus` carries transcript text only — none of the
    # exporter's JSON structure (frame_tags / metadata) leaks into it. Note a
    # transcript may legitimately CONTAIN brace/JSON-looking spoken text; that
    # is preserved verbatim and is not "residue" (Codex SHOULD-FIX).
    db.upsert(sample_record(path="/m/j.mp4", lang="zh",
                            transcript='他說 {"title":"片名"}\n下一行還在同一段。'))
    corpus = ex.build_corpus(lang="zh")
    # JSON-looking spoken text survives untouched...
    assert '{"title":"片名"}' in corpus
    assert "下一行還在同一段。" in corpus
    # ...but the exporter never dumps its own metadata structure into corpus.
    for leaked in ("frame_tags", "frame_descriptions", '"metadata"', '"filename"'):
        assert leaked not in corpus
    # the corpus as a whole is plain text, not a JSON document
    with pytest.raises(ValueError):
        json.loads(corpus)


def test_corpus_skips_empty_transcripts(ex, sample_record):
    _seed(sample_record)
    zh = ex.build_corpus(lang="zh")
    # d.mp4 has empty transcript -> only the 2 non-empty zh clips
    assert zh.count("\n\n") == 1  # 2 parts -> exactly one separator


# --------------------------------------------------------------------------
# jsonl
# --------------------------------------------------------------------------
def test_jsonl_each_line_valid_json_with_id_text_metadata(ex, sample_record):
    # Acceptance anchor: every line is valid JSON carrying id / text / metadata.
    _seed(sample_record)
    lines = ex.build_jsonl_lines()
    assert len(lines) == 3  # empty-transcript clip excluded
    for line in lines:
        obj = json.loads(line)  # must not raise
        assert "id" in obj and "text" in obj and "metadata" in obj
        assert obj["text"]
        assert "tags" in obj["metadata"] and "frame_descriptions" in obj["metadata"]


def test_jsonl_lang_filter_and_skip_empty(ex, sample_record):
    _seed(sample_record)
    zh = ex.build_jsonl_lines(lang="zh")
    assert len(zh) == 2  # 2 non-empty zh (empty one skipped)
    for line in zh:
        assert json.loads(line)["metadata"]["lang"] == "zh"


def test_jsonl_includes_tags_and_frame_descriptions(ex, sample_record):
    db.upsert(sample_record(path="/m/x.mp4", filename="x.mp4", lang="zh",
                            transcript="有標籤的片段。"))
    mid = db.get_record_by_id(1)["id"]
    db.add_tag(mid, "海邊", source="manual")
    lines = ex.build_jsonl_lines()
    obj = json.loads(lines[0])
    assert "海邊" in obj["metadata"]["tags"]
    # sample_record's frame_tags carries a description
    assert any("描述" in d for d in obj["metadata"]["frame_descriptions"])


def test_jsonl_ensure_ascii_false_keeps_utf8(ex, sample_record):
    db.upsert(sample_record(path="/m/u.mp4", lang="zh", transcript="中文不要被跳脫。"))
    line = ex.build_jsonl_lines()[0]
    assert "中文不要被跳脫" in line  # raw UTF-8, not \uXXXX


# --------------------------------------------------------------------------
# _frame_descriptions robustness
# --------------------------------------------------------------------------
def test_frame_descriptions_handles_garbage(ex):
    assert ex._frame_descriptions(None) == []
    assert ex._frame_descriptions("") == []
    assert ex._frame_descriptions("<not json>") == []
    assert ex._frame_descriptions('{"not": "a list"}') == []
    assert ex._frame_descriptions('[{"no_desc": 1}]') == []
    assert ex._frame_descriptions('[{"description": "  "}]') == []  # blank stripped


def test_frame_descriptions_extracts(ex):
    val = json.dumps([{"description": "海灘日落"}, {"description": "人物特寫"}])
    assert ex._frame_descriptions(val) == ["海灘日落", "人物特寫"]


# --------------------------------------------------------------------------
# txt + CLI
# --------------------------------------------------------------------------
def test_export_txt_returns_transcript(ex, sample_record):
    db.upsert(sample_record(path="/m/t.mp4", transcript="單支逐字稿。"))
    assert ex.export_txt(1) == "單支逐字稿。"


def test_export_txt_missing_raises(ex):
    with pytest.raises(KeyError):
        ex.export_txt(99999)


def test_cli_corpus_writes_out_file(ex, sample_record, tmp_path):
    _seed(sample_record)
    out = tmp_path / "corpus.txt"
    rc = ex.main(["corpus", "--lang", "zh", "--out", str(out)])
    assert rc == 0
    content = out.read_text(encoding="utf-8")
    assert "第一段中文逐字稿。" in content


def test_cli_jsonl_writes_valid_lines(ex, sample_record, tmp_path):
    _seed(sample_record)
    out = tmp_path / "chunks.jsonl"
    rc = ex.main(["jsonl", "--out", str(out)])
    assert rc == 0
    for line in out.read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_cli_txt_missing_id_returns_1(ex):
    assert ex.main(["txt", "99999"]) == 1


# --------------------------------------------------------------------------
# chapters (ProChapter-style markers from scene frames)
# --------------------------------------------------------------------------
def _seed_with_frames(sample_record):
    db.upsert(sample_record(path="/m/ch.mp4", filename="ch.mp4", lang="zh", duration_s=120.0))
    mid = db.get_record_by_id(1)["id"]
    db.upsert_frame(mid, 0, 0.0, description="開場：店內空景，木質吧台。整體氛圍溫暖。")
    db.upsert_frame(mid, 1, 30.0, description="主廚特寫切肉")
    db.upsert_frame(mid, 2, 75.0, description="")  # no usable description
    return mid


def test_chapters_youtube_format(ex, sample_record):
    _seed_with_frames(sample_record)
    out = ex.build_chapters(1, "youtube")
    lines = out.splitlines()
    assert lines[0].startswith("00:00 ")     # YouTube requires a 0:00 chapter
    assert "01:15" in out                     # 75s -> 1:15
    assert "Chapter 3" in out                 # empty description -> numbered title
    # title = first sentence only (stops at the first 。)
    assert "開場：店內空景，木質吧台。" in out
    assert "整體氛圍溫暖" not in out


def test_chapters_inserts_intro_when_first_frame_not_at_zero(ex, sample_record):
    db.upsert(sample_record(path="/m/i.mp4", duration_s=60.0))
    mid = db.get_record_by_id(1)["id"]
    db.upsert_frame(mid, 0, 12.0, description="第一個畫面")
    out = ex.build_chapters(mid, "youtube")
    assert out.splitlines()[0] == "00:00 Intro"
    assert "00:12 第一個畫面" in out


def test_chapters_ffmetadata_format(ex, sample_record):
    _seed_with_frames(sample_record)
    out = ex.build_chapters(1, "ffmetadata")
    assert out.startswith(";FFMETADATA1")
    assert out.count("[CHAPTER]") == 3
    assert "TIMEBASE=1/1000" in out
    for s in ("START=0", "START=30000", "START=75000"):
        assert s in out
    assert "END=120000" in out               # last chapter ends at duration
    assert "title=主廚特寫切肉" in out


def test_chapters_no_frames_returns_empty(ex, sample_record):
    db.upsert(sample_record(path="/m/nf.mp4", duration_s=10.0))
    assert ex.build_chapters(1, "youtube") == ""


def test_chapters_missing_media_raises(ex):
    with pytest.raises(KeyError):
        ex.build_chapters(99999)


def test_fmt_ts(ex):
    assert ex._fmt_ts(0) == "00:00"
    assert ex._fmt_ts(75) == "01:15"
    assert ex._fmt_ts(3661) == "1:01:01"


def test_cli_chapters_writes_out(ex, sample_record, tmp_path):
    _seed_with_frames(sample_record)
    out = tmp_path / "ch.txt"
    rc = ex.main(["chapters", "1", "--format", "ffmetadata", "--out", str(out)])
    assert rc == 0
    assert ";FFMETADATA1" in out.read_text(encoding="utf-8")


def test_chapters_youtube_drops_sub_10s_markers(ex, sample_record):
    """YouTube ignores chapter lists with <10s gaps — too-close scene markers
    are dropped for youtube (kept for ffmetadata). Codex P3."""
    db.upsert(sample_record(path="/m/g.mp4", duration_s=60.0))
    mid = db.get_record_by_id(1)["id"]
    db.upsert_frame(mid, 0, 0.0, description="A")
    db.upsert_frame(mid, 1, 5.0, description="B")    # 5s after A → dropped
    db.upsert_frame(mid, 2, 30.0, description="C")   # 25s gap → kept
    db.upsert_frame(mid, 3, 35.0, description="D")   # 5s after C → dropped
    assert ex.build_chapters(mid, "youtube").splitlines() == ["00:00 A", "00:30 C"]
    assert ex.build_chapters(mid, "ffmetadata").count("[CHAPTER]") == 4  # no spacing rule
