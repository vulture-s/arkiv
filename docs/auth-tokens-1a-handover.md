# Auth Tokens 1a — DB schema + auth.py middleware + tests 基礎 — Codex Handover 執行計畫

## Phase 0: Scope 邊界（強制，超出即視為越界）

### Scope 白名單（**Codex 只可動**）

| 檔案 | 動作 | LOC 預估 |
|---|---|---|
| `db.py` | Edit — 在 `init_db()` 加 2 個 `CREATE TABLE IF NOT EXISTS` 區段 | +30 |
| `auth.py` | **新檔** — `verify_token()` + `require_scopes()` factory + `SCOPES` enum + nanoid helper | ~120 |
| `tests/test_auth.py` | **新檔** — pytest 5 case 對 auth middleware 基礎驗 | ~100 |
| `requirements.txt` | Edit — 加 `nanoid>=2.0.0` 一行 | +1 |
| `requirements-cuda.txt` | Edit — 同上 | +1 |

**任何超出此白名單的檔案修改 = 越界，必須在 `CODEX_RESULT.md` `## REVIEW` 提出，由 CC 決定。**

### Scope 禁區（**Codex 不可動**）

```
server.py                ← A.1b 才動 42-route migration
admin.py                 ← A.1b 才新建
arkiv_token.py           ← A.1c 才新建
config.py                ← A.1b 才加 ARKIV_ADMIN_BOOTSTRAP_TOKEN
README.md / README.zh-TW.md / CHANGELOG.md   ← A.1c 才動
vision.py / transcribe.py / vectordb.py / federation.py / ingest.py / health.py / mhl.py / offload.py / camera_report.py / codec.py / embed.py / frames.py
tests/* 既有 test 檔（test_offload.py / test_mhl.py / test_camera_report.py / test_server.py / 其他）— 只新增 tests/test_auth.py
src-tauri/*
.github/workflows/*
.claude/settings.local.json
docs/* 除本 handover 對應的 CODEX_RESULT.md 之外
```

### Commit 邊界

**Codex 不負責 commit**。Codex 完成 apply + pytest 全綠 + `## Codex 自審 Checklist` 全勾後：
1. 寫 `CODEX_RESULT.md` 含完整 pytest 輸出 + 逐項 checklist 標記
2. **停手**——不做 `git add` / `git commit` / `git push`
3. CC 接手 audit + commit + push

如 Codex sandbox 撞 `.git/index.lock`（per `reference_codex_sandbox_no_git_index_write`）：跳過所有 git 操作，純檔案 apply 完即停。

---

## Context

per `~/.claude/plans/roadmap-cuddly-goblet.md` Feature A.1a，arkiv 現況 42 routes 零 auth (`server.py:58` CORS-only)，5-machine fleet (PC / M2 Max / mini-relay / Chloe / NAS-OpenClaw) 共用 arkiv backend 但 read/write 權限應分。借鏡 Edit Mind `apps/background-jobs/src/middleware/accessTokenAuth.ts` + Prisma `AccessToken` model pattern。

A.1a 是三段 sub-dispatch 中的第一段：建 DB schema + middleware 核心 + 基礎 test。後續 A.1b 套到 42 routes、A.1c 加 CLI。

預期成果：`auth.py` 模組可獨立 import，`verify_token()` 對 valid Bearer token 回傳含 scopes 的 dict；`require_scopes(...)` factory 可作為 FastAPI `Depends` 用；對應 DB schema 跑 `init_db()` 後存在。

---

## Repo / Constraints

- Repo：`C:/Users/user/.arkiv/`
- Python：3.9+（不限），但 NAS 部署需 3.9 相容 — **禁用 `match/case`、禁用 `X | None` union syntax**，per `core/rules/common/platform-compatibility.md`
- 現有 framework：FastAPI + SQLite + pytest + FastAPI `TestClient`
- 現有 tests：5+ 個 test 檔，全部以 `tmp_db` fixture (per `tests/conftest.py:117-189`) 跑（DO NOT 動 conftest.py）
- DB pattern：`db.py:init_db()` 使用 `CREATE TABLE IF NOT EXISTS` idempotent migration；新表跟著加 — 不另寫 ALTER

---

## 執行順序與依賴

```
Step 1: requirements.txt + requirements-cuda.txt 加 nanoid
    ↓
Step 2: auth.py 新檔（SCOPES enum + nanoid id helper + verify_token + require_scopes + audit trail SQL）
    ↓
Step 3: db.py init_db() 加 access_tokens + access_token_scopes 2 table
    ↓
Step 4: tests/test_auth.py 新檔，5 case
    ↓
Step 5: pytest tests/test_auth.py -v 全綠
    ↓
Step 6: 寫 CODEX_RESULT.md
```

---

## 逐步驟實作細節

### Step 1: requirements 加 nanoid

**檔案**：
- `requirements.txt`
- `requirements-cuda.txt`

**改動**：兩個檔各加一行：
```
nanoid>=2.0.0
```

加在已有依賴清單的字母排序合適位置（不強制全 reorder，找個合理的鄰居插入即可）。

### Step 2: 新建 `auth.py`

**檔案**：`C:/Users/user/.arkiv/auth.py`（新檔，~120 行）

**檔案完整骨架**（Codex 照此實作，可調 docstring/註解但 API surface 不動）：

```python
"""Access token auth middleware for arkiv API.

Bearer token + scope enum + IP allowlist (CIDR) + audit trail.
Use via FastAPI Depends(require_scopes('videos_read')) on protected endpoints.

DB schema in db.py init_db(): access_tokens + access_token_scopes tables.
CLI to manage tokens: arkiv_token.py (A.1c, not yet impl).
Bootstrap admin token via ARKIV_ADMIN_BOOTSTRAP_TOKEN env (A.1b).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from typing import Optional, Set

from fastapi import Depends, HTTPException, Request
from nanoid import generate as nanoid_generate

from db import get_conn


# ============================================================
# Scope enum
# ============================================================

SCOPES = frozenset({
    "videos_read",       # GET /api/media* / /api/search/all / /api/stats / /api/tags / /api/duration-by-lang / /api/size-by-ext / /api/projects/health
    "videos_write",      # PATCH /api/media/{id}/rating / POST + DELETE /api/media/{id}/tags
    "media_read",        # GET /api/media/{id}/waveform / /scenes / /export/{fmt} / /export/metadata-csv* / /remotion-props
    "collections_read",  # 預留 chat job 用（A.1a 不開 endpoint）
    "collections_write", # 預留
    "projects_read",     # GET /api/projects / /sync / /health
    "projects_write",    # POST /api/projects / DELETE /api/projects/{name}
    "ingest_write",      # POST /api/ingest / /ingest/scan / /retranscribe / /retry-vision / /reingest
    "admin",             # /api/admin/tokens CRUD (A.1b 才開 endpoint)
})


# ============================================================
# Helpers
# ============================================================

def hash_token(raw: str) -> str:
    """SHA256 hex of raw token. Token plaintext NEVER stored."""
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token_id() -> str:
    """Generate nanoid 21-char URL-safe id for access_tokens.id."""
    return nanoid_generate()


def new_raw_token() -> str:
    """Generate cryptographically random 32-char URL-safe raw token (caller stores hash only)."""
    import secrets
    return secrets.token_urlsafe(32)


def _check_ip_allowed(client_ip: str, allowed_ips_json: str) -> bool:
    """CIDR allowlist match. '*' = any. Returns True if allowed."""
    allowed = json.loads(allowed_ips_json)
    if "*" in allowed:
        return True
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in allowed:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            if ip in net:
                return True
        except ValueError:
            continue
    return False


# ============================================================
# Middleware: verify_token (Layer 1) — 5 gates
# ============================================================

def verify_token(request: Request) -> dict:
    """Verify Bearer token from Authorization header, return token row with scopes set.

    5 gates (in order):
      1. Header present + Bearer prefix → 401 else
      2. Token hash matches a row → 401 else
      3. expires_at not in past → 401 else
      4. client IP matches allowed_ips_json → 403 else
      5. Load scopes from access_token_scopes join table

    Then (non-blocking) update last_used_at / last_used_ip / last_used_user_agent.

    Returns: {"id": token_id, "name": token_name, "scopes": frozenset(scope_strings)}
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header (expected 'Bearer <token>')")

    raw = auth_header[len("Bearer "):].strip()
    if not raw:
        raise HTTPException(401, "Empty Bearer token")

    th = hash_token(raw)
    with get_conn() as cn:
        row = cn.execute(
            "SELECT id, name, expires_at, allowed_ips_json FROM access_tokens WHERE token_hash = ?",
            (th,),
        ).fetchone()
        if not row:
            raise HTTPException(401, "Invalid token")

        # Gate 3: expiry
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                # Treat naive as UTC
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < datetime.now(timezone.utc):
                    raise HTTPException(401, "Token expired")
            except (ValueError, TypeError):
                raise HTTPException(401, "Token has malformed expires_at")

        # Gate 4: IP allowlist
        client_ip = request.client.host if request.client else ""
        if not _check_ip_allowed(client_ip, row["allowed_ips_json"]):
            raise HTTPException(403, "Client IP not in token's allowlist")

        # Gate 5: load scopes
        scope_rows = cn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ?",
            (row["id"],),
        ).fetchall()
        scopes = frozenset(r["scope"] for r in scope_rows)

        # Audit trail (fire-and-forget, do not block request on failure)
        try:
            ua = request.headers.get("user-agent", "")[:200]  # bound length
            cn.execute(
                "UPDATE access_tokens SET last_used_at = ?, last_used_ip = ?, last_used_user_agent = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), client_ip, ua, row["id"]),
            )
        except Exception:
            pass  # audit trail failure must not break request

    return {"id": row["id"], "name": row["name"], "scopes": scopes}


# ============================================================
# Middleware: require_scopes (Layer 2) — factory
# ============================================================

def require_scopes(*needed: str):
    """FastAPI Depends factory. Usage:

        @app.get("/api/media")
        def list_media(_tok = Depends(require_scopes("videos_read"))): ...

    Returns 403 if token lacks any of needed scopes; 401 if no token.
    Passes through the verified token dict as the dependency value.
    """
    # Sanity check at factory time (fail fast at import, not runtime)
    for s in needed:
        if s not in SCOPES:
            raise ValueError(f"Unknown scope '{s}' (valid: {sorted(SCOPES)})")

    def _check(tok: dict = Depends(verify_token)) -> dict:
        token_scopes = tok["scopes"]
        missing = [s for s in needed if s not in token_scopes]
        if missing:
            raise HTTPException(
                403,
                f"Insufficient scope: token has {sorted(token_scopes)}, needs {sorted(needed)} (missing: {missing})",
            )
        return tok

    return _check
```

**注意**：
- 不要在 `auth.py` 加任何 endpoint（那是 admin.py 在 A.1b 做的）
- 不要 import server.py（避循環）
- `get_conn` 從 db 模組 import，不要自己開 sqlite3 連線
- 不要動 SCOPES 條目順序或名稱（A.1b 的 server.py mapping 表依賴此順序）
- 不要加 `chat_read` / `chat_write`（B.4a 才加）

### Step 3: `db.py` 加 2 table

**檔案**：`C:/Users/user/.arkiv/db.py`

**改動**：在 `init_db()` 既有 `CREATE TABLE` 區段末尾（在 `frames` 表之後、`commit()` 之前）加：

```python
    cur.execute("""CREATE TABLE IF NOT EXISTS access_tokens (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT,
      token_hash TEXT UNIQUE NOT NULL,
      expires_at TEXT,
      allowed_ips_json TEXT NOT NULL DEFAULT '["*"]',
      last_used_at TEXT,
      last_used_ip TEXT,
      last_used_user_agent TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS access_token_scopes (
      token_id TEXT NOT NULL,
      scope TEXT NOT NULL,
      PRIMARY KEY (token_id, scope),
      FOREIGN KEY (token_id) REFERENCES access_tokens(id) ON DELETE CASCADE
    )""")

    # Index for fast lookup by hash (verify_token critical path)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_access_tokens_hash ON access_tokens(token_hash)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_access_token_scopes_token_id ON access_token_scopes(token_id)")
```

**注意**：
- 不動 既有 `media` / `tags` / `frames` 表 schema
- 不動 `resolve_path()` / `get_conn()` 函式
- 用 idempotent `IF NOT EXISTS` pattern 一致

### Step 4: `tests/test_auth.py` 新檔

**檔案**：`C:/Users/user/.arkiv/tests/test_auth.py`（新檔，~100 行）

**5 個 test case**（每 case 用 `tmp_db` fixture from `conftest.py`）：

```python
"""Tests for auth.py — verify_token + require_scopes middleware.

Uses tmp_db fixture (conftest.py) which monkeypatches DB_PATH.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


@pytest.fixture
def auth_app(tmp_db, monkeypatch):
    """Build minimal FastAPI app with 2 protected endpoints for testing."""
    # Reimport auth module fresh (post-tmp_db setup)
    import auth
    importlib.reload(auth)

    app = FastAPI()

    @app.get("/test/read")
    def read_endpoint(_tok=Depends(auth.require_scopes("videos_read"))):
        return {"ok": True, "scope": "videos_read"}

    @app.get("/test/write")
    def write_endpoint(_tok=Depends(auth.require_scopes("videos_write"))):
        return {"ok": True, "scope": "videos_write"}

    return app, auth


def _insert_token(auth_mod, name, scopes, expires_at=None, allowed_ips_json='["*"]'):
    """Helper: insert a token, return raw token string."""
    import db, json
    raw = auth_mod.new_raw_token()
    tid = auth_mod.new_token_id()
    with db.get_conn() as cn:
        cn.execute(
            "INSERT INTO access_tokens (id, name, token_hash, expires_at, allowed_ips_json) VALUES (?, ?, ?, ?, ?)",
            (tid, name, auth_mod.hash_token(raw), expires_at, allowed_ips_json),
        )
        for s in scopes:
            cn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (tid, s),
            )
    return raw, tid


def test_missing_authorization_returns_401(auth_app):
    app, _ = auth_app
    client = TestClient(app)
    r = client.get("/test/read")
    assert r.status_code == 401
    assert "Authorization" in r.json()["detail"]


def test_invalid_token_returns_401(auth_app):
    app, _ = auth_app
    client = TestClient(app)
    r = client.get("/test/read", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
    assert "Invalid token" in r.json()["detail"]


def test_expired_token_returns_401(auth_app):
    app, auth = auth_app
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    raw, _ = _insert_token(auth, "expired", ["videos_read"], expires_at=past)
    client = TestClient(app)
    r = client.get("/test/read", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


def test_token_with_wrong_scope_returns_403(auth_app):
    """Token with only videos_read → 403 on /test/write."""
    app, auth = auth_app
    raw, _ = _insert_token(auth, "read-only", ["videos_read"])
    client = TestClient(app)
    r = client.get("/test/write", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    assert "Insufficient scope" in r.json()["detail"]


def test_valid_token_with_correct_scope_returns_200_and_updates_audit(auth_app):
    """Token with videos_read → 200 on /test/read + last_used_at updated."""
    import db
    app, auth = auth_app
    raw, tid = _insert_token(auth, "valid", ["videos_read"])

    client = TestClient(app)
    r = client.get("/test/read", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "scope": "videos_read"}

    # Audit trail: last_used_at should be set
    with db.get_conn() as cn:
        row = cn.execute(
            "SELECT last_used_at, last_used_ip FROM access_tokens WHERE id = ?",
            (tid,),
        ).fetchone()
    assert row["last_used_at"] is not None
    assert row["last_used_ip"] is not None  # TestClient sets some testclient IP
```

**注意**：
- 用 `importlib.reload(auth)` 確保 `tmp_db` 改完 DB_PATH 後 auth.py 拿到新 DB
- `tmp_db` fixture 在 conftest.py 已存在，直接 inject 即可（**不要動 conftest.py**）
- `nanoid` import 失敗時 fixture 會炸 — Step 1 加 requirements 後重跑

### Step 5: pytest

```bash
cd C:/Users/user/.arkiv
pip install nanoid                           # 安裝新依賴
python -m pytest tests/test_auth.py -v       # 預期 5 passed
python -m pytest tests/ -v                   # 預期 既有 5/5 test_offload + 其他既有 全綠（不增 fail）
```

如有 fail：debug 修；不放下 Step 6。

### Step 6: `CODEX_RESULT.md`

寫到 `C:/Users/user/.arkiv/CODEX_RESULT.md`，覆寫既有內容（CC audit 後會清掉）。

模板：

```markdown
# Codex Result — auth-tokens-1a

## 自審 Checklist

── 基礎 ──
[ ] pytest tests/test_auth.py -v 全綠（5/5 PASS）
[ ] pytest tests/ -v 既有測試不增 fail
[ ] py_compile auth.py + tests/test_auth.py 通過
[ ] Python 3.9 相容（無 match/case、無 X | None union）

── 功能 ──
[ ] auth.py SCOPES enum 含 9 個 scope（不少不多）
[ ] verify_token 5 gates 全實作
[ ] require_scopes factory 對 unknown scope 在 import time fail（ValueError）
[ ] DB schema access_tokens + access_token_scopes + 2 index 都建
[ ] Audit trail (last_used_at/ip/user_agent) 寫入 fire-and-forget

── 整合 ──
[ ] 無硬編碼 token / IP / secret
[ ] 無未使用 import
[ ] 無修改 scope 禁區檔
[ ] 無 git commit / push 操作

## 測試輸出

(貼完整 pytest -v 輸出)

## REVIEW

(若有任何 scope 越界 / 設計疑慮 / 對 spec 質疑，列在此)
```

---

## 不改的檔案（再次強調）

per Phase 0 禁區清單。Codex **任何修改禁區檔的動作視為越界**，CC audit 會 fail。如真有必要動，必須在 `CODEX_RESULT.md` `## REVIEW` 提出 + 暫不動。

---

## 測試計畫

| # | 測試名 | 驗證什麼 |
|---|---|---|
| 1 | `test_missing_authorization_returns_401` | 無 Authorization header → 401 |
| 2 | `test_invalid_token_returns_401` | token hash 在 DB 查無 → 401 |
| 3 | `test_expired_token_returns_401` | expires_at 在過去 → 401 |
| 4 | `test_token_with_wrong_scope_returns_403` | token 缺對應 scope → 403 |
| 5 | `test_valid_token_with_correct_scope_returns_200_and_updates_audit` | 正確 token → 200 + audit trail 更新 |

---

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| nanoid 依賴在某 Python 版本/平台裝不上 | 已選 ≥2.0.0 broad compat；如真撞 → fallback 用 `secrets.token_urlsafe(16)` 當 id（在 auth.py `new_token_id()` 內換實作，不破壞 API surface） |
| `tmp_db` fixture 跟 auth.py module-level state 撞 | `importlib.reload(auth)` 在 fixture 內強制重 import |
| Audit trail UPDATE 失敗阻擋請求 | try/except pass — fire-and-forget，audit 失敗不擋 request |
| FastAPI TestClient 的 `request.client.host` 是 `'testclient'` 而非 IP | 對 IP allowlist test 用 `'*'` 預設 OK；如測 CIDR 嚴格，要 mock request.client |

---

## CC-Fallback Brief（若 Codex 撞 limit / fail）

CC 可直接接手執行下列順序（預估 CC 時間：~1.5-2 hr）：

1. **Step 1** — `requirements.txt` + `requirements-cuda.txt` 各加一行 `nanoid>=2.0.0`（2 min）
2. **Step 2** — `auth.py` 新檔，照上方完整骨架抄（30-45 min；含 SCOPES enum / hash_token / new_token_id / new_raw_token / _check_ip_allowed / verify_token / require_scopes）
3. **Step 3** — `db.py` `init_db()` 末尾加 2 個 CREATE TABLE + 2 個 CREATE INDEX（10 min）
4. **Step 4** — `tests/test_auth.py` 新檔，5 case 照上方完整骨架抄（30 min）
5. **Step 5** — `pip install nanoid && pytest tests/test_auth.py -v && pytest tests/ -v`（10 min；如 fail iterate）
6. **Step 6** — 寫 dev-log §X「A.1a CC takeover after Codex limit」+ commit + push（10 min）

**CC takeover 觸發**：Codex 同一 sub-dispatch 連續 3 次 fail（per `codex-handover` SKILL Phase 4 rework_limits 三振出局），CC 走本 brief。

---

## 交付格式

Codex 完成後：

1. **檔案 apply 完**（git status 顯示 modified db.py / requirements.txt / requirements-cuda.txt + untracked auth.py + tests/test_auth.py）
2. **不 git commit** — CC 接手 commit + push
3. **寫 CODEX_RESULT.md** 含上方 checklist + pytest 完整輸出 + REVIEW
4. **停手** 等 CC audit

---

## 後續

A.1a CC audit + commit 通過後：
- A.1b 開始：CC 寫 `docs/auth-tokens-1b-handover.md`（42-route migration + admin.py + bootstrap token 機制）
- A.1c 開始：CC 寫 `docs/auth-tokens-1c-handover.md`（arkiv_token.py CLI + 補 test + README/CHANGELOG）
- A 三段全 commit 完 → tag v0.4.1 + push + GitHub Release
