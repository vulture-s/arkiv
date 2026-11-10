"""#634: backend API text the English UI translates.

* frontend/src/lib/i18n/backend-keys.json must match what the code can emit
  (regenerate: python scripts/i18n_backend_keys.py --write); the frontend test
  then requires an English entry for every key.
* ApiError / api_msg keep the formatted Chinese text exactly as before and only
  ADD the key + params.
"""
import importlib.util
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import apimsg
import health

ROOT = Path(__file__).resolve().parent.parent


def _scanner():
    spec = importlib.util.spec_from_file_location("i18n_backend_keys", ROOT / "scripts" / "i18n_backend_keys.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backend_keys_json_is_in_sync_with_the_code():
    mod = _scanner()
    committed = mod.OUT.read_text(encoding="utf-8")
    assert committed == mod.render(mod.scan()), (
        "backend-keys.json is stale — run `python scripts/i18n_backend_keys.py --write` "
        "and add English for any new key in frontend/src/lib/i18n/en/api.js")


def test_extra_keys_still_exist_in_the_source():
    mod = _scanner()
    src = "".join((ROOT / f).read_text(encoding="utf-8") for f in mod.SOURCES)
    gone = [k for k in mod.EXTRA if '"{0}"'.format(k) not in src]
    assert gone == [], "EXTRA lists sentences the code no longer has: {0}".format(gone)


def test_no_formatted_chinese_detail_regresses():
    # Formatted CJK inside a plain HTTPException cannot be translated; new ones
    # must go through ApiError. (Counted in backend-keys.json "remaining".)
    assert _scanner().scan()["remaining"] == {}


def _app():
    app = FastAPI()
    apimsg.install(app)

    @app.get("/e")
    def e():
        raise apimsg.ApiError(404, "找不到媒體檔案：{path}", path=Path("/v/x.mov"))

    @app.get("/h")
    def h():
        raise apimsg.ApiError(409, "已有匯入任務進行中，請稍候", headers={"Retry-After": "5"})

    @app.get("/m")
    def m():
        return {"ok": True, **apimsg.api_msg("開始生成 {n} 個 proxy（背景執行）", n=3)}

    return TestClient(app)


def test_api_error_keeps_detail_and_adds_key_params():
    r = _app().get("/e")
    assert r.status_code == 404
    assert r.json() == {
        "detail": "找不到媒體檔案：/v/x.mov",
        "detail_key": "找不到媒體檔案：{path}",
        "detail_params": {"path": "/v/x.mov"},
    }


def test_api_error_keeps_headers():
    r = _app().get("/h")
    assert r.status_code == 409 and r.headers["retry-after"] == "5"
    assert r.json()["detail"] == "已有匯入任務進行中，請稍候"


def test_api_msg_shape():
    assert _app().get("/m").json() == {
        "ok": True,
        "message": "開始生成 3 個 proxy（背景執行）",
        "message_key": "開始生成 {n} 個 proxy（背景執行）",
        "message_params": {"n": 3},
    }


def test_api_error_without_handler_degrades_to_plain_detail():
    # A router mounted on a bare app (tests, MCP) still gets the old body.
    app = FastAPI()

    @app.get("/e")
    def e():
        raise apimsg.ApiError(400, "非法路徑：{path}", path="../x")

    assert TestClient(app).get("/e").json() == {"detail": "非法路徑：../x"}


def test_path_access_error_matches_path_access_message():
    exc = OSError(13, "Permission denied")
    err = health.path_access_error(400, exc, "匯出")
    assert err.detail == health.path_access_message(exc, "匯出")
    assert err.key == health.PATH_ACCESS_KEY
    assert err.params["action"] == "匯出" and err.params["reason"] == "Permission denied"


def test_only_marked_params_are_flagged_translatable():
    # User data (a project literally named 匯出) carries no tr marker, so the UI
    # shows it verbatim; the path-access words are marked.
    plain = apimsg.ApiError(400, "找不到目的專案：{dest}", dest="匯出")
    assert plain.tr_params == []
    pa = health.path_access_error(400, OSError(13, "denied"), "匯出")
    assert pa.tr_params == ["fm", "action"]
    out = apimsg.api_msg("有 {n} 個模型下載失敗", n=2)
    assert "message_tr_params" not in out
    import pytest
    with pytest.raises(ValueError):
        apimsg.ApiError(400, "x{a}", tr_params=("b",), a=1)


def test_path_access_error_body_marks_word_params():
    app = FastAPI()
    apimsg.install(app)

    @app.get("/p")
    def p():
        raise health.path_access_error(400, OSError(13, "denied"), "加入")

    body = TestClient(app).get("/p").json()
    assert body["detail_tr_params"] == ["fm", "action"]
    assert body["detail_key"].format(**body["detail_params"]) == body["detail"]
