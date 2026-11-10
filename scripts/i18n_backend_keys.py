"""List every user-facing API string the English UI must be able to translate (#634).

The zh-TW text is the key (frontend/src/lib/i18n.js is gettext-style), so the
list is extracted from the code rather than maintained by hand:

  * ApiError(status, "<template>", ...)            — formatted, sent with detail_key
  * api_msg("<template>", ...)                     — formatted, sent with message_key
  * HTTPException(status, "<fixed text>")          — the detail text itself is the key
  * {"message"/"error": "<fixed text>"} in a JSON body
  * module-level *_KEY / *_MESSAGE constants, and the second argument of
    _user_dir_or_400 (templates passed through a variable)
  * EXTRA below — fixed sentences returned from a helper (no structural marker)

`remaining` counts the CJK details still built by f-string / .format / +
inside a plain HTTPException — the frontend cannot translate those yet.

    python scripts/i18n_backend_keys.py           # print
    python scripts/i18n_backend_keys.py --write   # refresh the committed JSON
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "src" / "lib" / "i18n" / "backend-keys.json"
SOURCES = ["reqopts.py", "webguard.py", "health.py", "settings.py"] + sorted(
    str(p.relative_to(ROOT)) for p in (ROOT / "routers").glob("*.py"))
CJK = re.compile(r"[㐀-鿿]")
MSG_FIELDS = ("message", "error")

# Fixed sentences a helper returns into a message field (routers/setup.py
# _friendly → item["message"]; health._file_manager_name / the action verbs →
# path_access params, which the frontend translates as words).
EXTRA = [
    "找不到這個模型名稱——檢查設定裡的模型名稱",
    "連不到下載伺服器——確認網路後再試一次",
    "磁碟空間不足",
    "檔案總管",
    "檔案管理員",
    "匯入",
    "匯出",
    "轉存",
    "加入",
]


def _const(n):
    """The string value of a str constant or implicit concatenation, else None."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        a, b = _const(n.left), _const(n.right)
        if a is not None and b is not None:
            return a + b
    return None


def _has_cjk(n):
    return any(isinstance(x, ast.Constant) and isinstance(x.value, str) and CJK.search(x.value)
               for x in ast.walk(n))


def scan():
    keys, remaining = set(EXTRA), {}
    for rel in SOURCES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                name = n.targets[0].id
                v = _const(n.value)
                if v and CJK.search(v) and (name.endswith("_KEY") or name.endswith("_MESSAGE")):
                    keys.add(v)
            if isinstance(n, ast.Call):
                fn = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                args = list(n.args) + [k.value for k in n.keywords if k.arg == "detail"]
                if fn == "ApiError" and len(n.args) >= 2:
                    v = _const(n.args[1])
                    if v:
                        keys.add(v)
                elif fn == "api_msg" and n.args:
                    v = _const(n.args[0])
                    if v:
                        keys.add(v)
                elif fn == "_user_dir_or_400" and len(n.args) >= 2:
                    v = _const(n.args[1])
                    if v:
                        keys.add(v)
                elif fn == "HTTPException":
                    detail = ([k.value for k in n.keywords if k.arg == "detail"] or n.args[1:2] or [None])[0]
                    if detail is None or not _has_cjk(detail):
                        continue
                    v = _const(detail)
                    if v is not None:
                        keys.add(v)
                    else:
                        remaining[rel] = remaining.get(rel, 0) + 1
                del args
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value in MSG_FIELDS and v is not None:
                        c = _const(v)
                        if c and CJK.search(c):
                            keys.add(c)
    return {"keys": sorted(k for k in keys if CJK.search(k)),
            "remaining": dict(sorted(remaining.items()))}


def render(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    data = scan()
    if "--write" in sys.argv:
        OUT.write_text(render(data), encoding="utf-8")
        print("wrote {0} keys to {1}".format(len(data["keys"]), OUT.relative_to(ROOT)))
    else:
        sys.stdout.write(render(data))
