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
