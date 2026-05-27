# Auth Tokens 1b — 42-route migration + admin.py + bootstrap — Codex Handover 執行計畫

## Phase 0: Scope 邊界

### Scope 白名單

| 檔案 | 動作 | LOC 預估 |
|---|---|---|
| `server.py` | Edit — 32 個 route 加 `Depends(require_scopes(...))` + 啟動 event 加 bootstrap logic + 4 個 admin endpoints | +80 |
| `admin.py` | **新檔** — token CRUD service functions（被 server.py admin endpoints call）| ~120 |
| `config.py` | Edit — 加 `ARKIV_ADMIN_BOOTSTRAP_TOKEN` env var read | +5 |
| `tests/test_auth.py` | Edit（A.1a 已建）— 補 admin endpoint tests + bootstrap test + 抽樣 5-7 個既有 endpoint scope enforcement test | +120 |

### Scope 禁區（不可動）

```
auth.py                  ← A.1a 已 ship 穩定，**不准動 SCOPES enum / verify_token / require_scopes 邏輯**；只可 import
db.py                    ← A.1a 已加 access_tokens schema，**不准動既有 schema**
arkiv_token.py           ← A.1c 才新建
README.md / README.zh-TW.md / CHANGELOG.md   ← A.1c 才動
requirements.txt / requirements-cuda.txt    ← A.1a 已加 nanoid
vision.py / transcribe.py / vectordb.py / federation.py / ingest.py / health.py / mhl.py / offload.py / camera_report.py / codec.py / embed.py / frames.py
tests/* 既有 test 檔（test_offload.py / test_mhl.py / test_camera_report.py / test_server.py / 其他）— 只動 tests/test_auth.py
src-tauri/* / .github/workflows/* / .claude/settings.local.json
docs/* 除本 handover 對應的 CODEX_RESULT.md
```

### Commit 邊界

同 A.1a — Codex apply + pytest + CODEX_RESULT.md 寫完即停。CC commit + push。

---

## Context

A.1a 已建好 `auth.py` middleware + DB schema + 基礎 5 case test。A.1b 是把 middleware **套到** server.py 42 個既有 route + 加 admin token CRUD endpoint 給 Hevin manage per-machine tokens + bootstrap 機制讓首次啟動可開門。

**依賴 A.1a 已 ship**：`auth.py` 的 `SCOPES`, `verify_token`, `require_scopes`, `hash_token`, `new_token_id`, `new_raw_token` 必須已 import-able；DB `access_tokens` + `access_token_scopes` table 必須已存在。如 A.1a audit fail 重派，A.1b 暫停等 A.1a clear。

預期成果：
- 32 個既有 route 全部需 valid Bearer token + 對應 scope 才能 200
- 4 個新 admin route 用 admin scope 管理 token
- 啟動時若 access_tokens 表空 → 從 `ARKIV_ADMIN_BOOTSTRAP_TOKEN` env 讀 raw + 建 admin token + 印 warning
- 既有 tests/test_offload.py / test_mhl.py / test_camera_report.py 等 不受影響（fixtures 自動含 admin token，per test_auth.py infra）

---

## Repo / Constraints

同 A.1a — Python 3.9 相容、不准動禁區檔、不准 git commit。

---

## 執行順序與依賴

```
Step 1: config.py 加 ARKIV_ADMIN_BOOTSTRAP_TOKEN env read
    ↓
Step 2: admin.py 新檔（token CRUD service 函式）
    ↓
Step 3: server.py 加 startup event handler 跑 bootstrap
    ↓
Step 4: server.py 32 個既有 route 加 Depends(require_scopes(...))（per mapping table）
    ↓
Step 5: server.py 加 4 個 /api/admin/tokens endpoint
    ↓
Step 6: tests/test_auth.py 補 admin + bootstrap + 抽樣 endpoint scope test
    ↓
Step 7: pytest tests/ -v 既有不增 fail + 新 test 全綠
    ↓
Step 8: CODEX_RESULT.md
```

---

## 逐步驟實作細節

### Step 1: `config.py` 加 env

**檔案**：`C:/Users/user/.arkiv/config.py`

**改動**：在既有 env var 區段（line ~85-90，跟 OLLAMA_URL 附近）加：

```python
# Auth bootstrap (per docs/auth-tokens-1b-handover.md)
ARKIV_ADMIN_BOOTSTRAP_TOKEN = os.getenv("ARKIV_ADMIN_BOOTSTRAP_TOKEN", "").strip()
```

**注意**：
- 只讀，不驗證（驗證在 bootstrap logic 時做）
- 不寫 default — 空字串 = 不 bootstrap
- 不動既有 OLLAMA / WHISPER / PROJECT_ROOT 配置

### Step 2: 新建 `admin.py`

**檔案**：`C:/Users/user/.arkiv/admin.py`（新檔，~120 行）

**完整骨架**：

```python
"""Admin functions for access token management.

Backend service functions called by /api/admin/tokens endpoints in server.py.
All admin endpoints require 'admin' scope (enforced at server.py level).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from auth import SCOPES, hash_token, new_raw_token, new_token_id
from db import get_conn


# ============================================================
# Token CRUD service functions
# ============================================================

def create_token(
    name: str,
    scopes: List[str],
    description: Optional[str] = None,
    expires_in_days: Optional[int] = None,
    allowed_ips: Optional[List[str]] = None,
) -> dict:
    """Create new access token. Returns dict with raw token (caller MUST surface immediately,
    cannot be retrieved later — only hash stored).

    Raises ValueError if any scope unknown or scopes list empty.
    """
    if not scopes:
        raise ValueError("scopes list cannot be empty")
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise ValueError(f"Unknown scopes: {unknown}. Valid: {sorted(SCOPES)}")

    raw = new_raw_token()
    tid = new_token_id()
    expires_at = None
    if expires_in_days:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    allowed_ips_json = json.dumps(allowed_ips or ["*"])

    with get_conn() as cn:
        cn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, expires_at, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, name, description, hash_token(raw), expires_at, allowed_ips_json),
        )
        for s in scopes:
            cn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (tid, s),
            )

    return {
        "id": tid,
        "name": name,
        "scopes": sorted(scopes),
        "expires_at": expires_at,
        "allowed_ips": allowed_ips or ["*"],
        "raw_token": raw,  # MUST surface to user immediately
    }


def list_tokens() -> List[dict]:
    """List all tokens (without raw, since not stored)."""
    with get_conn() as cn:
        rows = cn.execute(
            "SELECT id, name, description, expires_at, allowed_ips_json, "
            "last_used_at, last_used_ip, last_used_user_agent, created_at "
            "FROM access_tokens ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for r in rows:
            scope_rows = cn.execute(
                "SELECT scope FROM access_token_scopes WHERE token_id = ?",
                (r["id"],),
            ).fetchall()
            result.append({
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "scopes": sorted(s["scope"] for s in scope_rows),
                "expires_at": r["expires_at"],
                "allowed_ips": json.loads(r["allowed_ips_json"]),
                "last_used_at": r["last_used_at"],
                "last_used_ip": r["last_used_ip"],
                "last_used_user_agent": r["last_used_user_agent"],
                "created_at": r["created_at"],
            })
    return result


def get_token(token_id: str) -> Optional[dict]:
    """Get single token by id. None if not found."""
    with get_conn() as cn:
        row = cn.execute(
            "SELECT id, name, description, expires_at, allowed_ips_json, "
            "last_used_at, last_used_ip, last_used_user_agent, created_at "
            "FROM access_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        if not row:
            return None
        scope_rows = cn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ?",
            (token_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "scopes": sorted(s["scope"] for s in scope_rows),
            "expires_at": row["expires_at"],
            "allowed_ips": json.loads(row["allowed_ips_json"]),
            "last_used_at": row["last_used_at"],
            "last_used_ip": row["last_used_ip"],
            "last_used_user_agent": row["last_used_user_agent"],
            "created_at": row["created_at"],
        }


def revoke_token(token_id: str) -> bool:
    """Delete token by id. Returns True if deleted, False if not found.

    ON DELETE CASCADE handles access_token_scopes cleanup.
    """
    with get_conn() as cn:
        cur = cn.execute("DELETE FROM access_tokens WHERE id = ?", (token_id,))
        return cur.rowcount > 0


# ============================================================
# Bootstrap (called from server.py startup event)
# ============================================================

def bootstrap_admin_token_if_empty() -> Optional[str]:
    """If access_tokens table is empty, use ARKIV_ADMIN_BOOTSTRAP_TOKEN env to seed.

    Returns: raw bootstrap token if seeded (caller prints warning), None if not seeded.
    """
    from config import ARKIV_ADMIN_BOOTSTRAP_TOKEN

    with get_conn() as cn:
        count = cn.execute("SELECT COUNT(*) AS c FROM access_tokens").fetchone()["c"]
        if count > 0:
            return None  # already have tokens, no bootstrap needed

    if not ARKIV_ADMIN_BOOTSTRAP_TOKEN:
        return None  # no env, user must set it before starting server

    # Seed
    tid = new_token_id()
    with get_conn() as cn:
        cn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                tid,
                "bootstrap",
                "Admin token from ARKIV_ADMIN_BOOTSTRAP_TOKEN env (delete after creating per-machine tokens)",
                hash_token(ARKIV_ADMIN_BOOTSTRAP_TOKEN),
                '["*"]',
            ),
        )
        cn.execute(
            "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
            (tid, "admin"),
        )

    return ARKIV_ADMIN_BOOTSTRAP_TOKEN
```

### Step 3: `server.py` 加 startup event

**檔案**：`C:/Users/user/.arkiv/server.py`

**改動**：在 `app = FastAPI(...)` 之後（line ~60，CORS 中間件附近）加：

```python
from auth import require_scopes  # 既有 import 區段
from admin import bootstrap_admin_token_if_empty  # 新增


@app.on_event("startup")
def _bootstrap_admin_token():
    """Seed admin token from ARKIV_ADMIN_BOOTSTRAP_TOKEN env if access_tokens empty."""
    raw = bootstrap_admin_token_if_empty()
    if raw:
        print("=" * 70)
        print("[BOOTSTRAP] Admin token seeded from ARKIV_ADMIN_BOOTSTRAP_TOKEN env.")
        print("            Token name: 'bootstrap', scope: ['admin']")
        print("            Generate per-machine tokens via POST /api/admin/tokens,")
        print("            then unset ARKIV_ADMIN_BOOTSTRAP_TOKEN + revoke 'bootstrap'.")
        print("=" * 70)
    else:
        # Check if no tokens at all and no env → instruct user
        from db import get_conn
        with get_conn() as cn:
            count = cn.execute("SELECT COUNT(*) AS c FROM access_tokens").fetchone()["c"]
        if count == 0:
            print("=" * 70)
            print("[BOOTSTRAP] No access tokens in DB and ARKIV_ADMIN_BOOTSTRAP_TOKEN unset.")
            print("            API endpoints will return 401. To bootstrap:")
            print("              1. export ARKIV_ADMIN_BOOTSTRAP_TOKEN=$(openssl rand -base64 32)")
            print("              2. restart server (it will seed admin token)")
            print("              3. POST /api/admin/tokens to create per-machine tokens")
            print("=" * 70)
```

### Step 4: `server.py` 32 routes 加 scope

**Pattern**（per A.1a `require_scopes` factory）：

```python
# Before
@app.get("/api/media")
def list_media(q: str = "", limit: int = 50): ...

# After
@app.get("/api/media")
def list_media(q: str = "", limit: int = 50, _tok = Depends(require_scopes("videos_read"))): ...
```

**完整 mapping table**（Codex 照此 32 條一一對應；找不到對應路由則在 CODEX_RESULT.md REVIEW 提出）：

| Route | HTTP | Scope |
|---|---|---|
| `/api/search/all` | GET | `videos_read` |
| `/api/projects` | GET | `projects_read` |
| `/api/projects` | POST | `projects_write` |
| `/api/projects/{name}` | DELETE | `projects_write` |
| `/api/projects/sync` | POST | `projects_write` |
| `/api/projects/health` | GET | `projects_read` |
| `/api/media` | GET | `videos_read` |
| `/api/media/pool` | GET | `videos_read` |
| `/api/media/position/{media_id}` | GET | `videos_read` |
| `/api/media/{media_id}` | GET | `videos_read` |
| `/api/media/{media_id}/rating` | PATCH | `videos_write` |
| `/api/media/{media_id}/tags` | GET | `videos_read` |
| `/api/media/{media_id}/tags` | POST | `videos_write` |
| `/api/media/{media_id}/tags/{name}` | DELETE | `videos_write` |
| `/api/media/{media_id}/waveform` | GET | `media_read` |
| `/api/media/{media_id}/scenes` | GET | `media_read` |
| `/api/media/{media_id}/export/{fmt}` | GET | `media_read` |
| `/api/media/{media_id}/remotion-props` | GET | `media_read` |
| `/api/export/metadata-csv` | GET | `media_read` |
| `/api/export/metadata-csv-to` | POST | `media_read` |
| `/api/ingest/scan` | POST | `ingest_write` |
| `/api/ingest` | POST | `ingest_write` |
| `/api/media/{media_id}/retranscribe` | POST | `ingest_write` |
| `/api/media/{media_id}/retry-vision` | POST | `ingest_write` |
| `/api/media/{media_id}/reingest` | POST | `ingest_write` |
| `/api/cache/info` | GET | `videos_read` |
| `/api/cache/clear` | POST | `videos_write` |
| `/api/stats` | GET | `videos_read` |
| `/api/tags` | GET | `videos_read` |
| `/api/duration-by-lang` | GET | `videos_read` |
| `/api/size-by-ext` | GET | `videos_read` |
| `/api/media/{media_id}/export-to` | POST | `media_read` |

（若 Codex 掃到 server.py 有額外 route 不在此 mapping，REVIEW 提出，CC 決定 scope）

### Step 5: `server.py` 加 4 個 admin endpoints

加在 server.py 末尾（既有 route 之後、`if __name__ == "__main__":` 之前）：

```python
# ============================================================
# Admin endpoints — token management
# ============================================================
from pydantic import BaseModel
from typing import List as _List, Optional as _Optional
import admin as _admin


class CreateTokenRequest(BaseModel):
    name: str
    scopes: _List[str]
    description: _Optional[str] = None
    expires_in_days: _Optional[int] = None
    allowed_ips: _Optional[_List[str]] = None


@app.post("/api/admin/tokens")
def admin_create_token(req: CreateTokenRequest, _tok = Depends(require_scopes("admin"))):
    """Create new access token. Returns raw token ONCE (cannot be retrieved later)."""
    try:
        return _admin.create_token(
            name=req.name,
            scopes=req.scopes,
            description=req.description,
            expires_in_days=req.expires_in_days,
            allowed_ips=req.allowed_ips,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/admin/tokens")
def admin_list_tokens(_tok = Depends(require_scopes("admin"))):
    """List all tokens (without raw)."""
    return {"tokens": _admin.list_tokens()}


@app.get("/api/admin/tokens/{token_id}")
def admin_get_token(token_id: str, _tok = Depends(require_scopes("admin"))):
    """Get single token detail."""
    t = _admin.get_token(token_id)
    if not t:
        raise HTTPException(404, "Token not found")
    return t


@app.delete("/api/admin/tokens/{token_id}")
def admin_revoke_token(token_id: str, _tok = Depends(require_scopes("admin"))):
    """Revoke (delete) token."""
    if not _admin.revoke_token(token_id):
        raise HTTPException(404, "Token not found")
    return {"ok": True, "deleted": token_id}
```

### Step 6: `tests/test_auth.py` 補

**改動**：在 A.1a 既有 5 case 之後加：

1. **Admin create / list / get / revoke 流程 e2e**
2. **Bootstrap test**：set env → init empty DB → 跑 bootstrap → 驗 admin token 可用
3. **Bootstrap test 2**：DB 已有 token → bootstrap no-op
4. **Bootstrap test 3**：env 空 + DB 空 → bootstrap return None, server 印 instruction
5. **Endpoint scope sample**：對 5 個既有 route（GET /api/media / POST /api/admin/tokens / POST /api/ingest / GET /api/stats / GET /api/export/metadata-csv）各驗：(a) 無 token → 401 (b) 錯 scope → 403 (c) 對 scope → 200（或 200/404 OK，看 fixture data）

```python
# 範例 — admin create flow
def test_admin_create_token_e2e(tmp_db, monkeypatch):
    import importlib, auth, admin
    importlib.reload(auth); importlib.reload(admin)

    # Bootstrap an admin token first
    monkeypatch.setenv("ARKIV_ADMIN_BOOTSTRAP_TOKEN", "test-admin-bootstrap-12345")
    import config; importlib.reload(config); importlib.reload(admin)
    raw = admin.bootstrap_admin_token_if_empty()
    assert raw == "test-admin-bootstrap-12345"

    # Now use admin token to create per-machine token via /api/admin/tokens
    from server import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.post(
        "/api/admin/tokens",
        headers={"Authorization": f"Bearer {raw}"},
        json={"name": "PC-dev", "scopes": ["videos_read", "videos_write"], "expires_in_days": 90},
    )
    assert r.status_code == 200
    body = r.json()
    assert "raw_token" in body
    assert body["scopes"] == ["videos_read", "videos_write"]
    assert body["name"] == "PC-dev"
```

### Step 7: pytest

```bash
cd C:/Users/user/.arkiv
python -m pytest tests/test_auth.py -v       # 預期 全綠
python -m pytest tests/ -v                   # 預期 既有不增 fail
```

**重要**：既有 test_server.py 等可能因為 endpoint 加了 auth 而炸（之前可以裸 call /api/media，現在需 token）。**請 Codex 修補方式**：

- 不動 既有 test_server.py / test_offload.py / test_mhl.py 等
- 在 `tests/conftest.py` **新增** fixture `auth_client`，產生帶 admin token 的 TestClient — 但**這違反「不動 conftest.py」規則**
- 解法：在 tests/test_auth.py 內驗 endpoint scope，**既有 test_server.py 等預期會有些 fail（pre-existing fail set 變大）**，這是 expected behavior，**不是 Codex 的鍋**。Codex 只負責 test_auth.py 全綠。

**Codex CODEX_RESULT.md 必須註明**：「既有 tests/test_server.py 等因 endpoint 加 auth 預期有額外 fail，這是 A.1b 必要副作用，後續 A.1c 或新 PR 修 conftest 加 auth_client fixture 來補。」

### Step 8: CODEX_RESULT.md

同 A.1a 格式，含完整 pytest 輸出 + 自審 checklist + REVIEW（含上述既有 test 副作用說明）。

---

## 不改的檔案

per Phase 0 禁區清單。

---

## 測試計畫

| # | 測試名 | 驗證什麼 |
|---|---|---|
| 6 | `test_admin_create_token_e2e` | bootstrap → 用 admin token call /api/admin/tokens POST → 拿到 raw_token + scopes |
| 7 | `test_admin_list_tokens` | bootstrap → /api/admin/tokens GET → 含 bootstrap token + 新建的 |
| 8 | `test_admin_get_token` | GET /api/admin/tokens/{id} → 對到正確 token 不含 raw |
| 9 | `test_admin_revoke_token` | DELETE /api/admin/tokens/{id} → 200 + 後續 GET → 404 |
| 10 | `test_bootstrap_seeds_admin_when_db_empty_and_env_set` | env set + DB 空 → bootstrap_admin_token_if_empty 回 raw |
| 11 | `test_bootstrap_noop_when_tokens_exist` | DB 已有 token → bootstrap return None |
| 12 | `test_bootstrap_noop_when_env_empty` | env 空 + DB 空 → bootstrap return None |
| 13 | `test_admin_endpoint_rejects_non_admin_scope` | videos_read token call /api/admin/tokens → 403 |
| 14 | `test_route_sample_videos_read_scope_enforced` | GET /api/media without token → 401; with videos_read → 200/404 |
| 15 | `test_route_sample_ingest_write_scope_enforced` | POST /api/ingest without ingest_write → 403 |
| 16 | `test_route_sample_media_read_scope_enforced` | GET /api/media/{id}/scenes scope check |

---

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| 32 routes 漏漏改（少改幾個 → 對應 endpoint 仍裸跑無 auth） | Codex 跑 grep `@app\.(get\|post\|patch\|delete)` 對 server.py 全 route count，跟 mapping table 32 條對比；不符 raise in REVIEW |
| Scope mapping 寫錯（GET 寫 write 或顛倒） | Mapping table 明確、Codex 不准 deviate；CC audit 逐 route 對 |
| 既有 test_server.py / test_offload.py 等大量 fail | Expected — A.1b 在 REVIEW 註明、A.1c 或新 PR 加 conftest auth_client fixture 修 |
| Bootstrap event 在 pytest 重複跑導致 race | startup event 只在 server start 跑；pytest TestClient(`app`) 可能 trigger 多次，但 bootstrap 是 idempotent（DB 非空就 no-op）|
| `import admin` 跟 `import db` 循環（admin import db, server import admin, db 沒 import 三者） | admin 依賴 auth + db，server 依賴 admin + auth — 線性，無循環 |

---

## CC-Fallback Brief（若 Codex 撞 limit / fail）

CC 可直接接手執行（預估 CC 時間：~2-3 hr）：

1. **Step 1** — `config.py` 加一行 `ARKIV_ADMIN_BOOTSTRAP_TOKEN`（2 min）
2. **Step 2** — `admin.py` 新檔，照完整骨架抄（30-45 min）
3. **Step 3** — `server.py` 加 startup event handler + import（15 min）
4. **Step 4** — `server.py` 32 routes 加 Depends — 用 sed/Edit 跑 32 次 + 對 mapping table 一致（45 min — 最累，逐 route 改）
5. **Step 5** — `server.py` 加 4 admin endpoints（15 min）
6. **Step 6** — `tests/test_auth.py` 補 11 case（45 min — 用 A.1a 既有 5 case 為 base）
7. **Step 7** — `pytest tests/test_auth.py -v`（5 min）
8. **Step 8** — 寫 dev-log + commit + push（15 min）

**CC takeover 觸發**：Codex 同 sub-dispatch 連續 3 次 fail（per `codex-handover` SKILL rework_limits 三振）。

---

## 交付格式

同 A.1a — apply + CODEX_RESULT.md 寫完即停，CC 接手。

---

## 後續

A.1b CC audit + commit 通過後：
- A.1c 開始：CC 寫 `docs/auth-tokens-1c-handover.md`（arkiv_token.py CLI + 補 conftest auth_client fixture + README/CHANGELOG）
- A.1c 通過 → CC commit + tag v0.4.1 + push + Release
