import importlib


def test_is_repetitive_detects_looping_text():
    transcribe = importlib.import_module("transcribe")
    text = "字幕循環內容字幕循環內容字幕循環內容字幕循環內容字幕循環內容"
    assert transcribe._is_repetitive(text) is True


def test_is_repetitive_keeps_normal_chinese_sentence():
    transcribe = importlib.import_module("transcribe")
    text = "今天的訪談內容很完整，句子長度自然，而且沒有不合理的重複。"
    assert transcribe._is_repetitive(text) is False


def test_char_loop_helpers_handle_chinese_patterns():
    transcribe = importlib.import_module("transcribe")
    text = "字幕由字幕由字幕由字幕由"
    assert transcribe._has_char_loops(text) is True
    assert transcribe._remove_char_loops(text) == "字幕由"
    assert transcribe._has_char_loops("內容自然，沒有循環。") is False


def test_postprocess_returns_empty_when_average_no_speech_too_high(monkeypatch):
    transcribe = importlib.import_module("transcribe")
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    segments = [
        {"text": "幾乎沒有語音", "no_speech_prob": 0.9},
        {"text": "還是沒有語音", "no_speech_prob": 0.7},
    ]
    cleaned, lang = transcribe._postprocess("原始文字", "zh", segments, "zh")
    assert (cleaned, lang) == ("", "zh")


def test_postprocess_filters_bad_segments_but_keeps_threshold_boundary(monkeypatch):
    transcribe = importlib.import_module("transcribe")
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    segments = [
        {
            "text": "這段應該保留，因為它剛好在邊界值。",
            "no_speech_prob": 0.8,
            "avg_logprob": -1.5,
            "compression_ratio": 3.0,
        },
        {
            "text": "靜音誤判",
            "no_speech_prob": 0.81,
            "avg_logprob": -0.2,
            "compression_ratio": 1.0,
        },
        {
            "text": "低信心段落",
            "no_speech_prob": 0.1,
            "avg_logprob": -1.6,
            "compression_ratio": 1.0,
        },
        {
            "text": "壓縮異常段落",
            "no_speech_prob": 0.1,
            "avg_logprob": -0.2,
            "compression_ratio": 3.1,
        },
    ]
    cleaned, _ = transcribe._postprocess("原始文字", "zh", segments, "zh")
    assert cleaned == "這段應該保留，因為它剛好在邊界值。"


def test_postprocess_rejects_repetitive_text(monkeypatch):
    transcribe = importlib.import_module("transcribe")
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    repetitive = "字幕循環內容字幕循環內容字幕循環內容字幕循環內容字幕循環內容"
    segments = [{
        "text": repetitive,
        "no_speech_prob": 0.1,
        "avg_logprob": -0.1,
        "compression_ratio": 1.2,
    }]
    cleaned, _ = transcribe._postprocess(repetitive, "zh", segments, "zh")
    assert cleaned == ""


def test_postprocess_removes_char_loops_and_polishes(monkeypatch):
    transcribe = importlib.import_module("transcribe")
    monkeypatch.setattr(transcribe, "LLM_POLISH", True)
    monkeypatch.setattr(
        transcribe,
        "_llm_polish",
        lambda text, language: "校正後：" + text,
    )
    segments = [{
        "text": "字幕由字幕由字幕由字幕由，這是一段正常補充。",
        "no_speech_prob": 0.1,
        "avg_logprob": -0.2,
        "compression_ratio": 1.1,
    }]
    cleaned, _ = transcribe._postprocess(
        "字幕由字幕由字幕由字幕由，這是一段正常補充。",
        "zh",
        segments,
        "zh",
    )
    assert cleaned == "校正後：字幕由，這是一段正常補充。"
