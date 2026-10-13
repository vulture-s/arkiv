"""審計 round 2 —— 七個「畫面說沒事、實際上少做了一件事」的缺口。

Round 1 修的是「會殺掉健康匯入、卻不說是誰殺的」。這一輪的形狀不同：
每一條都是**做少了一件事，而接收面看不出來**。

  ④ MHL 校驗失敗 raise 出整個 dst 迴圈 → 第二顆碟根本沒複製
  ⑧ run_tree_watched 的 stderr 恆空，而 /api/ingest 拿它當診斷欄
  ⑨ dst_start 沒清 stage → 第二顆碟複製中卻顯示「正在校驗 MHL」
  ⑩ 中途加入的 client 沒收過 start → 0 檔 0% 空白 elapsed 蓋在跑著的匯入上
  ⑪ 捷徑的分隔字元沒跳脫，而寫回是整串重寫 → 新增一筆會固化既有的損壞
  ⑫ scan 探測過的檔，實際跑的時候整份重探
  ⑯ max_probe 取前 500 筆 → 最長的那支可能沒被取樣，而 stall 門檻靠它
"""
import io
import inspect

import pytest


# ── ④ MHL 失敗要留在這顆碟裡 ────────────────────────────────────────────────

def _card(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    (card / "C0001.MP4").write_bytes(b"x" * 64)
    return card


def test_mhl_failure_still_copies_the_other_destination(tmp_path, monkeypatch):
    """第二顆碟是兩碟備份的全部意義 —— 第一顆的 manifest 掛了不該取消它。"""
    import offload
    card = _card(tmp_path)
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir(); d2.mkdir()

    def _boom(dst_root, hash_algo, op="offload"):
        if str(dst_root) == str(d1):
            raise RuntimeError("mhl write blew up")
        return dst_root / "fake.mhl"

    monkeypatch.setattr(offload, "_write_mhl", _boom)
    monkeypatch.setattr(offload, "_verify_emitted_mhl", lambda *a, **k: 0)

    code, summary, _ = offload.run_offload(
        str(card), [str(d1), str(d2)], resume=str(tmp_path / "state.json"),
        progress="none")

    assert (d2 / "C0001.MP4").exists(), "第一顆碟的 manifest 失敗把第二顆碟整個跳過了"
    assert summary[str(d1)]["status"] == "failed"
    assert summary[str(d1)]["error"], "失敗要說是什麼失敗，不能只留一個 exit code"
    assert summary[str(d2)]["status"] == "done"
    assert code != 0, "有一顆碟沒有 manifest，不可以回 0"


def test_mhl_verify_mismatch_is_a_failed_destination(tmp_path, monkeypatch):
    """每個 byte 都複製並校驗過，但 manifest 對不上 —— 這顆碟仍然是 failed。

    verified_files 會是滿的，所以 failed_files == 0；只看那個數字會把
    「沒有 chain of custody 的碟」當成成功。
    """
    import offload
    card = _card(tmp_path)
    d1 = tmp_path / "d1"; d1.mkdir()
    monkeypatch.setattr(offload, "_write_mhl", lambda *a, **k: d1 / "x.mhl")
    monkeypatch.setattr(offload, "_verify_emitted_mhl", lambda *a, **k: 3)

    code, summary, _ = offload.run_offload(
        str(card), [str(d1)], resume=str(tmp_path / "s.json"), progress="none")

    s = summary[str(d1)]
    assert s["failed_files"] == 0, "前提：複製本身沒失敗"
    assert s["status"] == "failed"
    assert "3" in (s["error"] or "")
    assert code == 2, "唯一的目的地掛了 → all-bad"


def test_mhl_failure_emits_a_phase_event(tmp_path, monkeypatch, capsys):
    """UI 的 ndjson reader 只 JSON.parse；traceback 走 stdout 會被它丟掉，
    所以失敗必須是一個 event，不能只是一段例外文字。"""
    import offload, json
    card = _card(tmp_path)
    d1 = tmp_path / "d1"; d1.mkdir()
    monkeypatch.setattr(offload, "_write_mhl", lambda *a, **k: d1 / "x.mhl")
    monkeypatch.setattr(offload, "_verify_emitted_mhl", lambda *a, **k: 9)
    offload.run_offload(str(card), [str(d1)], resume=str(tmp_path / "s.json"),
                        progress="json")
    events = []
    for line in capsys.readouterr().out.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    assert any(e.get("phase") == "mhl_failed" for e in events)


# ── ⑧ pump_error 不能是死路 ─────────────────────────────────────────────────

def test_watched_run_surfaces_pump_error_not_empty_stderr():
    """round 1 停止吞掉 pump 例外、改成記下來 —— 但沒有人讀那筆紀錄。

    stderr 因為 merge 進 stdout 而恆為空，偏偏 /api/ingest 把它當診斷欄印出來。
    唯一沒有別的通道的東西就是 pump 自己的例外，所以那格放它。
    """
    import proctree
    src = inspect.getsource(proctree.run_tree_watched)
    assert 'stderr=""' not in src, "TreeTimeout 仍寫死空 stderr"
    assert src.count('state.get("pump_error"') == 2, \
        "正常結束與逾時兩條路徑都要把 pump_error 交出去"


def test_watched_run_normal_path_has_empty_stderr_when_pump_is_healthy(tmp_path):
    import proctree, sys
    r = proctree.run_tree_watched(
        [sys.executable, "-c", "print('hi')"], stall_timeout=10, total_timeout=30)
    assert r.returncode == 0
    assert "hi" in r.stdout
    assert r.stderr == "", "pump 沒出事就不該無中生有一個錯誤"


# ── ⑫/⑯ 探測：不重複、而且要取樣到最長的那一支 ──────────────────────────────

def test_probe_durations_caches_on_size_and_mtime(tmp_path, monkeypatch):
    """scan 探過的檔，實際跑的時候不該再探一次（NAS 上 0.13s/檔 × 200）。"""
    from routers import ingest as R
    R._PROBE_CACHE.clear()
    f = tmp_path / "a.mp4"; f.write_bytes(b"x" * 10)
    calls = []

    class _R:
        stdout = "12.5"

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr(R.__dict__["__builtins__"] if False else __import__("subprocess"),
                        "run", _fake_run)
    assert R._probe_durations([str(f)]) == [12.5]
    assert R._probe_durations([str(f)]) == [12.5]
    assert len(calls) == 1, "同一個未變動的檔被探了兩次"

    f.write_bytes(b"y" * 999)  # size changes → cache must miss
    R._probe_durations([str(f)])
    assert len(calls) == 2, "檔案變了卻還吃舊的探測結果"


def test_probe_durations_samples_across_the_whole_folder(monkeypatch):
    """stall 門檻 = max(known) × RTF。取前 N 筆會讓排在後面的長片隱形，
    而 whisper 解碼那支長片時是完全靜默的 —— 門檻不夠大就會誤殺。"""
    import subprocess
    from routers import ingest as R
    R._PROBE_CACHE.clear()
    paths = ["/x/{0:04d}.mp4".format(i) for i in range(2000)]
    seen = []

    class _R:
        stdout = ""

    def _spy(cmd, **kw):
        seen.append(cmd[-1])
        return _R()

    monkeypatch.setattr(subprocess, "run", _spy)
    out = R._probe_durations(paths, max_probe=50)
    assert len(out) == 2000, "回傳長度必須對齊輸入，呼叫端是用 zip 對位的"
    assert len(seen) == 50, "探測數量的上限沒守住"
    nums = sorted(int(p.split("/")[-1].split(".")[0]) for p in seen)
    assert nums[-1] >= 1900, "2000 支裡最後段完全沒被取樣到 —— 最長的片會隱形"
    assert nums[0] <= 100, "取樣也要涵蓋前段"


# ── ⑫ 預估與實跑要算同一批檔 ────────────────────────────────────────────────

def test_ingest_limits_budgets_only_unprocessed_files(tmp_path, monkeypatch):
    """/api/ingest/scan 的註解宣稱 UI 顯示的上限跟實跑算出來的是同一個數字。
    limit=0（UI 的預設路徑）以前把已匯入的檔也算進去，那句宣稱就是假的。
    """
    from routers import ingest as R
    d = tmp_path / "lib"; d.mkdir()
    new = d / "new.mp4"; new.write_bytes(b"x")
    old = d / "old.mp4"; old.write_bytes(b"x")
    monkeypatch.setattr(R, "_unprocessed", lambda paths: [p for p in paths if p.endswith("new.mp4")])
    seen = {}

    def _probe(paths, max_probe=500):
        seen["paths"] = list(paths)
        return [60.0] * len(paths)

    monkeypatch.setattr(R, "_probe_durations", _probe)
    R._ingest_limits(d)
    assert seen["paths"] == [str(new)], "已匯入的檔還被算進預算"


def test_unprocessed_drops_indexed_and_survives_a_dead_db(monkeypatch):
    from routers import ingest as R
    import db as _db

    class _Conn:
        def execute(self, q):
            return [("/x/done.mp4",)]
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(_db, "get_conn", lambda: _Conn())
    assert R._unprocessed(["/x/done.mp4", "/x/new.mp4"]) == ["/x/new.mp4"]

    def _boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(_db, "get_conn", _boom)
    assert R._unprocessed(["/x/a.mp4"]) == ["/x/a.mp4"], \
        "DB 讀不到時要退回原樣，不能把預算算成 0 檔"


# ── ⑨/⑩/⑪ 前端：三個「少做一件事」的接收面 ─────────────────────────────────

def _svelte(name):
    return io.open("frontend/src/routes/" + name, encoding="utf-8").read()


def test_offload_ui_clears_stage_on_next_destination():
    """只看 dst_start 那一支，不能讓後面 done 分支裡的 stage = '' 混進來充數。"""
    src = _svelte("Offload.svelte")
    i = src.index("ev.type === 'dst_start'")
    j = src.index("ev.type === 'file'", i)   # 下一個 else-if = 這支的結尾
    branch = src[i:j]
    assert "stage = ''" in branch, \
        "換到下一顆碟時沒清 stage，第二顆碟複製中會顯示上一顆的『正在校驗 MHL』"
    assert "stageFiles = 0" in branch


def test_offload_ui_counts_a_manifest_failure_as_failed():
    src = _svelte("Offload.svelte")
    i = src.index("$: anyFailed")
    assert "s.error" in src[i:i + 200], \
        "manifest 失敗時 failed_files 是 0，只看它會把沒有 manifest 的碟當成功"
    assert "s.error ?" in src, "summary 那列要看得到失敗原因"


def test_ingest_live_adopts_total_when_joining_mid_run():
    src = _svelte("IngestLive.svelte")
    assert "adoptRunHeader" in src
    i = src.index("function adoptRunHeader")
    body = src[i:i + 500]
    assert "msg.total" in body, "中途加入時沒有從既有事件補回 total → 進度條恆 0%"
    assert "joinedLate" in body, "補上的 elapsed 是『加入後』，要標記不能當成 elapsed"


def test_ingest_setup_refuses_unrepresentable_preset_paths():
    src = _svelte("IngestSetup.svelte")
    assert "PRESET_UNSAFE" in src
    i = src.index("async function savePreset")
    body = src[i:i + 1200]
    assert "PRESET_UNSAFE.test(p.path)" in body, \
        "寫回是整串重寫 —— 既有的損壞會被固化，所以寫之前要先擋"
