"""Web-security boundary guards for the API layer.

R5-25 / round-5 #51: the APIRouter split is blocked by ~50 cross-group helpers —
a naive cut has each router do `from server import _assert_same_site`, and since
`server` imports the routers, that's a partially-initialized-module ImportError.
The fix is to extract the shared, server-state-free helpers into leaf service
modules that the routers (and server) import. This is the write-boundary /
same-site cluster: the export-destination allowlist, the offload-destination
denylist, the ingest-path allowlist, and the same-site (CSRF) guard.

Depends only on config + fastapi + stdlib — no server state — so it sits at the
bottom of the import graph. server.py re-exports these names for backward compat
(existing call sites + tests referencing `server._assert_export_dest_safe` etc.
keep working unchanged).
"""
import os
from pathlib import Path

from fastapi import HTTPException, Request

import config

# CORS / same-site allowlist. Owned HERE, not in server.py, because both
# `_assert_same_site` (below) AND server's CORSMiddleware need it — leaving it in
# server.py while webguard imported it back would re-create the very
# router→server→router import cycle this split exists to remove. server.py
# re-imports it for the middleware.
_ALLOWED_ORIGINS = [
    "http://localhost:8501",
    "http://127.0.0.1:8501",
    "http://localhost:5173",     # Vite dev server (frontend dev + ws proxy)
    "http://127.0.0.1:5173",
    "https://tauri.localhost",   # Tauri webview
]


# ── export destination allowlist (write boundary) ────────────────────────────

def _allowed_export_roots() -> list:
    """Approved roots for export-to endpoints. User can override with
    ARKIV_EXPORT_ROOTS env (os.pathsep-separated list of abs paths — ':' on
    POSIX, ';' on Windows). fable-audit 2026-07-12 (#3): splitting on a literal
    ':' shredded Windows drive-letter paths (D:\\Exports → ['D', '\\Exports'])
    at the export-allowlist boundary — mirror the _allowed_ingest_roots fix."""
    custom = os.environ.get("ARKIV_EXPORT_ROOTS", "").strip()
    if custom:
        return [Path(p).expanduser().resolve() for p in custom.split(os.pathsep) if p.strip()]
    home = Path.home()
    return [
        (home / "Desktop").resolve(),
        (home / "Documents").resolve(),
        (home / "Downloads").resolve(),
        (home / "Movies").resolve(),
        # Cross-platform tmp + project root for tests / scripted exports
        Path("/tmp").resolve(),
        Path(os.environ.get("TMPDIR", "/tmp")).resolve(),
        (Path.cwd() / "temp").resolve(),
        (config.PROJECT_ROOT / "temp").resolve(),
    ]


_ALLOWED_EXPORT_EXTS = {
    ".csv", ".srt", ".vtt", ".edl", ".fcpxml", ".xml", ".txt", ".json",
}


def _assert_export_dest_safe(dest: Path) -> None:
    """Reject writes outside approved user export roots.

    Codex Round-2 audit Critical fix: 舊版 denylist 只擋 6 個系統 dir，能寫
    `~/.ssh/authorized_keys` / `/Library/LaunchAgents/*.plist` / `/var/log`
    等敏感位置。Tailscale 共享 + 無 auth 場景下任何 collaborator 直接 RCE。

    新策略：allowlist — dest 的 canonical path 必須落在 ALLOWED 之一底下；
    副檔名也限定在常見匯出格式（.csv/.srt/.vtt/.edl/.fcpxml/.xml/.txt/.json），
    防止寫 .plist / .pem / .ssh-config 之類執行/憑證檔。
    """
    canonical = dest.resolve()
    if canonical.suffix.lower() not in _ALLOWED_EXPORT_EXTS:
        raise HTTPException(403, f"不允許的匯出副檔名：{canonical.suffix}")
    roots = _allowed_export_roots()
    for root in roots:
        try:
            canonical.relative_to(root)
            return  # under approved root
        except ValueError:
            continue
    # fable-audit 2026-07-12: don't echo the resolved absolute roots in the 403
    # body — that leaks the operator's home layout to any caller probing the
    # boundary. Return stable labels instead.
    raise HTTPException(
        403,
        "匯出路徑必須在批准的目錄下（Desktop / Documents / Downloads / Movies / temp，"
        "或 ARKIV_EXPORT_ROOTS 指定的目錄）",
    )


# ── offload destination denylist ─────────────────────────────────────────────

# Offload destinations are arbitrary by design (camera card → backup drives, e.g.
# /Volumes/*), so — unlike export — we do NOT apply the approved-roots allowlist.
# But refuse writes INTO OS-sensitive locations where a copied file could gain
# execution/persistence (LaunchAgents/LaunchDaemons, ~/.ssh, cron, systemd, /etc,
# system dirs). A card offload has no legitimate reason to target these; the
# export path already 403s for the same class of write.
# fable-audit 2026-07-12 (#1 /api/offload arbitrary-file-write).
_OFFLOAD_DENY_SUBSTR = (
    "/library/launchagents", "/library/launchdaemons",
    "/.ssh", "/.config/systemd/", "/var/spool/cron", "/private/etc/",
)
_OFFLOAD_DENY_ROOTS = (
    "/system", "/bin", "/sbin", "/usr/bin", "/usr/sbin", "/etc", "/private/etc",
)


def _assert_offload_dst_safe(dst: str) -> None:
    """Reject offload destinations that land inside OS-sensitive/executable dirs."""
    canonical = str(Path(dst).expanduser().resolve()).replace("\\", "/").lower().rstrip("/")
    probe = canonical + "/"
    for bad in _OFFLOAD_DENY_SUBSTR:
        if bad in probe:
            raise HTTPException(403, "拒絕寫入系統敏感目錄（LaunchAgents / .ssh / cron / systemd 等）")
    for root in _OFFLOAD_DENY_ROOTS:
        if canonical == root or probe.startswith(root + "/"):
            raise HTTPException(403, "拒絕寫入系統目錄")


# ── ingest path allowlist (filesystem-inventory-leak boundary) ───────────────

def _allowed_ingest_roots() -> list:
    """Approved roots for ingest scan / ingest endpoints.

    Default: PROJECT_ROOT (where arkiv 's own DB lives) + standard user media
    locations. Override with ARKIV_INGEST_ROOTS env (colon-separated).

    Codex Round-2 audit (J1): without bounds, /api/ingest/scan walked any path
    a Tailscale collaborator could supply, returning size + abs path of every
    media file — full filesystem inventory leak.
    """
    custom = os.environ.get("ARKIV_INGEST_ROOTS", "").strip()
    if custom:
        # os.pathsep, not ':' — Windows uses ';' AND ':' appears in drive letters
        # (C:\...), so splitting on ':' shredded every Windows path.
        return [Path(p).expanduser().resolve() for p in custom.split(os.pathsep) if p.strip()]
    home = Path.home()
    roots = [
        config.PROJECT_ROOT.resolve() if config.PROJECT_ROOT else None,
        (home / "Desktop").resolve(),
        (home / "Documents").resolve(),
        (home / "Movies").resolve(),
        (home / "Pictures").resolve(),
    ]
    # /Volumes/* (Mac SMB mounts of NAS shares) — allow each top-level mount
    volumes = Path("/Volumes")
    if volumes.exists():
        try:
            for vol in volumes.iterdir():
                if vol.is_dir():
                    resolved = vol.resolve()
                    # Skip a volume that resolves to the filesystem root (e.g.
                    # /Volumes/Macintosh HD → '/'): allowing '/' makes the J1
                    # bound a no-op — every path is then "under an approved root".
                    if str(resolved) != resolved.anchor:
                        roots.append(resolved)
        except OSError:
            pass
    # Final guard: never allow a bare filesystem/drive root through (defeats J1).
    return [r for r in roots if r is not None and str(r) != r.anchor]


def _assert_ingest_path_safe(target: Path) -> None:
    roots = _allowed_ingest_roots()
    canonical = target.resolve()
    for root in roots:
        try:
            canonical.relative_to(root)
            return
        except ValueError:
            continue
    raise HTTPException(
        403,
        f"ingest 路徑必須在批准的目錄底下：{[str(r) for r in roots]} (override via ARKIV_INGEST_ROOTS env)",
    )


# ── same-site (CSRF) guard ───────────────────────────────────────────────────

def _assert_same_site(request: Request) -> None:
    """audit M14: the no-body POSTs below are CORS 'simple requests' — a
    malicious page can fire them cross-site WITHOUT a preflight, and
    loopback-trust then authorizes them (whole-library rebuild / proxy-build
    DoS). Browsers attach Sec-Fetch-Site and/or Origin on cross-site POSTs;
    non-browser clients (curl, scripts) send neither and pass through."""
    sfs = request.headers.get("sec-fetch-site")
    if sfs and sfs not in ("same-origin", "same-site", "none"):
        raise HTTPException(403, "cross-site request rejected")
    origin = request.headers.get("origin")
    if not origin:
        return  # non-browser client
    if origin in _ALLOWED_ORIGINS:
        return
    if origin != "null" and origin.split("://", 1)[-1] == request.headers.get("host", ""):
        return  # same-origin for whatever host/port this deployment uses
    raise HTTPException(403, "cross-site request rejected")
