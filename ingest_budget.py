"""匯入要跑多久 —— 以及兩道截止線各自該設多少。

## 為什麼不是一個固定秒數

`routers/ingest.py` 原本寫死 `timeout=1800`。2026-09-06 拿 200 支現場素材實測，
整批需要 **94 分鐘** —— 那個上限撐不到第 60 支，而且它是硬殺整棵行程樹。
把上限調大又會讓「真的卡死」變成無限等待。所以拆成兩道：

  stall   子行程可以沉默多久   →  抓「卡住了」
  budget  整批總共可以跑多久   →  抓「估錯了」

## 係數怎麼來的

拿那次跑掛的 log 逐檔耗時（n=33）做迴歸。**用素材時長當自變數 R² 只有 0.39，
用幀數是 0.88** —— 成本由 vision 的幀數驅動，不是由片長驅動：

    單檔處理秒 ≈ 5.0 + 5.6 × 幀數        (R² = 0.88, n=33)

    幀數 3 → 中位 21.8s（每幀 7.3s）
    幀數 5 → 中位 32.7s（每幀 6.5s）

⚠️ 這兩個係數是 **M2 Max + qwen2.5vl:7b** 量到的，換機器會漂。所以它們是
`config` 的常數（可覆寫），而預算再乘一個安全係數 —— 估值本身不是死線，
`budget` 才是，而 `budget = 估值 × 2`。

⚠️ 幀數一律走 `frames._adaptive_frame_count`，**不在這裡重抄一份**：
兩份會漂，而漂掉之後預算會安靜地變錯。
"""
from __future__ import annotations

from typing import Iterable, List, Optional

import config


def _frames_for(duration_s: float) -> int:
    from frames import _adaptive_frame_count
    return _adaptive_frame_count(duration_s)


def estimate_seconds(durations: Iterable[Optional[float]], n_files: Optional[int] = None) -> float:
    """預估整批要跑多久（秒）。

    `durations` 是每支素材的時長；`None`／0（探測不到）的用其餘檔案的中位時長
    代入 —— **不可以當成 0**，那會讓預算偏小，而偏小的預算會殺掉正常的匯入。
    """
    ds: List[Optional[float]] = [d for d in durations]
    known = sorted(d for d in ds if d and d > 0)
    if n_files is None:
        n_files = len(ds)
    if not known:
        # 全部探不到 → 只能用檔案數 × 一支的平均成本（幀數取中間值 3）
        per_file = config.INGEST_PER_FILE_SECONDS + 3 * config.INGEST_PER_FRAME_SECONDS
        return float(n_files) * per_file
    median = known[len(known) // 2]
    total_frames = 0
    for d in ds:
        total_frames += _frames_for(d if d and d > 0 else median)
    # ds 短於 n_files（例如只抽樣探測了一部分）→ 其餘用中位數補齊
    missing = max(0, n_files - len(ds))
    total_frames += missing * _frames_for(median)
    return (n_files * config.INGEST_PER_FILE_SECONDS
            + total_frames * config.INGEST_PER_FRAME_SECONDS)


def budget_seconds(estimate_s: float, max_duration_s: Optional[float] = None) -> float:
    """總預算 —— 估值乘安全係數，並有兩道下限。

    下限一：小批次的估值可能只有幾十秒，而模型暖機一次就要 ~100 秒。
    沒有下限的話，最小的匯入反而最容易被誤殺。

    🔴 下限二（`max_duration_s`）：預算不得低於同一批的 stall 門檻。

    estimate_seconds() 只模型化了 vision 的每幀成本 —— 時長僅透過幀數影響它，
    轉錄時間完全沒進去。而 stall_seconds() 明白知道轉錄跟片長成正比（它自己
    的註解引 RTF 1.22）。兩個函式對同一件成本的模型互相矛盾，結果是：

        一支 2 小時的片 → budget 46 分，stall 156 分

    預算比「這一批被允許的沉默」還短 3.4 倍，所以長素材必然在還健康地轉錄時
    就被判 budget 超時，而 stall 那條路徑對超過 ~23 分鐘的素材是不可達的
    dead code。傳入 max_duration_s 讓預算至少涵蓋 stall 容許的等待。
    """
    floor = config.INGEST_BUDGET_FLOOR_SECONDS
    if max_duration_s:
        # 同一批最長素材的 stall 門檻 + 一趟估值，才是「合理的最壞情況」
        floor = max(floor, stall_seconds(max_duration_s) + estimate_s)
    return max(floor, estimate_s * config.INGEST_BUDGET_FACTOR)


def stall_seconds(max_duration_s: Optional[float]) -> float:
    """可以沉默多久才算卡住。

    不能是死的 10 分鐘：**whisper 的解碼是「一支檔一次不透明呼叫」**
    （`transcribe.py` 自己的註解），長片在解碼期間完全不出聲。最慢的解碼路徑
    是 faster-whisper large-v3 在 CPU 上（Mac 沒有 Metal 加速），實測 RTF 1.22
    —— 也就是一支片可能靜默到「比它自己還久」。所以門檻要隨最長的那支放大。
    """
    floor = config.INGEST_STALL_FLOOR_SECONDS
    if not max_duration_s or max_duration_s <= 0:
        return float(floor)
    return float(max(floor, max_duration_s * config.INGEST_STALL_RTF))
