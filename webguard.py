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
        (home / "Movies").resolve(),   # macOS name
        (home / "Videos").resolve(),   # Windows / Linux name for the same folder
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
        "匯出路徑必須在批准的目錄下（Desktop / Documents / Downloads / Movies / Videos / temp，"
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

# Windows GetDriveTypeW() return value for a mapped network drive.
_DRIVE_REMOTE = 4
_IS_WINDOWS = os.name == "nt"


def _win_kernel32():
    """kernel32 via ctypes, or None off Windows. A function (not a module-level
    handle) so tests can substitute a fake without importing ctypes.windll on a
    POSIX host, where it does not exist."""
    if not _IS_WINDOWS:
        return None
    import ctypes
    return ctypes.windll.kernel32


def _windows_network_drives() -> list:
    """Drive letters ("Z:") of the currently mapped network drives on Windows —
    the Windows counterpart of a NAS share mounted under /Volumes on a Mac.

    GetLogicalDrives() + GetDriveTypeW() rather than os.listdrives(): the latter
    only exists from Python 3.12 and arkiv still supports 3.9. Only DRIVE_REMOTE
    counts; a fixed disk (C:), a USB stick or a CD drive is not a network drive and
    stays subject to the normal allowlist. Any failure answers [] — fewer roots,
    never more."""
    k32 = _win_kernel32()
    if k32 is None:
        return []
    out = []
    try:
        mask = int(k32.GetLogicalDrives())
        for i in range(26):
            if mask & (1 << i):
                letter = chr(ord("A") + i)
                if int(k32.GetDriveTypeW("{0}:\\".format(letter))) == _DRIVE_REMOTE:
                    out.append(letter + ":")
    except Exception:
        return []
    return out


def _drive_root(drive: str):
    """`Z:` → its resolved root. On Windows resolve() may hand back the UNC form
    (`\\\\nas\\home\\`) for a mapped drive; _is_bare_local_root accepts both."""
    return Path(drive + "\\").resolve()


def _is_bare_local_root(path, remote_drives=()) -> bool:
    r"""True when `path` is a filesystem root that would make the J1 bound a no-op.

    The old guard was simply `str(r) == r.anchor`, which also threw away the two
    roots a Windows NAS user actually needs: a mapped network drive is used at its
    root (`Z:\`), and a UNC share root (`\\nas\home\`) is an anchor too. Neither
    exposes THIS machine's filesystem — they are the same thing `/Volumes/home`
    is on a Mac — so they are allowed. Still refused:
      * `/`, `C:\` and any other local drive root (the original J1 case);
      * `\\host\C$\` — an admin share addresses a whole drive, local or not;
      * `\\?\…` / `\\.\…` device-namespace roots.
    `remote_drives` is the list from _windows_network_drives() ("Z:")."""
    s = str(path)
    if s != path.anchor:
        return False                       # not a root at all
    drive = getattr(path, "drive", "") or ""
    if drive.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        return True
    if drive.startswith(("\\\\", "//")):            # UNC share root
        share = drive.replace("/", "\\").rstrip("\\").split("\\")[-1]
        return len(share) == 2 and share[0].isalpha() and share[1] == "$"
    if len(drive) == 2 and drive[1] == ":":
        return drive.upper() not in {d.upper() for d in remote_drives}
    return True


def _split_extra_roots(raw) -> list:
    """`ingest.extra_roots` is one string: entries separated by ';' or newlines.
    ';' and not ':' or os.pathsep, because ':' is inside every Windows drive path
    and the same stored value must parse on whichever OS reads it."""
    out = []
    for chunk in str(raw or "").replace("\r", "\n").replace(";", "\n").split("\n"):
        chunk = chunk.strip().strip('"').strip()
        if chunk and chunk not in out:
            out.append(chunk)
    return out


def _extra_root_problem(entry: str) -> str:
    """'' if `entry` is acceptable as an extra ingest root, else why not (zh, for
    the settings 422). Shared by the PUT validation and the read side, so a value
    that got into the DB some other way is still filtered at use."""
    p = Path(entry).expanduser()
    if not p.is_absolute():
        return "不是完整路徑（要從磁碟機或 \\\\伺服器\\分享 開頭）：{0}".format(entry)
    if _is_bare_local_root(p, _windows_network_drives()):
        return "不能把整顆本機磁碟加入允許清單：{0}".format(entry)
    return ""


def _extra_ingest_roots() -> list:
    r"""Roots from the per-library setting `ingest.extra_roots` (admin-only to
    write: PUT /api/settings requires the `admin` scope). This is the path for a
    UNC folder — `\\nas\home\Photos` has no drive letter, so the network-drive
    sweep cannot find it. A settings/DB failure answers [] (fail closed)."""
    try:
        import settings as settings_store
        raw = settings_store.ingest_extra_roots()
    except Exception:
        return []
    roots = []
    for entry in _split_extra_roots(raw):
        if _extra_root_problem(entry):
            continue
        p = Path(entry).expanduser()
        try:
            roots.append(p.resolve())
        except OSError:
            # Windows realpath() raises on some UNC failures it does not treat as
            # "missing" — measured on real Windows: WinError 1326 (bad
            # credentials) for \\nas\home. Keep the literal absolute form; it is
            # what the target will be compared in when it resolves the same way.
            roots.append(Path(os.path.abspath(str(p))))
        except ValueError:
            continue
    return roots


def _allowed_ingest_roots() -> list:
    """Approved roots for ingest scan / ingest endpoints.

    Default: PROJECT_ROOT (where arkiv 's own DB lives) + standard user media
    locations (Movies on macOS, Videos on Windows/Linux) + network storage the OS
    has mounted: /Volumes/* on a Mac, mapped network drives (Z:) on Windows.
    ARKIV_INGEST_ROOTS env (os.pathsep-separated) REPLACES those defaults — the
    Docker deploy relies on that (tests/test_compose_deploy.py). The per-library
    setting `ingest.extra_roots` is ADDED on top of either.

    Codex Round-2 audit (J1): without bounds, /api/ingest/scan walked any path
    a Tailscale collaborator could supply, returning size + abs path of every
    media file — full filesystem inventory leak. That is why a local disk root
    (`/`, `C:\\`) is never allowed through the defaults or the setting.
    """
    extra = _extra_ingest_roots()
    custom = os.environ.get("ARKIV_INGEST_ROOTS", "").strip()
    if custom:
        # os.pathsep, not ':' — Windows uses ';' AND ':' appears in drive letters
        # (C:\...), so splitting on ':' shredded every Windows path.
        roots = [Path(p).expanduser().resolve() for p in custom.split(os.pathsep) if p.strip()]
        return roots + [r for r in extra if r not in roots]
    home = Path.home()
    roots = [
        config.PROJECT_ROOT.resolve() if config.PROJECT_ROOT else None,
        (home / "Desktop").resolve(),
        (home / "Documents").resolve(),
        (home / "Movies").resolve(),    # macOS
        (home / "Videos").resolve(),    # Windows / Linux
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
    # Windows mapped network drives (Z:) — the /Volumes equivalent. Added at their
    # root, which is an anchor; _is_bare_local_root lets a DRIVE_REMOTE root (and
    # the UNC share root resolve() may turn it into) through, never C:\.
    remote = _windows_network_drives()
    for drive in remote:
        try:
            roots.append(_drive_root(drive))
        except (OSError, ValueError):
            continue
    roots.extend(extra)
    # Final guard: never allow a bare LOCAL filesystem/drive root through (J1).
    out = []
    for r in roots:
        if r is None or _is_bare_local_root(r, remote) or r in out:
            continue
        out.append(r)
    return out


def ingest_forbidden_message(target) -> str:
    """The 403 body for a path outside the allowlist — written for the person who
    picked a NAS folder in the app, not for an operator: what happened, then the
    two things they can actually do. Deliberately does not list the resolved
    allowed roots (that leaks the home layout to anyone probing the boundary — the
    same reasoning as the export 403); the path echoed is the caller's own."""
    return (
        "這個資料夾不在 arkiv 允許匯入的位置：{0}\n"
        "如果是 NAS／網路資料夾：在 Windows 上先把它對應成網路磁碟機（例如 Z:），"
        "或到 設定 → 匯入 → 額外允許的資料夾 加入這個路徑"
        "（Mac 上先在 Finder 連上伺服器，掛在 /Volumes 底下就能直接匯入）。\n"
        "預設允許：這個庫的資料夾、Desktop、Documents、Movies／Videos、Pictures、已連上的網路磁碟。"
        "進階：ARKIV_INGEST_ROOTS 環境變數可整份取代預設清單。"
    ).format(target)


def _assert_ingest_path_safe(target: Path) -> None:
    roots = _allowed_ingest_roots()
    canonical = target.resolve()
    for root in roots:
        try:
            canonical.relative_to(root)
            return
        except ValueError:
            continue
    raise HTTPException(403, ingest_forbidden_message(target))


# ── same-site (CSRF) guard ───────────────────────────────────────────────────

_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))


def _raw_credential(headers, query_string: str = "") -> str:
    auth_header = headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):].strip()
    from urllib.parse import parse_qs
    return (parse_qs(query_string or "").get("token") or [""])[0].strip()


def _credentials_valid(headers, query_string: str = "", client_ip: str = ""):
    """Callable for same_site_violation: does the request carry a token that
    ACTUALLY validates (hash, expiry, IP allowlist)? Merely carrying `?token=x`
    or `Authorization: Bearer junk` used to exempt a request from the guard — a
    rebinding page is same-origin with itself and can set either (#498 review
    C1: `POST /api/client-log?token=x` from a rebound Host → 200). Evaluated
    lazily, only on the one branch that needs it."""
    def _check() -> bool:
        raw = _raw_credential(headers, query_string)
        if not raw:
            return False
        import auth
        try:
            auth.resolve_raw_token(raw, client_ip, headers.get("user-agent", ""),
                                   forwarded=any(h in headers for h in auth._PROXY_HEADERS))
            return True
        except Exception:
            return False
    return _check


def same_site_violation(headers, has_credentials=False):
    """Return a reason string when a browser request is cross-site, else None.

    Browsers attach Sec-Fetch-Site and/or Origin on cross-site requests;
    non-browser clients (curl, scripts, the Resolve plugin) send neither and pass.
    Origin == Host used to be accepted unconditionally as "same-origin for any
    deployment host" — but under DNS rebinding the attacker's page IS same-origin
    with itself (audit 2026-10-09 HIGH). It is now accepted only when that Host
    can't be a rebinding name (auth._host_is_trusted) or the request carries its
    own VALID token (then token auth, not loopback trust, decides).
    `has_credentials` is a bool or a zero-arg callable (evaluated lazily)."""
    sfs = headers.get("sec-fetch-site")
    if sfs and sfs not in ("same-origin", "same-site", "none"):
        return "cross-site (sec-fetch-site={0})".format(sfs)
    origin = headers.get("origin")
    if not origin:
        return None  # non-browser client
    if origin in _ALLOWED_ORIGINS:
        return None
    host = headers.get("host", "")
    if origin != "null" and origin.split("://", 1)[-1] == host:
        import auth  # lazy: keep webguard a leaf at import time
        if auth._host_is_trusted(host) or (has_credentials() if callable(has_credentials) else has_credentials):
            return None  # same-origin on a host that can't be a rebinding name
        return "untrusted host {0!r}".format(host)
    return "cross-site origin {0!r}".format(origin)


def _assert_same_site(request: Request) -> None:
    """audit M14: the no-body POSTs are CORS 'simple requests' — a malicious page
    can fire them cross-site WITHOUT a preflight, and loopback-trust then
    authorizes them. Kept for the routes that call it explicitly; every write is
    ALSO covered by SameSiteWriteGuard (server.py) since audit 2026-10-09."""
    query = getattr(getattr(request, "url", None), "query", "") or ""
    client = getattr(request, "client", None)
    ip = getattr(client, "host", "") or ""
    if same_site_violation(request.headers, _credentials_valid(request.headers, query, ip)):
        raise HTTPException(403, "cross-site request rejected")


class SameSiteWriteGuard:
    """ASGI middleware: the same-site check on EVERY non-GET/HEAD/OPTIONS request.

    Audit 2026-10-09 (MED): `_assert_same_site` was hand-attached to 13 of 52
    write routes; multipart upload, trash purge/restore, reingest, retry-vision,
    projects/sync were open to a cross-site <form> / no-cors fetch under loopback
    trust. Enforcing it here means a new route can't forget it. Pure ASGI (not
    BaseHTTPMiddleware) so streaming responses (offload ndjson) are untouched;
    WebSocket handshakes keep their own Origin check in routers/ingest.py."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method", "GET").upper() not in _SAFE_METHODS:
            from starlette.datastructures import Headers
            headers = Headers(scope=scope)
            qs = (scope.get("query_string") or b"").decode("latin-1")
            ip = (scope.get("client") or ("", 0))[0] or ""
            if same_site_violation(headers, _credentials_valid(headers, qs, ip)):
                from starlette.responses import JSONResponse
                resp = JSONResponse({"detail": "cross-site request rejected"}, status_code=403)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ── Content-Security-Policy for the SPA document ─────────────────────────────
#
# WHERE this policy takes effect (P4, 2026-10-17): the Tauri shell opens the
# window with `WebviewUrl::External("http://127.0.0.1:<port>")` (src-tauri/
# src/main.rs), so WKWebView loads the page straight from this uvicorn. Tauri's
# `app.security.csp` is only attached by Tauri's own asset protocol
# (tauri/src/protocol/tauri.rs → `asset.csp_header`) for *embedded* assets — it
# never touches a remote-URL page. So the CSP that actually governs the desktop
# app (and the browser UI at :8501) is the header sent HERE with index.html.
# CSP is enforced per document, so the header only needs to ride on "/".
_CSP_HOST_OK = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:[]"
)


def spa_csp(host: str = "") -> str:
    """CSP header value for the SPA shell.

    `host` is the request's Host header; it is echoed into connect-src as an
    explicit ws:// / wss:// source because older WebKit (macOS 12 = Safari 15,
    our minimumSystemVersion) does not let 'self' match WebSocket schemes. Only
    a plain host[:port] is accepted — anything else (a `;` would inject a
    directive) falls back to 'self' only.
    """
    # Tauri's IPC to the desktop shell is a fetch to its own custom protocol:
    # `ipc://localhost` on macOS/Linux, `http://ipc.localhost` on Windows (WebView2).
    # Without these the shell's first call is refused by this very policy and Tauri
    # falls back to postMessage after logging a CSP violation on every launch
    # (seen 2026-10-22 on the Windows build). Neither scheme reaches the network.
    connect = ["'self'", "ipc:", "http://ipc.localhost"]
    if host and len(host) <= 255 and all(c in _CSP_HOST_OK for c in host):
        connect += ["ws://" + host, "wss://" + host]
    directives = [
        "default-src 'self'",
        "script-src 'self'",                 # Vite bundle only; no eval/inline script
        "style-src 'self' 'unsafe-inline'",  # Svelte style= attrs / transitions
        "img-src 'self' data: blob:",        # data: favicon + CSS data URIs; blob: textures
        "media-src 'self' blob:",            # /api/stream video, waveform audio
        "font-src 'self' data:",             # bundled @fontsource woff/woff2
        "connect-src " + " ".join(connect),  # fetch /api/* + ws /ws/ingest
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    return "; ".join(directives)
