import importlib


def test_split_sentences_and_cjk_detection():
    vectordb = importlib.import_module("vectordb")
    text = "第一句。第二句！Third sentence?"
    assert vectordb._split_sentences(text) == ["第一句。", "第二句！", "Third sentence?"]
    assert vectordb._is_cjk("這是一段中文內容" * 5) is True
    assert vectordb._is_cjk("This is an English paragraph. " * 5) is False


def test_chunk_text_handles_short_empty_chinese_and_english():
    vectordb = importlib.import_module("vectordb")

    assert vectordb.chunk_text("短句。") == ["短句。"]
    assert vectordb.chunk_text("") == [""]

    long_zh = "這是一段用來測試分塊效果的中文句子。" * 80
    zh_chunks = vectordb.chunk_text(long_zh)
    assert len(zh_chunks) > 1
    assert all(chunk for chunk in zh_chunks)

    long_en = ("This is an English sentence used for chunking tests. " * 120).strip()
    en_chunks = vectordb.chunk_text(long_en)
    assert len(en_chunks) > 1
    assert en_chunks[0] != en_chunks[-1]


def test_build_doc_text_supports_transcript_filename_only_and_bad_json():
    vectordb = importlib.import_module("vectordb")

    # Legacy schema 用 "keywords" 字串（早期版本）
    legacy = vectordb.build_doc_text(
        {
            "filename": "clip.mp4",
            "transcript": "中文逐字稿",
            "frame_tags": '[{"keywords":"人物 場景"}]',
        }
    )
    assert "[clip.mp4]" in legacy
    assert "中文逐字稿" in legacy
    assert "人物 場景" in legacy

    filename_only = vectordb.build_doc_text(
        {"filename": "only-name.mp4", "transcript": "", "frame_tags": None}
    )
    assert filename_only == "[only-name.mp4]"

    bad_json = vectordb.build_doc_text(
        {"filename": "broken.mp4", "transcript": "", "frame_tags": "{bad json}"}
    )
    assert bad_json == "[broken.mp4]"


def test_build_doc_text_extracts_production_vision_schema():
    """audit critical fix：production frame_tags 是 description + tags 結構，
    不是 legacy keywords，舊邏輯讓 vision 全部沒進 vector index。

    Partial-field（缺 focus_score / exposure / stability / audio_quality /
    edit_reason）是刻意：本 test 只驗 build_doc_text 對 description + tags 的
    抽取，quality 欄不影響 vector index（只進 metadata 不進 doc text）。完整
    schema 在 conftest sample_record。"""
    vectordb = importlib.import_module("vectordb")
    import json
    frame_tags = json.dumps([
        {
            "description": "戶外體育課，孩童在跳繩。",
            "tags": ["兒童", "戶外", "運動"],
            "content_type": "A-Roll",
            "atmosphere": "活潑",
        },
        {
            "description": "近景特寫人物表情。",
            "tags": ["特寫", "人物"],
        },
    ], ensure_ascii=False)

    doc = vectordb.build_doc_text({
        "filename": "clip.mp4",
        "transcript": "",
        "frame_tags": frame_tags,
    })
    # Description 拉進 doc text — 不再 silently 漏掉
    assert "戶外體育課，孩童在跳繩。" in doc
    assert "近景特寫人物表情。" in doc
    # Tags 也拉進來
    assert "兒童" in doc and "戶外" in doc and "運動" in doc
    assert "特寫" in doc and "人物" in doc


def test_build_doc_text_handles_mixed_schema_in_same_record():
    """新舊混合：有 description / tags 的 frame 跟有 keywords 的 frame 並存應同時拉。"""
    vectordb = importlib.import_module("vectordb")
    import json
    frame_tags = json.dumps([
        {"description": "新版描述", "tags": ["新版-tag"]},
        {"keywords": "舊版 keyword 字串"},
    ], ensure_ascii=False)
    doc = vectordb.build_doc_text({
        "filename": "mixed.mp4",
        "transcript": "",
        "frame_tags": frame_tags,
    })
    assert "新版描述" in doc
    assert "新版-tag" in doc
    assert "舊版 keyword 字串" in doc


def test_embed_truncates_before_request(monkeypatch):
    vectordb = importlib.import_module("vectordb")
    captured = {}

    class FakeResponse(object):
        def raise_for_status(self):
            return None

        def json(self):
            return {"embedding": [0.1, 0.2, 0.3]}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["prompt_len"] = len(json["prompt"])
        captured["model"] = json["model"]
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(vectordb.requests, "post", fake_post)
    result = vectordb.embed("中" * (vectordb.EMBED_MAX_CHARS + 500))

    assert result == [0.1, 0.2, 0.3]
    assert captured["url"] == vectordb.OLLAMA_EMBED_URL
    assert captured["model"] == vectordb.EMBED_MODEL
    assert captured["prompt_len"] == vectordb.EMBED_MAX_CHARS
    assert captured["timeout"] == 30


def test_search_deduplicates_media_results_and_rounds_scores(monkeypatch):
    vectordb = importlib.import_module("vectordb")

    class FakeCollection(object):
        def count(self):
            return 3

        def query(self, query_embeddings, n_results, include):
            assert query_embeddings == [[0.42]]
            assert n_results == 3
            assert include == ["documents", "metadatas", "distances"]
            return {
                "documents": [[
                    "第一個片段",
                    "同一檔案的第二個片段",
                    "另一個檔案",
                ]],
                "metadatas": [[
                    {
                        "media_id": "1",
                        "filename": "a.mp4",
                        "path": "/tmp/a.mp4",
                        "duration_s": 12,
                        "lang": "zh",
                        "chunk_type": "transcript",
                    },
                    {
                        "media_id": "1",
                        "filename": "a.mp4",
                        "path": "/tmp/a.mp4",
                        "duration_s": 12,
                        "lang": "zh",
                        "chunk_type": "transcript",
                    },
                    {
                        "media_id": "2",
                        "filename": "b.mp3",
                        "path": "/tmp/b.mp3",
                        "duration_s": 30,
                        "lang": "en",
                        "chunk_type": "frame_tags",
                    },
                ]],
                "distances": [[0.1, 0.2, 0.35]],
            }

    monkeypatch.setattr(vectordb, "embed", lambda text: [0.42])
    monkeypatch.setattr(vectordb, "get_collection", lambda: FakeCollection())

    results = vectordb.search("受訪者說了什麼", n_results=2)

    assert results == [
        {
            "media_id": "1",
            "filename": "a.mp4",
            "path": "/tmp/a.mp4",
            "duration_s": 12,
            "lang": "zh",
            "excerpt": "第一個片段",
            "score": 0.9,
            "chunk_type": "transcript",
        },
        {
            "media_id": "2",
            "filename": "b.mp3",
            "path": "/tmp/b.mp3",
            "duration_s": 30,
            "lang": "en",
            "excerpt": "另一個檔案",
            "score": 0.65,
            "chunk_type": "frame_tags",
        },
    ]
