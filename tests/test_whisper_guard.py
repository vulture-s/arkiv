"""Phase 10 — whisper_guard package public-API tests.

These exercise the extracted package directly (not via transcribe), so they
double as the package's own regression suite when it's split into its own repo.
"""
import whisper_guard as wg
from whisper_guard import HallucinationGuard


# --------------------------------------------------------------------------
# is_repetitive
# --------------------------------------------------------------------------
def test_is_repetitive_detects_loop():
    # needs >= window*3 (18) chars to be eligible
    assert wg.is_repetitive("字幕由" * 8) is True


def test_is_repetitive_keeps_normal_sentence():
    assert wg.is_repetitive("今天天氣很好，我們一起去公園散步聊天。") is False


def test_is_repetitive_short_text_never_flagged():
    assert wg.is_repetitive("好") is False
    assert wg.is_repetitive("短句") is False


def test_is_repetitive_empty():
    assert wg.is_repetitive("") is False


# --------------------------------------------------------------------------
# char loops
# --------------------------------------------------------------------------
def test_has_char_loops_true():
    assert wg.has_char_loops("哈哈哈哈哈哈") is True
    assert wg.has_char_loops("字幕由字幕由字幕由") is True


def test_has_char_loops_false():
    assert wg.has_char_loops("內容自然，沒有循環。") is False


def test_remove_char_loops_collapses():
    assert wg.remove_char_loops("字幕由字幕由字幕由") == "字幕由"
    # collapse leaves the matched 2-4 char unit (greedy picks the 2-char unit here)
    assert wg.remove_char_loops("哈哈哈哈哈哈") == "哈哈"


def test_remove_char_loops_noop_on_clean():
    assert wg.remove_char_loops("正常文字") == "正常文字"


# --------------------------------------------------------------------------
# HallucinationGuard convenience class
# --------------------------------------------------------------------------
def test_guard_is_hallucination():
    g = HallucinationGuard()
    assert g.is_hallucination("好好好好好好好好好好") is True
    assert g.is_hallucination("字幕由字幕由字幕由") is True
    assert g.is_hallucination("一段完全正常的中文句子，沒問題。") is False


def test_guard_clean():
    g = HallucinationGuard()
    assert g.clean("字幕由字幕由字幕由") == "字幕由"
    assert g.clean("乾淨文字") == "乾淨文字"


def test_is_repetitive_threshold_param():
    # 3 chunks, 2 unique -> ratio 0.667; trips at 0.9 but not at default 0.35.
    text = "abcdefabcdefghijklmnopqr"
    assert wg.is_repetitive(text, threshold=0.35) is False
    assert wg.is_repetitive(text, threshold=0.9) is True


def test_version_exposed():
    assert isinstance(wg.__version__, str)


# --------------------------------------------------------------------------
# extraction parity: transcribe's private aliases ARE the package functions
# --------------------------------------------------------------------------
def test_transcribe_aliases_are_package_functions():
    import importlib
    transcribe = importlib.import_module("transcribe")
    assert transcribe._is_repetitive is wg.is_repetitive
    assert transcribe._has_char_loops is wg.has_char_loops
    assert transcribe._remove_char_loops is wg.remove_char_loops
