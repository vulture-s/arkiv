"""arkiv ↔ whisper_guard integration contract.

whisper_guard was de-vendored (Phase 12) and is now the external PyPI package
whisper-guard>=0.3, which ships its OWN regression suite. So arkiv no longer
re-tests the package internals here — it only pins the contract arkiv depends
on: the three private wrappers transcribe binds (_is_repetitive / _has_char_loops
/ _remove_char_loops) and the v0.3 public API surface. Parity with the old v0.1
free functions was verified at migration time.
"""
import importlib

import whisper_guard


# --------------------------------------------------------------------------
# v0.3 public API is present (catches a wrong/old install)
# --------------------------------------------------------------------------
def test_package_exposes_v03_api():
    assert hasattr(whisper_guard, "WhisperGuard")
    assert hasattr(whisper_guard, "GuardConfig")
    assert hasattr(whisper_guard, "filter_hallucinations")


# --------------------------------------------------------------------------
# arkiv's transcribe wrappers behave as the pipeline expects
# --------------------------------------------------------------------------
def _transcribe():
    return importlib.import_module("transcribe")


def test_is_repetitive_wrapper_returns_bool():
    tr = _transcribe()
    # needs >= window*3 (18) chars to be eligible
    assert tr._is_repetitive("字幕由" * 8) is True
    assert tr._is_repetitive("今天天氣很好，我們一起去公園散步聊天。") is False
    assert tr._is_repetitive("好") is False
    assert tr._is_repetitive("") is False


def test_has_char_loops_wrapper_returns_bool():
    tr = _transcribe()
    assert tr._has_char_loops("哈哈哈哈哈哈") is True
    assert tr._has_char_loops("字幕由字幕由字幕由") is True
    assert tr._has_char_loops("內容自然，沒有循環。") is False


def test_remove_char_loops_wrapper_returns_str():
    tr = _transcribe()
    # v0.3 remove_char_loops returns (text, count); the wrapper must unwrap to str
    out = tr._remove_char_loops("字幕由字幕由字幕由")
    assert isinstance(out, str)
    assert out == "字幕由"
    assert tr._remove_char_loops("哈哈哈哈哈哈") == "哈哈"
    assert tr._remove_char_loops("正常文字") == "正常文字"
