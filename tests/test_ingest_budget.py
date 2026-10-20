"""預估與兩道截止線的算法。

2026-09-06 用 200 支現場素材反推出來的係數（單檔秒 ≈ 5.0 + 5.6 × 幀數，
R² = 0.88），這裡鎖住它的**性質**而不是鎖住那兩個數字 —— 數字會隨機器改，
性質不會：預估要隨量成長、探不到的檔不可以當成零、下限要擋住小批次被誤殺。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
import ingest_budget as ib  # noqa: E402


def test_estimate_tracks_the_real_run():
    """對照組：那批 200 支實測 94 分鐘，估值要落在同一個量級。"""
    durations = [11.0] * 100 + [9.0] * 60 + [30.0] * 30 + [90.0] * 10
    est = ib.estimate_seconds(durations)
    assert 60 * 60 <= est <= 150 * 60, "%.0f 分" % (est / 60)


def test_estimate_grows_with_frames_not_just_files():
    """同樣的檔案數、更長的素材 → 更多幀 → 更貴。

    這條在防「拿檔案數當唯一自變數」——實測那樣做 R² 只有 0.39。
    """
    short = ib.estimate_seconds([5.0] * 50)     # 每支 3 幀
    long_ = ib.estimate_seconds([120.0] * 50)   # 每支 7 幀
    assert long_ > short * 1.5


def test_unprobed_files_are_not_treated_as_zero():
    """🔴 探不到 ≠ 零秒。當成零會讓預算偏小，而偏小的預算會殺掉正常的匯入。"""
    all_known = ib.estimate_seconds([20.0] * 10)
    half_unknown = ib.estimate_seconds([20.0] * 5 + [None] * 5)
    assert half_unknown == pytest.approx(all_known, rel=0.15)
    # 反向：若被當成 0，估值會明顯掉下來
    assert half_unknown > all_known * 0.7


def test_estimate_extrapolates_when_only_a_sample_was_probed():
    """只探測了前 N 支時，其餘要用中位數補齊，不可以當作不存在。"""
    sampled = ib.estimate_seconds([20.0] * 10, n_files=100)
    full = ib.estimate_seconds([20.0] * 100)
    assert sampled == pytest.approx(full, rel=0.05)


def test_estimate_with_nothing_probed_still_scales_with_count():
    a = ib.estimate_seconds([None] * 10, n_files=10)
    b = ib.estimate_seconds([None] * 100, n_files=100)
    assert b == pytest.approx(a * 10, rel=0.01)
    assert a > 0


def test_budget_has_a_floor_so_small_imports_are_not_killed():
    """暖機一次就 ~100 秒；沒有下限的話最小的匯入反而最容易被誤殺。"""
    tiny = ib.budget_seconds(ib.estimate_seconds([3.0]))
    assert tiny >= config.INGEST_BUDGET_FLOOR_SECONDS


def test_budget_exceeds_the_estimate_it_is_derived_from():
    """預算必須明顯大於估值 —— 估值只是建議，等於死線就等於沒有餘裕。"""
    est = ib.estimate_seconds([30.0] * 300)
    assert ib.budget_seconds(est) >= est * 1.5


def test_stall_threshold_grows_with_the_longest_clip():
    """whisper 解碼是一支檔一次不透明呼叫 —— 長片會靜默很久，門檻要跟著放大。

    最慢的解碼路徑（faster-whisper large-v3 在 CPU 上）實測 RTF 1.22。
    """
    assert ib.stall_seconds(60) == config.INGEST_STALL_FLOOR_SECONDS
    two_hours = ib.stall_seconds(7200)
    assert two_hours > 7200, "門檻若小於素材本身時長，長片必被誤殺"


def test_stall_threshold_never_drops_below_the_floor():
    for d in (None, 0, -5, 1.0):
        assert ib.stall_seconds(d) >= config.INGEST_STALL_FLOOR_SECONDS


def test_frame_count_is_borrowed_not_reimplemented():
    """🔴 幀數必須跟 frames.py 同一份實作，不可以在這裡重抄一份會漂的尺。"""
    from frames import _adaptive_frame_count
    for d in (0.5, 1.9, 2.0, 10.0, 10.1, 60.0, 61.0, 200.0):
        assert ib._frames_for(d) == _adaptive_frame_count(d)
