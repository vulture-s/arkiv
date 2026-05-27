# Auth Tokens 1c — CLI + conftest fixture 修補 + README/CHANGELOG — Codex Handover 執行計畫

## Phase 0: Scope 邊界

### Scope 白名單

| 檔案 | 動作 | LOC 預估 |
|---|---|---|
| `arkiv_token.py` | **新檔** — CLI: create / list / show / revoke subcommands；走本機 SQLite 直接寫（避 bootstrap 雞生蛋）| ~150 |
| `tests/conftest.py` | Edit — **`fastapi_client` fixture 內部 inject admin Authorization header**（讓既有 test_server.py 等不用改也能跑） | +30 |
| `tests/test_auth.py` | Edit（A.1a + A.1b 已建 11 case）— 補 CLI + IP CIDR subnet + multi-scope edge case ~5 case | +80 |
| `README.md` | Edit — Features 段加「Auth & multi-machine」一條 + 加 quickstart 範例 | +30 |
| `README.zh-TW.md` | Edit — 同 | +30 |
| `CHANGELOG.md` | Edit — 加 `## v0.4.1 (2026-05-27) — API Scope Token` entry | +40 |

### Scope 禁區（不可動）

```
auth.py / admin.py / server.py / config.py / db.py         ← A.1a + A.1b 已 ship 穩定，不准動
requirements.txt / requirements-cuda.txt                    ← A.1a 已加 nanoid
vision.py / transcribe.py / vectordb.py / federation.py / ingest.py / health.py / mhl.py / offload.py / camera_report.py / codec.py / embed.py / frames.py
tests/test_offload.py / test_mhl.py / test_camera_report.py / test_server.py / test_config.py / test_db.py / test_phase8.py / test_vectordb.py
  ← 既有 test 檔本身不准動；conftest.py 的 fastapi_client fixture 修改後 應該自動 fix 大部分 既有 test 的 401/403 問題
src-tauri/* / .github/workflows/* / .claude/settings.local.json
docs/* 除本 handover 對應的 CODEX_RESULT.md
```

### Commit 邊界

同 A.1a / A.1b — Codex apply + pytest + CODEX_RESULT.md 寫完即停。**Codex 不負責 git tag / GitHub Release**（v0.4.1 tag + Release 由 CC 在 A.1c commit 後做）。

---

## Context

A.1a + A.1b 已 ship：DB schema + auth middleware + admin endpoints + bootstrap mechanism on `server.py`。但 A.1b 預期副作用 = 既有 `tests/test_server.py` 等大量 fail（裸 call /api/media 現需 token）。

A.1c 收尾三件事：
1. **CLI tool** `arkiv_token.py` — Hevin 從 shell 直接管 token（不一定要走 admin endpoint，CLI 走本機 SQLite 直寫，方便 bootstrap）
2. **conftest fix** — `fastapi_client` fixture 自動 inject admin token，既有 test_server.py 等不改也通
3. **文件** — README/CHANGELOG 講清 auth 怎麼用 + 為什麼 v0.4.1 patch

預期成果：
- `python arkiv_token.py create --name PC-dev --scopes videos_read,videos_write` → 印 raw token + id（一次性）
- `pytest tests/` 全綠（或回到 A.1a baseline 的 9 pre-existing fail，不含 auth-related fail）
- v0.4.1 release narrative 清楚

---

## Repo / Constraints

同 A.1a / A.1b。Python 3.9 相容。Test 用 TMP=/c/tmp 環境（per A.1a 觀察）。

---

## 執行順序與依賴

```
Step 1: 新建 arkiv_token.py CLI
    ↓
Step 2: 改 tests/conftest.py — fastapi_client fixture 加 auth bypass
    ↓
Step 3: tests/test_auth.py 補 5 case (CLI + IP CIDR + multi-scope)
    ↓
Step 4: README.md + README.zh-TW.md 加 Auth 段
    ↓
Step 5: CHANGELOG.md 加 v0.4.1 entry
    ↓
Step 6: pytest tests/ -v 全綠（或回到 A.1a baseline 的 9 pre-existing fail）
    ↓
Step 7: CODEX_RESULT.md
```

---

## 逐步驟實作細節

### Step 1: 新建 `arkiv_token.py` CLI

**檔案**：`C:/Users/user/.arkiv/arkiv_token.py`（新檔，~150 行）

**完整骨架**：

```python
#!/usr/bin/env python3
"""arkiv-token — CLI for managing API access tokens.

Usage:
  arkiv_token.py create --name <name> --scopes <s1,s2,...> [--ip-allowlist <cidr1,cidr2,...>] [--expires-in <days>] [--description <text>]
  arkiv_token.py list
  arkiv_token.py show <token-id>
  arkiv_token.py revoke <token-id>

Reads/writes directly to local SQLite (no admin token needed — bypasses chicken-and-egg).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from auth import SCOPES, hash_token, new_raw_token, new_token_id
from db import get_conn, init_db


def _parse_scope_list(s: str) -> List[str]:
    parts = [p.strip() for p in s.split(",") if p.strip()]
    unknown = [p for p in parts if p not in SCOPES]
    if unknown:
        print("error: unknown scope(s): {0}".format(unknown), file=sys.stderr)
        print("       valid scopes: {0}".format(sorted(SCOPES)), file=sys.stderr)
        sys.exit(2)
    return parts


def _parse_ip_list(s: Optional[str]) -> List[str]:
    if not s:
        return ["*"]
    return [p.strip() for p in s.split(",") if p.strip()]


def cmd_create(args):
    init_db()  # idempotent — ensure tables exist on first call
    scopes = _parse_scope_list(args.scopes)
    allowed_ips = _parse_ip_list(args.ip_allowlist)
    expires_at = None
    if args.expires_in:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=args.expires_in)).isoformat()

    raw = new_raw_token()
    tid = new_token_id()

    with get_conn() as cn:
        cn.execute(
            "INSERT INTO access_tokens (id, name, description, token_hash, expires_at, allowed_ips_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, args.name, args.description, hash_token(raw), expires_at, json.dumps(allowed_ips)),
        )
        for s in scopes:
            cn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (tid, s),
            )

    # Print token ONCE — cannot be retrieved later
    print("=" * 70)
    print("Token created. RAW TOKEN (save now, cannot be retrieved later):")
    print("")
    print("  {0}".format(raw))
    print("")
    print("Token ID:     {0}".format(tid))
    print("Name:         {0}".format(args.name))
    print("Scopes:       {0}".format(sorted(scopes)))
    print("IP allowlist: {0}".format(allowed_ips))
    if expires_at:
        print("Expires:      {0}".format(expires_at))
    else:
        print("Expires:      never")
    print("=" * 70)


def cmd_list(args):
    init_db()
    with get_conn() as cn:
        rows = cn.execute(
            "SELECT id, name, expires_at, last_used_at, last_used_ip, created_at "
            "FROM access_tokens ORDER BY created_at DESC"
        ).fetchall()
        if not rows:
            print("No tokens. Run: arkiv_token.py create --name <name> --scopes <s1,s2,...>")
            return
        # Header
        print("{0:<24} {1:<20} {2:<22} {3:<22} {4}".format("ID", "NAME", "LAST USED", "EXPIRES", "SCOPES"))
        for r in rows:
            scope_rows = cn.execute(
                "SELECT scope FROM access_token_scopes WHERE token_id = ?",
                (r["id"],),
            ).fetchall()
            scopes_str = ",".join(sorted(s["scope"] for s in scope_rows))
            print("{0:<24} {1:<20} {2:<22} {3:<22} {4}".format(
                r["id"][:22],
                r["name"][:18],
                r["last_used_at"] or "never",
                r["expires_at"] or "never",
                scopes_str,
            ))


def cmd_show(args):
    init_db()
    with get_conn() as cn:
        row = cn.execute(
            "SELECT id, name, description, expires_at, allowed_ips_json, "
            "last_used_at, last_used_ip, last_used_user_agent, created_at "
            "FROM access_tokens WHERE id = ?",
            (args.token_id,),
        ).fetchone()
        if not row:
            print("error: token not found: {0}".format(args.token_id), file=sys.stderr)
            sys.exit(2)
        scope_rows = cn.execute(
            "SELECT scope FROM access_token_scopes WHERE token_id = ?",
            (args.token_id,),
        ).fetchall()
        scopes = sorted(s["scope"] for s in scope_rows)

    print("Token ID:      {0}".format(row["id"]))
    print("Name:          {0}".format(row["name"]))
    print("Description:   {0}".format(row["description"] or "(none)"))
    print("Scopes:        {0}".format(scopes))
    print("IP allowlist:  {0}".format(json.loads(row["allowed_ips_json"])))
    print("Expires:       {0}".format(row["expires_at"] or "never"))
    print("Created:       {0}".format(row["created_at"]))
    print("Last used:     {0}".format(row["last_used_at"] or "never"))
    if row["last_used_at"]:
        print("Last IP:       {0}".format(row["last_used_ip"] or "(unknown)"))
        print("Last UA:       {0}".format(row["last_used_user_agent"] or "(none)"))


def cmd_revoke(args):
    init_db()
    with get_conn() as cn:
        cur = cn.execute("DELETE FROM access_tokens WHERE id = ?", (args.token_id,))
        if cur.rowcount == 0:
            print("error: token not found: {0}".format(args.token_id), file=sys.stderr)
            sys.exit(2)
    print("Revoked token: {0}".format(args.token_id))


def build_parser():
    p = argparse.ArgumentParser(description="arkiv API access token CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("create", help="Create new access token")
    sp.add_argument("--name", required=True, help="Human-readable name (e.g. 'PC-dev')")
    sp.add_argument("--scopes", required=True, help="Comma-separated scope list")
    sp.add_argument("--ip-allowlist", default=None, help="Comma-separated CIDR list (e.g. '127.0.0.1/32,100.64.0.0/10'). Default: '*' (any).")
    sp.add_argument("--expires-in", type=int, default=None, help="Expire after N days. Default: never.")
    sp.add_argument("--description", default=None, help="Optional description")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("list", help="List all tokens (no raw tokens shown)")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="Show token detail")
    sp.add_argument("token_id", help="Token ID from list")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("revoke", help="Delete a token")
    sp.add_argument("token_id", help="Token ID to revoke")
    sp.set_defaults(func=cmd_revoke)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
```

### Step 2: 改 `tests/conftest.py` — fastapi_client fixture

**檔案**：`C:/Users/user/.arkiv/tests/conftest.py`

**改動**：找到既有 `fastapi_client` fixture（per Phase 1 探索 line ~117-189），修改其 implementation 來 **inject admin token Authorization header**：

```python
# 既有大致長相 (conftest.py)
@pytest.fixture
def fastapi_client(tmp_db, monkeypatch):
    import server
    from fastapi.testclient import TestClient
    return TestClient(server.app)
```

**改成**（加 auto-bootstrap admin token + 設 client headers）：

```python
@pytest.fixture
def fastapi_client(tmp_db, monkeypatch):
    """TestClient with admin token auto-injected via Authorization header.

    Bootstraps a synthetic admin token in tmp_db so existing endpoint tests
    (test_server.py / 等) don't need to know about auth. Real auth behavior
    tested in test_auth.py separately.
    """
    import importlib
    import auth, db, admin
    importlib.reload(auth)
    importlib.reload(db)
    importlib.reload(admin)

    # Bootstrap admin token directly into tmp_db
    raw = auth.new_raw_token()
    tid = auth.new_token_id()
    with db.get_conn() as cn:
        cn.execute(
            "INSERT INTO access_tokens (id, name, token_hash, allowed_ips_json) "
            "VALUES (?, ?, ?, ?)",
            (tid, "test-admin", auth.hash_token(raw), '["*"]'),
        )
        # Grant all scopes
        for s in auth.SCOPES:
            cn.execute(
                "INSERT INTO access_token_scopes (token_id, scope) VALUES (?, ?)",
                (tid, s),
            )

    import server
    importlib.reload(server)
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    # Default header on every request
    client.headers.update({"Authorization": "Bearer {0}".format(raw)})
    return client
```

**注意**：
- 用 `importlib.reload` 強制重新 import 確保 tmp_db monkeypatch 生效
- 給 token 全 9 scope（讓 test_server.py 等所有 endpoint 都過）
- `client.headers.update()` 設 default header，後續 `client.get(...)` 不用每次手動帶 Authorization
- **不動** `tmp_db` fixture 本身或 `sample_record` factory（這些是 A.1a 既有）

### Step 3: `tests/test_auth.py` 補 5 case

加在 A.1a + A.1b 既有 11 case 之後：

| # | 測試名 | 驗證什麼 |
|---|---|---|
| 17 | `test_cli_create_inserts_token` | run arkiv_token.py create → DB 有對應 row + scope FK |
| 18 | `test_cli_list_includes_created_tokens` | create 後 list → output 含 name |
| 19 | `test_cli_revoke_removes_token` | revoke → DB row gone + scope FK cascade gone |
| 20 | `test_ip_allowlist_cidr_match` | 100.64.0.0/10 token → 100.64.5.3 OK / 100.128.0.1 reject |
| 21 | `test_multi_scope_token_all_required_present` | token has [videos_read, videos_write] → /test/read (videos_read) ✓ / /test/admin (admin) ✗ 403 |

CLI tests 用 `subprocess.run([sys.executable, "arkiv_token.py", "create", ...], capture_output=True)` 跑 CLI 進程。

### Step 4: README 加 Auth 段

**檔案**：`C:/Users/user/.arkiv/README.md`

在 Features 段之後（line ~70-72 附近）加：

```markdown
## API Authentication

All `/api/*` endpoints require a Bearer token with the appropriate scope. Tokens are scope-based for multi-machine fleets (e.g. ingest PC = write, edit Mac = read-only).

**First-time bootstrap:**

```bash
# Generate admin bootstrap token, restart server
export ARKIV_ADMIN_BOOTSTRAP_TOKEN=$(openssl rand -base64 32)
python server.py &

# Create per-machine tokens via CLI (writes to local SQLite directly)
python arkiv_token.py create --name "PC-dev" --scopes videos_read,videos_write,media_read,ingest_write --ip-allowlist 127.0.0.1/32 --expires-in 90
# → Output prints raw token ONCE; save it

# Use token:
curl -H "Authorization: Bearer <token>" http://localhost:8501/api/media
```

**Available scopes:** `videos_read` / `videos_write` / `media_read` / `collections_read` / `collections_write` / `projects_read` / `projects_write` / `ingest_write` / `admin`

**CLI subcommands:** `arkiv_token.py {create,list,show,revoke}`. See `arkiv_token.py --help`.
```

**README.zh-TW.md** 同理加繁中版。

### Step 5: CHANGELOG.md v0.4.1 entry

加在 CHANGELOG.md 最上方（v0.4.0 之上）：

```markdown
## v0.4.1 (2026-05-27) — API Scope Token Auth

> **⚠️ Breaking change** — all `/api/*` endpoints now require Bearer token. See README "API Authentication" section for bootstrap SOP.

### New Features
- **API access token system** — Bearer + 9-scope enum (videos/collections/projects/media/ingest read/write + admin) + IP allowlist (CIDR) + audit trail (last_used_at/ip/user_agent). Pattern borrowed from Edit Mind (`apps/background-jobs/src/middleware/accessTokenAuth.ts`) — adapted to FastAPI Depends + SQLite + stdlib `ipaddress`.
- **`arkiv_token.py` CLI** — `create / list / show / revoke` subcommands; writes directly to local SQLite (no admin token needed — solves bootstrap chicken-and-egg).
- **Bootstrap mechanism** — `ARKIV_ADMIN_BOOTSTRAP_TOKEN` env on first startup seeds an admin token (which can then create per-machine tokens via `/api/admin/tokens`).

### Internals
- New: `auth.py` (137 LOC, middleware + scope check + IP CIDR + audit), `admin.py` (~120 LOC, token CRUD service), `arkiv_token.py` (~150 LOC, CLI), `tests/test_auth.py` (~250 LOC, 16 cases).
- DB: `access_tokens` + `access_token_scopes` tables + 2 index for fast hash lookup.
- `server.py`: 32 endpoints decorated with `Depends(require_scopes(...))` + 4 new `/api/admin/tokens` endpoints + startup event for bootstrap.
- New dep: `nanoid>=2.0.0`.

### Driving incident
5-machine fleet (PC / M2 Max / mini-relay / Chloe / NAS-OpenClaw) all need to call arkiv API but should have different read/write permissions. Pre-v0.4.1 = zero auth, anyone on Tailscale could `DELETE /api/projects/{name}` or `POST /api/ingest`. v0.4.1 closes this with scope-based per-machine tokens + IP allowlist.

---
```

### Step 6: pytest

```bash
cd C:/Users/user/.arkiv
TMP=/c/tmp TEMP=/c/tmp TMPDIR=/c/tmp python -m pytest tests/ -v
```

**預期**：
- tests/test_auth.py: 16 PASS（A.1a 6 + A.1b 11 + A.1c 5 = 22... wait 應該是 6+11+5=22 不是 16）— 重算：A.1a 6, A.1b 11, A.1c 5 = 22 cases total
- tests/test_server.py 等 既有 test 因 conftest fix 應回到 A.1a baseline 的 fail set（test_config Windows POSIX 5 + test_db 1 + test_phase8 3 = 9 pre-existing）
- 總計 ~140-145 passed / 9 pre-existing fail

如 既有 test 仍 fail 多於 9（i.e. 還有 auth-related fail 沒 conftest fix），表示 fastapi_client fixture 改 inject 不完整 → debug iterate。

### Step 7: CODEX_RESULT.md

同 A.1a / A.1b 格式。

---

## 不改的檔案

per Phase 0 禁區清單。

---

## 測試計畫

| # | 測試名 | 驗證 |
|---|---|---|
| 17 | `test_cli_create_inserts_token` | subprocess.run CLI create → DB 有對應 row |
| 18 | `test_cli_list_includes_created_tokens` | create + list → name 在 output |
| 19 | `test_cli_revoke_removes_token` | revoke → DELETE 觸發 + scope FK CASCADE |
| 20 | `test_ip_allowlist_cidr_match` | CIDR subnet match 正確 + non-match reject |
| 21 | `test_multi_scope_token_all_required_present` | token 有 [videos_read, videos_write] → 對 videos_read endpoint OK / 對 admin endpoint 403 |

---

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| conftest fastapi_client fixture 改錯 → 既有 test_server.py 等仍 fail | 漸進驗：先跑 `pytest tests/test_server.py -v` 看狀況；fail 多 → debug fixture 而非 spec |
| importlib.reload(server) 觸發 startup event 重跑 → 對 tmp_db pollution | startup event bootstrap 是 idempotent (DB 非空 no-op)；reload 重跑無害 |
| CLI subprocess test 在 Windows path quoting | 用 `[sys.executable, ...]` list 形式不用 shell=True |
| README 中英 markdown 表格寬度跨平台不對齊 | 用標準 markdown 表格語法，不用 ASCII art |

---

## CC-Fallback Brief（若 Codex 撞 limit / fail）

CC 接手執行（預估 ~2-3 hr）：

1. **Step 1** — `arkiv_token.py` 新檔，照完整骨架抄（45 min）
2. **Step 2** — 改 `tests/conftest.py` `fastapi_client` fixture 加 token inject（20 min）
3. **Step 3** — `tests/test_auth.py` 補 5 case（30 min）
4. **Step 4** — README.md + README.zh-TW.md Auth 段（20 min）
5. **Step 5** — CHANGELOG.md v0.4.1 entry（10 min）
6. **Step 6** — `pytest tests/ -v` 全跑 + debug（30 min）
7. **Step 7** — dev-log + commit + push（15 min）

**CC takeover 觸發**：Codex 同 sub-dispatch 連續 3 次 fail（per rework_limits 三振）。

---

## 交付格式

同 A.1a / A.1b — apply + CODEX_RESULT.md 寫完即停。CC 接手 audit + commit + push + tag v0.4.1 + GitHub Release。

---

## 後續（A 三段全完成後）

CC 執行：
1. Commit A.1c atomic
2. Push origin main
3. `git tag -a v0.4.1 -m "API Scope Token Auth"` + `git push origin v0.4.1`
4. `gh release create v0.4.1 --notes-from-tag`（或從 CHANGELOG 抽 notes）
5. `rm C:/Users/user/.arkiv/.data/no-auto-commit`（Phase 5 cleanup — A 全鏈完成才 rm）
6. Sync vault dev-log §X + todo Edit Mind 借鏡「API scope token system」條目 mark ✅
7. 通知 Hevin：bootstrap SOP + fleet token rollout 可開始
