"""User-facing API text that the English UI can translate (#634).

The frontend i18n (frontend/src/lib/i18n.js, #581) is gettext-style: the zh-TW
source string IS the key, and frontend/src/lib/i18n/en/*.js is the only English
dictionary. The backend reuses that instead of growing a second dictionary:

  * a FIXED sentence needs nothing here — the frontend looks the `detail` /
    `message` text itself up in the dictionary;
  * a sentence with VALUES in it (a path, a count, an OS reason) cannot be looked
    up once formatted, so it is raised / returned through this module, which keeps
    the formatted Chinese text exactly as before (old clients, tests and MCP
    callers see no change) and ADDS the template and its params next to it:

      raise ApiError(404, "找不到媒體檔案：{path}", path=p)
        → {"detail": "找不到媒體檔案：/x.mov",
           "detail_key": "找不到媒體檔案：{path}", "detail_params": {"path": "/x.mov"}}

      return {"ok": True, **api_msg("開始生成 {n} 個 proxy（背景執行）", n=3)}
        → {"message": "開始生成 3 個 proxy（背景執行）",
           "message_key": "開始生成 {n} 個 proxy（背景執行）", "message_params": {"n": 3}}

Placeholders are `{name}` — the same syntax translate() fills on the frontend.
Every key the backend can emit is listed in frontend/src/lib/i18n/backend-keys.json
(regenerate with `python scripts/i18n_backend_keys.py --write`); the Python test
keeps that list in sync with the code and the frontend test requires an English
entry for each one.
"""
from typing import Any, Dict, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    # JSON-safe and stable: numbers stay numbers (the UI may format them), the
    # rest becomes the same text str.format would have printed.
    return {k: (v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
            for k, v in params.items()}


class ApiError(HTTPException):
    """HTTPException whose `detail` is a translatable template + params."""

    def __init__(self, status_code: int, key: str, headers: Optional[Dict[str, str]] = None,
                 **params: Any) -> None:
        self.key = key
        self.params = _clean(params)
        super().__init__(status_code=status_code, detail=key.format(**params), headers=headers)


def api_msg(key: str, field: str = "message", **params: Any) -> Dict[str, Any]:
    """`{field, field_key, field_params}` for a JSON body (spread it in)."""
    return {field: key.format(**params), field + "_key": key, field + "_params": _clean(params)}


async def api_error_handler(request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.detail, "detail_key": exc.key, "detail_params": exc.params},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


def install(app) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
