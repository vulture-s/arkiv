"""VAD 說「沒人聲」時不得直接放棄。

2026-09-07 在一場 200 支的現場素材上實測：85 支 transcript 為空，繞過 VAD
重跑後 **54 支解出人聲** —— 誤判率 64%。而舊行為在誤判時回 `("", "", [], [])`，
**跟真的沒人聲回的值一模一樣** ⇒ 使用者永遠不知道哪幾支被漏掉了。

**那個「分不出來」才是缺陷本體**，誤判率只是決定它有多大。

本檔釘住的是：`_vad_filter` 回 `(None, None)` 時，`transcribe()` 仍然要把
**原始整段** 餵給 backend、且 `offset_map` 必須是 `None`（沒有裁切 ⇒ 兩個時鐘
本來就相同，誤傳 offset_map 會把時間軸整個算錯）。
"""
import pytest

import transcribe as tr


@pytest.fixture
def stub_backend(monkeypatch, tmp_path):
    """把 wav 抽取與 backend 都換成 stub，只留 VAD 那一段的決策邏輯。"""
    wav = str(tmp_path / "a.wav")
    calls = []

    monkeypatch.setattr(tr, "_to_wav", lambda p: wav)
    monkeypatch.setattr(tr, "_USE_MLX", False)
    monkeypatch.setattr(tr, "_non_mac_backend", lambda: "faster-whisper")
    monkeypatch.setattr(tr, "zh_convert", type("_Z", (), {
        "convert_result": staticmethod(lambda *a: a)})())
    monkeypatch.setattr(tr, "_remap_result_times",
                        lambda *a, **kw: (a[0], a[1], a[2], a[3]))

    def _backend(w, lang):
        calls.append(w)
        return ("解出來的字", "zh", [], [])

    monkeypatch.setattr(tr, "_transcribe_faster_whisper", _backend)
    return wav, calls


def test_no_speech_still_decodes_the_full_file(stub_backend, monkeypatch):
    """🔴 本次修的那一格：VAD 回 (None, None) 不再是終局。"""
    wav, calls = stub_backend
    monkeypatch.setattr(tr, "_vad_filter", lambda w: (None, None))

    text, lang, _segs, _words = tr.transcribe("/clip.mp4", language="zh")

    assert calls == [wav], "backend 沒被呼叫 ⇒ 又回到靜默放棄"
    assert text == "解出來的字"
    assert lang == "zh"


def test_no_speech_fallback_says_so(stub_backend, monkeypatch, capsys):
    """舊的失敗是隱形的，所以 fallback 一定要出聲。"""
    monkeypatch.setattr(tr, "_vad_filter", lambda w: (None, None))
    tr.transcribe("/clip.mp4", language="zh")
    assert "no speech detected" in capsys.readouterr().out


def test_no_speech_fallback_does_not_carry_an_offset_map(monkeypatch, tmp_path):
    """沒有裁切 ⇒ offset_map 必須是 None。

    帶著上一次的 offset_map 走 fallback，會把整段的時間戳重新映射到一個
    根本沒發生過的裁切上 —— 比沒有 transcript 更糟（字對、時間全錯）。
    """
    wav = str(tmp_path / "a.wav")
    monkeypatch.setattr(tr, "_to_wav", lambda p: wav)
    monkeypatch.setattr(tr, "_vad_filter", lambda w: (None, None))
    assert tr._vad_or_full(wav) == (wav, None)


def test_speech_path_is_untouched(monkeypatch, tmp_path):
    """🔴 反例：VAD 有找到人聲時，裁切後的 wav 與 offset_map 都要原樣傳回。

    少了這一格，把 `_vad_or_full` 寫成「永遠回 (wav, None)」也會全綠 ——
    而那等於整個關掉 VAD，靜音不再被裁掉、時間軸也不用 remap 了。
    """
    trimmed = str(tmp_path / "trimmed.wav")
    omap = [(0.0, 1.0, 5.0)]
    monkeypatch.setattr(tr, "_vad_filter", lambda w: (trimmed, omap))
    assert tr._vad_or_full("/orig.wav") == (trimmed, omap)


def test_speech_path_stays_quiet(monkeypatch, capsys):
    """反例組第二格：正常路徑不得印那句 fallback，否則訊號會被稀釋成噪音。"""
    monkeypatch.setattr(tr, "_vad_filter", lambda w: ("/t.wav", None))
    tr._vad_or_full("/orig.wav")
    assert "no speech detected" not in capsys.readouterr().out
