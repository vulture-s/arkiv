# Assemble the self-contained backend bundle for the arkiv desktop app -- Windows.
#
# Windows counterpart of assemble-backend.sh. Same shape, same output contract:
# produces src-tauri\backend\{python,site-packages,src}, which tauri.conf.json
# bundles as an app resource. Run this ONCE before `cargo tauri build` (and again
# whenever the deps or the SPA change).
#
# The backend is python-build-standalone (a portable, self-contained CPython) +
# an already-built site-packages + the arkiv Python source + the built SPA. It is
# deliberately NOT PyInstaller, for the reason recorded in the .sh: the spike
# (2026-07-17) showed the native-heavy tree imports and boots cleanly under a
# stock portable interpreter, sidestepping the hidden-import/DLL whack-a-mole
# PyInstaller would demand for this dep set.
#
# Three things differ from the .sh beyond path separators:
#
#   1. The venv layout is `Lib\site-packages`, not `lib/python3.12/site-packages`.
#   2. The source venv defaults to .venv-pack, NOT .venv. On a dev box with an
#      NVIDIA card `pip install -r requirements.txt` resolves silero-vad's torch
#      to the +cuXXX build -- 4.2 GB against ~250 MB for the CPU wheel, measured
#      on the Win11/RTX 4070 box on 2026-08-12 -- and all of it would land in the
#      installer. torch here only backs silero VAD (transcribe.py:14-15);
#      transcription itself runs on ctranslate2, so the CPU build costs nothing
#      a user would feel. Build .venv-pack with:
#        py -3.12 -m venv .venv-pack
#        .venv-pack\Scripts\pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
#        .venv-pack\Scripts\pip install -r requirements.txt
#      Override with $env:ARKIV_PACKAGING_VENV to point somewhere else.
#   3. Copies go through robocopy rather than rsync -- it is native on every
#      Windows box and on the windows-latest runner, so the script has no
#      Git-Bash/MSYS dependency.
#
# The staging dir is git-ignored (it is ~1 GB); this script rebuilds it.
#
# KEEP THIS FILE PURE ASCII. Windows PowerShell 5.1 reads a BOM-less file as the
# machine's ANSI codepage, not UTF-8, so a non-ASCII byte sequence is re-split
# under e.g. Big5 (cp950) or cp1252. When the re-split swallows a quote the whole
# script dies at PARSE time with errors pointing at unrelated lines -- and it
# does so only on machines with that codepage, which is the worst possible way to
# find out. An ellipsis next to a quote cost a debugging round here on
# 2026-08-12. Use `--` and `->` rather than the typographic characters.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$HERE    = Split-Path -Parent $MyInvocation.MyCommand.Path   # src-tauri\
$REPO    = Split-Path -Parent $HERE                          # repo root
$BACKEND = Join-Path $HERE 'backend'

$PY_VERSION = '3.12.13'
$PBS_TAG    = '20260623'
# NOTE the %2B -- the asset name contains a literal '+' which must stay encoded in the URL.
$PBS_URL    = "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PY_VERSION%2B$PBS_TAG-x86_64-pc-windows-msvc-install_only.tar.gz"

$PACK_VENV = if ($env:ARKIV_PACKAGING_VENV) { $env:ARKIV_PACKAGING_VENV } else { Join-Path $REPO '.venv-pack' }
$VENV_SP   = Join-Path $PACK_VENV 'Lib\site-packages'

# robocopy signals detail through its exit code: 0-7 are success (1 = files were
# copied, 2 = extra files in dest, 4 = mismatched files), 8+ are real failures.
# Treating it like a normal command would fail the script on a successful copy --
# and, worse, leave $LASTEXITCODE non-zero for whatever reads it next (CI does).
function Invoke-Robocopy {
    param([string]$Source, [string]$Dest, [string[]]$ExtraArgs)
    $argv = @($Source, $Dest, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:2', '/W:1') + $ExtraArgs
    & (Get-SystemTool 'robocopy.exe') @argv | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        throw "robocopy failed (exit $rc) copying '$Source' -> '$Dest'"
    }
    $global:LASTEXITCODE = 0
}

# Resolve curl/tar from System32 by ABSOLUTE path rather than trusting PATH.
# Git for Windows ships MSYS twins of both in `Git\usr\bin`, and when this script
# is launched from a Git Bash shell those come first -- at which point GNU tar
# reads the `C:\...` destination as a remote `host:path` and dies with
# "Cannot connect to C: resolve failed" (observed 2026-08-12 on the Win11 box).
# Windows' own tar is bsdtar, which handles both drive paths and .tar.gz natively.
function Get-SystemTool {
    param([string]$Name)
    $p = Join-Path $env:SystemRoot "System32\$Name"
    if (-not (Test-Path $p)) {
        throw "$p not found -- this script needs the Windows-native $Name (Windows 10 1803+), not an MSYS/Git twin."
    }
    return $p
}

function Get-DirSizeMB {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return 0 }
    $bytes = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { return 0 }
    return [math]::Round($bytes / 1MB)
}

if (-not (Test-Path $VENV_SP)) {
    Write-Error @"
$VENV_SP not found.

Build the packaging venv first (CPU-only torch -- see the header of this script for why):
  py -3.12 -m venv .venv-pack
  .venv-pack\Scripts\pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
  .venv-pack\Scripts\pip install -r requirements.txt

Or point `$env:ARKIV_PACKAGING_VENV` at an existing CPU-only venv.
"@
    exit 1
}

Write-Host "[assemble] clean staging: $BACKEND (preserving tracked .gitkeep)"
foreach ($d in 'python', 'site-packages', 'src') {
    $p = Join-Path $BACKEND $d
    if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path (Join-Path $BACKEND 'src') | Out-Null

Write-Host "[assemble] portable python $PY_VERSION (python-build-standalone $PBS_TAG)"
$TMP = Join-Path ([System.IO.Path]::GetTempPath()) ("arkiv-pbs-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $TMP | Out-Null
try {
    $tarball = Join-Path $TMP 'pbs.tar.gz'
    # curl.exe / tar.exe (System32, see Get-SystemTool) -- not the PowerShell
    # aliases, which resolve to Invoke-WebRequest / nothing.
    & (Get-SystemTool 'curl.exe') -fsSL $PBS_URL -o $tarball
    if ($LASTEXITCODE -ne 0) { throw "curl failed (exit $LASTEXITCODE) fetching $PBS_URL" }
    & (Get-SystemTool 'tar.exe') -xzf $tarball -C $TMP          # extracts .\python\
    if ($LASTEXITCODE -ne 0) { throw "tar failed (exit $LASTEXITCODE) extracting $tarball" }
    Move-Item -LiteralPath (Join-Path $TMP 'python') -Destination (Join-Path $BACKEND 'python')
} finally {
    Remove-Item -LiteralPath $TMP -Recurse -Force -ErrorAction SilentlyContinue
}

$pyExe = Join-Path $BACKEND 'python\python.exe'
if (-not (Test-Path $pyExe)) { throw "expected $pyExe after extracting the PBS tarball" }

Write-Host "[assemble] site-packages from $PACK_VENV (trim caches + chromadb transitives arkiv never imports at runtime)"
# arkiv embeds via ollama, not chromadb's built-in onnx/k8s embedding paths, so
# kubernetes\ and onnxruntime\ (~155 MB) are dead weight in the bundle. Drop
# bytecode caches too. If a future feature needs them, remove the excludes.
Invoke-Robocopy -Source $VENV_SP -Dest (Join-Path $BACKEND 'site-packages') -ExtraArgs @(
    '/XD', '__pycache__', 'kubernetes', 'onnxruntime',
    '/XF', '*.pyc'
)

Write-Host "[assemble] arkiv source (runtime .py + routers + built SPA; no tests/docs/venv)"
# The server imports many top-level modules + the routers package, and serves the
# SPA from .\frontend\dist relative to its cwd. Copy generously (repo minus the
# heavy/irrelevant dirs) so no runtime import is missed.
#
# Bare names in /XD match a directory of that name at ANY depth; everything the
# repo also has a legitimate copy of is passed path-scoped instead, since `src`
# and `dist` name dirs we need to keep (frontend\dist is the SPA).
#
# The gitignored runtime-scratch dirs (temp\, thumbnails\, proxies\, waveforms\,
# .arkiv\, chroma_db\) are excluded for two reasons. Obviously they are a dev
# box's local data and have no business in a shipped bundle. Less obviously,
# `temp\` is what made this copy fail on 2026-08-12: the offload tests leave
# `temp\arkiv-offload-*` dirs behind with ACLs the current user can no longer
# enumerate, and 20 of them turned into robocopy's "directories failed" count ->
# exit 9. A CI runner checks out clean and never sees them, so this only ever
# bites a real workstation -- the one machine that also does the release build.
# .env* is excluded because this is a DENYLIST, and a denylist that misses a
# secret is a silent leak rather than a loud failure: install.sh does
# `cp .env.example .env`, and .env.example tells you to fill in
# ARKIV_TOKEN_HMAC_KEY, ARKIV_ADMIN_BOOTSTRAP_TOKEN and ARKIV_PG_DSN (which
# carries a DB password). Without it a release built on a real workstation ships
# that file inside the .exe/.msi for anyone to read. .dockerignore already
# excludes .env; this is the same discipline for the Tauri path.
# .env.example goes too -- nothing at runtime reads it (config._load_env only
# looks for .env), so shipping it only widens the pattern we have to police.
# bench_*.json is called out in .gitignore as containing private transcripts.
Invoke-Robocopy -Source $REPO -Dest (Join-Path $BACKEND 'src') -ExtraArgs @(
    '/XD',
    (Join-Path $REPO '.git'),
    (Join-Path $REPO '.venv'),
    (Join-Path $REPO '.venv-pack'),
    (Join-Path $REPO 'src-tauri'),
    (Join-Path $REPO 'tests'),
    (Join-Path $REPO 'docs'),
    (Join-Path $REPO 'frontend\node_modules'),
    (Join-Path $REPO 'frontend\src'),
    (Join-Path $REPO '.arkiv'),
    (Join-Path $REPO 'chroma_db'),
    (Join-Path $REPO 'temp'),
    (Join-Path $REPO 'thumbnails'),
    (Join-Path $REPO 'proxies'),
    (Join-Path $REPO 'waveforms'),
    (Join-Path $REPO '.tmp-camera-report-tests'),
    'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache',
    '/XF', '*.pyc', '.env', '.env.*', 'bench_*.json'
)

# Fail loud rather than ship quietly. Not redundant with /XF above: robocopy is
# not run with /MIR here, so a .env copied in by an OLDER build of this script is
# still sitting in $BACKEND\src and would ship even now that the exclude exists.
# A denylist only stops the patterns someone remembered; this stops the class.
#
# One list, one scan. The previous version hard-coded '.env*' twice, so the guard
# covered the class it named and nothing else -- the same "only what someone
# remembered" failure it exists to prevent, one level up. Adding a pattern is now
# a single line here, and the error names which pattern caught the file.
$NeverShip = @(
    '.env*'         # secrets -- see the /XF note above
    'bench_*.json'  # dev-machine benchmark logs: GPU model + the filenames of
                    # whatever was last ingested. On a real workstation that is a
                    # list of client media. Added to /XF on 2026-08-20 (#341), but
                    # the reason above is exactly why /XF is not enough: a staging
                    # dir assembled BEFORE that still has one sitting in it.
)
$srcDir = Join-Path $BACKEND 'src'
$anyLeaked = $false
foreach ($pat in $NeverShip) {
    $hits = @(Get-ChildItem -LiteralPath $srcDir -Recurse -Force -File -Filter $pat -ErrorAction SilentlyContinue)
    if ($hits.Count -eq 0) { continue }
    $anyLeaked = $true
    Write-Host "ERROR: '$pat' matched inside the assembled backend -- refusing to ship." -ForegroundColor Red
    $hits | ForEach-Object { Write-Host "  $($_.FullName)" -ForegroundColor Red }
}
if ($anyLeaked) {
    Write-Host "       Delete them from $srcDir and re-run. If this is a new file" -ForegroundColor Red
    Write-Host "       class that must never ship, add it to BOTH the /XF list above" -ForegroundColor Red
    Write-Host "       and `$NeverShip here (and to assemble-backend.sh)." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $BACKEND 'src\frontend\dist\index.html'))) {
    Write-Warning "frontend\dist\index.html missing -- run 'cd frontend; npm run build' first,"
    Write-Warning "      or the packaged app will serve the fallback page."
}

Write-Host "[assemble] done:"
foreach ($d in '', 'python', 'site-packages', 'src') {
    $p = if ($d) { Join-Path $BACKEND $d } else { $BACKEND }
    "{0,8} MB  {1}" -f (Get-DirSizeMB $p), $p | Write-Host
}
Write-Host "[assemble] next:  cargo tauri build   (from src-tauri\)"
exit 0
