"""縮圖 seek 位置的上下限。

失效模式是**靜默**：1 秒以下的片算出的中點被 >=1s 的下限抬過檔尾，
ffmpeg 回 `Nothing was written into output file`，於是那支片**沒有縮圖**，
而 `_adaptive_frame_count` 對 `duration_s < 2` 另有處理（回 1 格）⇒
**視覺標籤正常、只有縮圖掛掉**，畫面上看不出來。

實測（A7520260906_0116.MP4，0.5s）：seek 1.0s 零輸出、seek 0.25s 得到 239 KB JPEG。

本檔驗的是純函式 `_thumbnail_seek`，不需要 ffmpeg 或真檔。
"""
import pytest

from frames import _EOF_MARGIN_S, _thumbnail_seek


@pytest.mark.parametrize("duration_s", [0.04, 0.1, 0.5, 0.9, 1.0, 1.5, 2.0])
def test_never_seeks_past_end(duration_s):
    """任何時長都不得 seek 到檔尾之後 —— 這是本次修的那個 bug。"""
    t = _thumbnail_seek(duration_s)
    assert t <= duration_s, "seek %.3f > duration %.3f" % (t, duration_s)
    assert t >= 0.0


def test_the_regression_case():
    """0.5s：修法前是 1.0（過檔尾、零輸出），修法後必須落在檔內。"""
    t = _thumbnail_seek(0.5)
    assert t < 0.5
    assert t == pytest.approx(0.5 - _EOF_MARGIN_S)


@pytest.mark.parametrize("duration_s,expected", [
    (2.0, 1.0),      # 下限剛好生效
    (4.0, 2.0),      # 中點
    (10.0, 5.0),
    (600.0, 300.0),
])
def test_normal_clips_unchanged(duration_s, expected):
    """🔴 反例組：>=2s 的片行為**必須跟修法前一模一樣**。

    少了這組，把整條改成 `duration_s * 0.5` 也會全綠 —— 而那會讓
    1 秒以下的片全部回到 t≈0（正是 >=1s 下限當初要防的）。
    """
    assert _thumbnail_seek(duration_s) == pytest.approx(expected)


def test_floor_still_applies_between_margin_and_two_seconds():
    """下限沒有被上限吃掉：1.5s 的片仍取 1.0 而不是 0.75。"""
    assert _thumbnail_seek(1.5) == pytest.approx(1.0)


def test_degenerate_duration_clamps_to_zero():
    """時長比 margin 還短時回 0，不得回負數（ffmpeg 的 -ss 不吃負值）。"""
    assert _thumbnail_seek(0.01) == 0.0
    assert _thumbnail_seek(0.0) == 0.0
