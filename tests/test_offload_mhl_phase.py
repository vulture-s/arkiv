"""拷卡完成之後、done 之前的那兩趟雜湊 —— 它們的可見性與狀態語意。

2026-09-06 實地跑 200 支／89 GB：進度條在 00:55 走到 200/200，`done` 事件
01:30 才出現。中間 35 分鐘是 MHL 寫入 + 驗證各讀完一次全部位元組，而兩者
都不發任何事件 —— 畫面上「還在跑」與「當掉了」完全同形。

更嚴重的是狀態語意：`status` 原本在 MHL 之前就寫成 "done"。在那 35 分鐘裡
按取消，state 會留下一筆 `status: "done"` 且 `mhl_path: null` 的紀錄 ——
跟真正完成的紀錄無法區分，而 MHL 正是 DIT 的保管鏈交付物。
"""
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "safe.directory={0}".format(ROOT), "-C", str(ROOT)]


def _bootstrap_mhl(tmp_path, monkeypatch):
    mhl_src = subprocess.run(
        GIT + ["show", "HEAD:mhl.py"], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    module_dir = tmp_path / "bootstrap"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "mhl.py").write_text(mhl_src, encoding="utf-8")
    monkeypatch.syspath_prepend(str(module_dir))
    sys.modules.pop("mhl", None)
    sys.modules.pop("offload", None)
    return importlib.import_module("offload")


@pytest.fixture
def scratch():
    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="arkiv-mhlphase-", dir=str(temp_root)))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _src(root):
    src = root / "src"
    (src / "A001").mkdir(parents=True, exist_ok=True)
    (src / "A001" / "clip_001.mp4").write_bytes(b"alpha")
    (src / "A001" / "clip_002.mp4").write_bytes(b"bravo")
    return src


def _events(capsys):
    out = []
    for line in capsys.readouterr().out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def test_mhl_write_and_verify_each_announce_themselves(scratch, monkeypatch, capsys):
    """兩趟雜湊各自要有事件，且要落在最後一個 file 與 done 之間。"""
    offload = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    code, _summary, _sp = offload.run_offload(
        _src(scratch), [scratch / "dst"], verify=True, emit_mhl=True, progress="json")
    assert code == 0

    evs = _events(capsys)
    kinds = [e.get("type") for e in evs]
    phases = [e for e in evs if e.get("type") == "phase"]
    assert [p["phase"] for p in phases] == ["mhl_write", "mhl_verify"], kinds
    # 檔案數要帶上 —— 使用者要知道這一趟在雜湊幾個檔，否則「還要多久」無從估計
    assert all(p["files"] == 2 for p in phases), phases

    last_file = max(i for i, k in enumerate(kinds) if k == "file")
    first_phase = min(i for i, k in enumerate(kinds) if k == "phase")
    assert first_phase > last_file, "phase 必須在最後一個 file 之後（那正是靜默期的起點）"


def test_phase_events_stay_silent_in_tui_mode(scratch, monkeypatch, capsys):
    """負向：tui 模式不該吐 JSON —— 否則會汙染人看的輸出。"""
    offload = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    offload.run_offload(_src(scratch), [scratch / "dst"], verify=True,
                        emit_mhl=True, progress="tui")
    assert not [e for e in _events(capsys) if e.get("type") == "phase"]


def test_status_is_not_done_when_mhl_write_fails(scratch, monkeypatch):
    """🔴 本檔的核心：MHL 沒寫成，state 就不可以說 done。

    這是「取消在那 35 分鐘裡」的可測代理 —— 兩者都是「拷完了但 manifest 沒有」。

    ⚠️ 2026-09-08 審計 round 2：原本這裡是 `pytest.raises(RuntimeError)` ——
    失敗會 raise 出整個 dst 迴圈。那讓本檔的核心成立了，但代價是**剩下的目的地
    一顆都不會拷**，而第二顆碟正是兩碟備份的全部意義。現在失敗收斂在這顆碟裡，
    所以核心命題從「不是 done」升級成更強的「明確是 failed，而且說得出原因」。
    """
    offload = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated: interrupted during manifest write")
    monkeypatch.setattr(offload, "_write_mhl", _boom)

    code, summary, _sp = offload.run_offload(
        _src(scratch), [scratch / "dst"], verify=True,
        emit_mhl=True, resume=str(scratch / "st.json"))

    st = json.loads((scratch / "st.json").read_text(encoding="utf-8"))
    key = str((scratch / "dst").resolve())
    dst_state = st["destinations"][key]
    assert dst_state["status"] != "done", "檔案拷完不等於過卡完成 —— manifest 才是交付物"
    assert dst_state["status"] == "failed", "「running」跟使用者中途取消同形，分不出來"
    assert "interrupted during manifest write" in (dst_state.get("error") or "")
    assert not dst_state.get("mhl_path")
    assert summary[key]["error"], "summary 是 UI 唯一讀得到的東西"
    assert code != 0, "沒有 manifest 不可以回 0"


def test_status_done_always_carries_an_mhl_path(scratch, monkeypatch):
    """正向配對：成功時 done 與 mhl_path 必須同時成立，不可只有其一。"""
    offload = _bootstrap_mhl(scratch, monkeypatch)
    monkeypatch.chdir(scratch)
    _code, summary, state_path = offload.run_offload(
        _src(scratch), [scratch / "dst"], verify=True, emit_mhl=True)
    st = json.loads(Path(state_path).read_text(encoding="utf-8"))
    key = str((scratch / "dst").resolve())
    assert st["destinations"][key]["status"] == "done"
    assert st["destinations"][key]["mhl_path"]
    assert Path(summary[key]["mhl_path"]).exists()
