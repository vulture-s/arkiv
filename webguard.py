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
# Windows-native equivalents. On Windows resolve() anchors a rootless/POSIX path
# to the CURRENT drive (Path('/etc') -> C:\etc; Path('C:\\Windows') stays), so
# these match against a drive-letter-stripped form and are themselves drive-
# agnostic (X:\Windows, D:\Windows, …). The per-user Startup folder is the
# Windows analog of LaunchAgents — a file copied there gains logon persistence.
_OFFLOAD_DENY_WIN_ROOTS = (
    "/windows", "/program files", "/program files (x86)", "/programdata",
)
_OFFLOAD_DENY_WIN_SUBSTR = (
    "/appdata/roaming/microsoft/windows/start menu/programs/startup",
)


def _strip_offload_segment(seg):
    """Drop trailing dots/spaces that Win32 ignores ('windows.' -> 'windows'),
    but keep '.'/'..' navigation and any all-dot/space segment intact."""
    stripped = seg.rstrip(" .")
    return stripped if stripped else seg


def _norm_offload_path(path_str):
    r"""Fold to the lower-case, forward-slash, no-trailing-slash form the offload
    denylist compares against, canonicalising the Windows forms that would else
    slip past the deny roots (2026-07-25 audit follow-up):
      \\?\C:\..  and  \\.\C:\..  (extended-length / DOS-device)  -> c:/..
      \\?\UNC\host\share\..                                       -> //host/share/..
      \\host\C$\..              (admin drive share)               -> c:/..
      plus trailing dots/spaces Win32 strips from each path segment.
    Residual (accepted): 8.3 short names (C:\PROGRA~1) and other existing-dir
    aliases are left to Windows resolve() in the caller, which only expands them
    for paths that actually exist on disk."""
    p = path_str.replace("\\", "/").lower()
    if p.startswith("//?/unc/"):
        p = "//" + p[len("//?/unc/"):]          # device-namespace UNC -> plain UNC
    elif p.startswith(("//?/", "//./")):
        p = p[4:]                                 # //?/c:/windows -> c:/windows
    if p.startswith("//"):
        host, _sep, tail = p[2:].partition("/")   # host, '/', 'share/rest'
        share, _sep2, rest = tail.partition("/")
        if len(share) == 2 and "a" <= share[0] <= "z" and share[1] == "$":
            p = share[0] + ":/" + rest            # \\host\C$\.. addresses the whole drive
    p = "/".join(_strip_offload_segment(seg) for seg in p.split("/"))
    return p.rstrip("/")


def _strip_win_drive(path):
    """Drop a leading Windows drive letter, keeping the path root-anchored
    ('c:/etc' -> '/etc', 'c:/windows' -> '/windows'), so a POSIX deny root or a
    (also root-anchored) Windows deny root matches regardless of which drive
    resolve() anchored the path to. Requires an ASCII letter drive so a POSIX
    path like '1:/etc' is not mistaken for a drive and wrongly denied."""
    if len(path) >= 2 and "a" <= path[0] <= "z" and path[1] == ":":
        return path[2:]
    return path


def _offload_deny_reason(path):
    """Deny category for ONE already-normalised path (see _norm_offload_path), or
    '' if allowed. 'sensitive' = an execution/persistence location; 'system' = an
    OS system root. Pure and host-independent: it evaluates the denylist over both
    the path as given AND its drive-stripped form, so a POSIX literal that resolve()
    drive-anchored on Windows ('/etc' -> 'c:/etc') still matches the '/etc' root."""
    for form in (path, _strip_win_drive(path)):
        probe = form + "/"
        for bad in _OFFLOAD_DENY_SUBSTR + _OFFLOAD_DENY_WIN_SUBSTR:
            if bad in probe:
                return "sensitive"
        for root in _OFFLOAD_DENY_ROOTS + _OFFLOAD_DENY_WIN_ROOTS:
            if form == root or probe.startswith(root + "/"):
                return "system"
    return ""


def _assert_offload_dst_safe(dst: str) -> None:
    """Reject offload destinations that land inside OS-sensitive/executable dirs.

    Windows-correctness (2026-07-25): the deny roots are POSIX-anchored literals
    (/etc, /system, …). On Windows, Path('/etc').resolve() drive-anchors to
    'C:\\etc', which no longer string-matches '/etc' — silently opening the 403
    gate (tests/test_hardening_round4.py went 4-fail on windows-latest). Fix:
    evaluate the denylist over BOTH the raw (pre-resolve) literal AND the resolved
    canonical path, so a POSIX-absolute sensitive literal denies on any host; and
    add drive-agnostic Windows system/persistence roots. resolve() still catches
    '..' escapes; the raw pass catches the drive-anchor bypass."""
    expanded = Path(dst).expanduser()
    candidates = [str(expanded)]
    try:
        candidates.append(str(expanded.resolve()))
    except (OSError, ValueError):
        pass  # malformed dst (reserved name / illegal char) can make resolve() raise
              # on Windows; the raw pass above still guards and no write has happened.
    for candidate in candidates:
        reason = _offload_deny_reason(_norm_offload_path(candidate))
        if reason == "sensitive":
            raise HTTPException(403, "拒絕寫入系統敏感目錄（LaunchAgents / .ssh / cron / systemd 等）")
        if reason == "system":
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
