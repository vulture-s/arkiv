"""Ingest 來源捷徑 —— 而它存在的理由是瀏覽器根本沒有選資料夾的能力。

`canPickFolder()` 要 `window.__TAURI__`，所以 `⋯` 那顆按鈕**只有桌面 App 會渲染**。
在瀏覽器開 :8501 的人，每次都得手打
`/Volumes/home/影片專案/商業案/李多慧` 這種長路徑。

解析刻意寬鬆：這個值是人手打進設定欄位的，一個多餘的分號不可以讓匯入頁開不起來。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings  # noqa: E402


def _parse(monkeypatch, raw):
    monkeypatch.setattr(settings, "for_project", lambda *a, **k: raw)
    return settings.source_preset_list()


def test_label_and_path_pairs(monkeypatch):
    got = _parse(monkeypatch, "CARD|/Volumes/CARD/DCIM; NAS|/Volumes/home/影片專案")
    assert got == [{"label": "CARD", "path": "/Volumes/CARD/DCIM"},
                   {"label": "NAS", "path": "/Volumes/home/影片專案"}]


def test_bare_path_gets_its_folder_name_as_label(monkeypatch):
    got = _parse(monkeypatch, "/Volumes/home/影片專案/李多慧")
    assert got == [{"label": "李多慧", "path": "/Volumes/home/影片專案/李多慧"}]


def test_malformed_input_drops_entries_instead_of_raising(monkeypatch):
    """🔴 一個多打的分號不可以讓整頁掛掉 —— 少一個捷徑救得回來，開不起來的畫面救不回來。"""
    for raw in ("", ";", ";;;", "   ", "|", "A|", "|/x;B|/y"):
        got = _parse(monkeypatch, raw)
        assert isinstance(got, list)
        for item in got:
            assert item["path"] and item["label"]


def test_duplicate_paths_collapse(monkeypatch):
    got = _parse(monkeypatch, "X|/same; Y|/same")
    assert len(got) == 1


def test_empty_setting_is_empty_list_not_none(monkeypatch):
    """UI 用 `{#each}` 走它 —— None 會炸，[] 不會。"""
    assert _parse(monkeypatch, "") == []
    assert _parse(monkeypatch, None) == []


def test_engines_endpoint_exposes_the_presets(fastapi_client):
    r = fastapi_client.get("/api/ingest/engines")
    assert r.status_code == 200, r.text
    assert "source_presets" in r.json(), "前端只讀這個端點，沒帶就等於沒做"
    assert isinstance(r.json()["source_presets"], list)


def test_setting_is_registered_in_the_schema(monkeypatch):
    """沒進 schema 就 PUT 不進去 —— 而 UI 的『＋』就是靠 PUT 存的。"""
    assert "ingest.source_presets" in settings.SETTINGS_SCHEMA
    assert settings.SETTINGS_SCHEMA["ingest.source_presets"]["type"] == "str"
    # accessor 必須穿透（薄的），解析是另一個函式 —— 這是 settings 的體例，
    # 由 test_settings_g5 的覆蓋表逐 key 驗 project scope
    monkeypatch.setattr(settings, "for_project", lambda *a, **k: "A|/x")
    assert settings.ingest_source_presets() == "A|/x"
