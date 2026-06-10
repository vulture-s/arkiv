"""Unit tests for the faster-whisper transcription backend (non-Mac / CUDA path).

The real WhisperModel is never loaded — `_fw_model` is monkeypatched with a fake
that returns canned segments/info, so these run on any platform (incl. the Mac
MLX box, which cannot exercise the CUDA path). Live ingest→embed→chat on real
CUDA hardware is verified separately on the PC before release.
"""
import pytest

import transcribe


class FakeWord:
    def __init__(self, word, start, end, probability):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class FakeSegment:
    def __init__(self, text, start, end, words=None,
                 avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=1.2):
        self.text = text
        self.start = start
        self.end = end
        self.words = words
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob
        self.compression_ratio = compression_ratio


class FakeInfo:
    def __init__(self, language="zh"):
        self.language = language


class FakeModel:
    """Stand-in for faster_whisper.WhisperModel — records the opts it was called
    with and yields the canned segments as a generator (matching the real API)."""
    def __init__(self, segments, info):
        self._segments = segments
        self._info = info
        self.last_opts = None

    def transcribe(self, wav, **opts):
        self.last_opts = opts
        return (s for s in self._segments), self._info


@pytest.fixture
def hermetic(monkeypatch):
    """Neutralise the LLM-polish (Ollama) + filter-word post-processing so the
    test asserts the backend's raw contract, not downstream cleanup."""
    monkeypatch.setattr(transcribe, "LLM_POLISH", False)
    monkeypatch.setattr(transcribe, "FILTER_WORDS", "")
    monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "")


@pytest.fixture
def restore_guard_mode():
    """Restore the active whisper-guard layer after a test mutates it."""
    original = transcribe.WHISPER_GUARD_ACTIVE_MODE
    yield
    transcribe._apply_whisper_guard_mode(original)


def test_returns_four_tuple_contract(monkeypatch, hermetic):
    words = [
        FakeWord("你好", 0.0, 0.5, 0.91),
        FakeWord("世界", 0.5, 1.0, 0.88),
    ]
    segs = [
        FakeSegment("你好世界", 0.0, 1.0, words=words),
        FakeSegment("第二句", 1.0, 2.0, words=[FakeWord("第二句", 1.0, 2.0, 0.77)]),
    ]
    monkeypatch.setattr(transcribe, "_fw_model", FakeModel(segs, FakeInfo("zh")))

    text, lang, segments, out_words = transcribe._transcribe_faster_whisper("/fake.wav", "zh")

    assert lang == "zh"
    assert "你好世界" in text and "第二句" in text
    assert [s["text"] for s in segments] == ["你好世界", "第二句"]
    assert all({"start", "end", "text"} <= set(s) for s in segments)
    assert len(out_words) == 3


def test_word_score_maps_from_probability(monkeypatch, hermetic):
    segs = [FakeSegment("詞", 0.0, 0.4, words=[FakeWord("詞", 0.0, 0.4, 0.9345)])]
    monkeypatch.setattr(transcribe, "_fw_model", FakeModel(segs, FakeInfo("zh")))

    _text, _lang, _segments, out_words = transcribe._transcribe_faster_whisper("/fake.wav", "zh")

    assert out_words[0]["word"] == "詞"
    assert out_words[0]["score"] == pytest.approx(0.934, abs=1e-3)  # probability → score, rounded(3)
    assert out_words[0]["start"] == 0.0 and out_words[0]["end"] == 0.4


def test_guard_layer_options_mapped(monkeypatch, hermetic, restore_guard_mode):
    transcribe._apply_whisper_guard_mode(3)  # mode 3 has non-None thresholds
    layer = transcribe._current_whisper_guard_settings()
    fake = FakeModel([FakeSegment("x", 0.0, 0.5, words=[])], FakeInfo("zh"))
    monkeypatch.setattr(transcribe, "_fw_model", fake)

    transcribe._transcribe_faster_whisper("/fake.wav", "zh")

    assert fake.last_opts["language"] == "zh"
    assert fake.last_opts["beam_size"] == layer["beam_size"]
    assert fake.last_opts["condition_on_previous_text"] == layer["condition_on_previous_text"]
    assert fake.last_opts["word_timestamps"] is True
    assert fake.last_opts["compression_ratio_threshold"] == layer["compression_ratio_threshold"]
    assert fake.last_opts["log_prob_threshold"] == layer["whisperx"]["log_prob_threshold"]


def test_empty_segments_returns_empty_contract(monkeypatch, hermetic):
    monkeypatch.setattr(transcribe, "_fw_model", FakeModel([], FakeInfo("en")))

    text, lang, segments, words = transcribe._transcribe_faster_whisper("/fake.wav", "en")

    assert text == ""
    assert lang == "en"
    assert segments == []
    assert words == []


def test_segment_without_words_still_parses(monkeypatch, hermetic):
    segs = [FakeSegment("沒有逐字時間戳", 0.0, 1.5, words=None)]
    monkeypatch.setattr(transcribe, "_fw_model", FakeModel(segs, FakeInfo("zh")))

    text, _lang, segments, words = transcribe._transcribe_faster_whisper("/fake.wav", "zh")

    assert "沒有逐字時間戳" in text
    assert len(segments) == 1
    assert words == []


def test_dispatcher_routes_to_faster_whisper_on_non_mac(monkeypatch, hermetic):
    """transcribe() on a non-Mac box (default backend) goes through the
    faster-whisper path: _to_wav → _vad_filter → _transcribe_faster_whisper."""
    monkeypatch.setattr(transcribe, "_USE_MLX", False)
    monkeypatch.setattr(transcribe, "_to_wav", lambda p: "/fake_to.wav")
    monkeypatch.setattr(transcribe, "_vad_filter", lambda w: w)  # speech present, no trim
    segs = [FakeSegment("路由測試", 0.0, 1.0, words=[FakeWord("路由測試", 0.0, 1.0, 0.8)])]
    monkeypatch.setattr(transcribe, "_fw_model", FakeModel(segs, FakeInfo("zh")))

    text, lang, segments, words = transcribe.transcribe("/clip.mp4", language="zh")

    assert "路由測試" in text
    assert lang == "zh"
    assert len(segments) == 1 and len(words) == 1


def test_dispatcher_no_speech_returns_empty(monkeypatch, hermetic):
    """VAD finding no speech short-circuits to the empty contract."""
    monkeypatch.setattr(transcribe, "_USE_MLX", False)
    monkeypatch.setattr(transcribe, "_to_wav", lambda p: "/fake_to.wav")
    monkeypatch.setattr(transcribe, "_vad_filter", lambda w: None)  # no speech

    assert transcribe.transcribe("/clip.mp4", language="zh") == ("", "", [], [])


# ── custom vocabulary: env + file merge (FatSub-style hotword wordlist) ───────

def test_custom_terms_env_only(monkeypatch):
    monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "富田, Furutech ,明燒肉")
    monkeypatch.setattr(transcribe, "VOCABULARY_FILE", "")
    assert transcribe._custom_terms() == ["富田", "Furutech", "明燒肉"]
    assert transcribe._build_initial_prompt() == "富田、Furutech、明燒肉"


def test_custom_terms_file_only(monkeypatch, tmp_path):
    vf = tmp_path / "vocabulary.txt"
    vf.write_text("# 影視人名詞庫\n恬馨\n\nWaffle House\n  明燒肉  \n", encoding="utf-8")
    monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "")
    monkeypatch.setattr(transcribe, "VOCABULARY_FILE", str(vf))
    # comments + blank lines ignored, surrounding whitespace trimmed
    assert transcribe._custom_terms() == ["恬馨", "Waffle House", "明燒肉"]


def test_custom_terms_env_and_file_merge_dedup(monkeypatch, tmp_path):
    vf = tmp_path / "vocabulary.txt"
    vf.write_text("明燒肉\n富田\n新詞\n", encoding="utf-8")
    monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "富田,明燒肉")
    monkeypatch.setattr(transcribe, "VOCABULARY_FILE", str(vf))
    # env terms keep their leading position; file-only '新詞' appended; dups dropped
    assert transcribe._custom_terms() == ["富田", "明燒肉", "新詞"]


def test_custom_terms_missing_file_non_fatal(monkeypatch):
    monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "甲,乙")
    monkeypatch.setattr(transcribe, "VOCABULARY_FILE", "/no/such/vocabulary.txt")
    assert transcribe._custom_terms() == ["甲", "乙"]


def test_custom_terms_empty(monkeypatch):
    monkeypatch.setattr(transcribe, "CUSTOM_VOCABULARY", "")
    monkeypatch.setattr(transcribe, "VOCABULARY_FILE", "")
    assert transcribe._custom_terms() == []
    assert transcribe._build_initial_prompt() == ""
