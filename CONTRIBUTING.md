# Contributing to arkiv

Thanks for your interest in contributing! arkiv is a local-first media asset manager for filmmakers.

## Development Setup

### Prerequisites

- Python 3.9+
- FFmpeg 6.0+
- [Ollama](https://ollama.com/) with `nomic-embed-text` model
- Node 20+ (the UI is a Svelte SPA that `server.py` serves as a built bundle)
- Git

### Getting Started

```bash
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Whisper backend (pick one)
pip install mlx-whisper          # macOS Apple Silicon
pip install faster-whisper torch  # NVIDIA GPU

# Ollama models
ollama pull nomic-embed-text
ollama pull qwen3-vl:8b

# Build the UI (server.py serves frontend/dist at /)
cd frontend && npm ci && npm run build && cd ..

# Verify environment
python health.py
```

### Running

```bash
uvicorn server:app --host 0.0.0.0 --port 8501 --reload
```

Open <http://127.0.0.1:8501> — `server.py` serves the built SPA from `frontend/dist`. Without
a build, `/` only shows a "run npm run build" hint.

Working on the UI? Run Vite's dev server alongside the backend for hot reload:

```bash
cd frontend && npm run dev   # http://127.0.0.1:5173
```

It proxies `/api`, `/thumbnails` and `/ws` to the backend on :8501, so keep both running.
Rebuild with `npm run build` before checking the production path at :8501.

### Git hooks

Enable the repo's pre-commit checks once per clone:

```bash
bash scripts/install-hooks.sh   # points core.hooksPath at .githooks/
```

The pre-commit hook blocks two easy-to-miss landmines: `DO NOT COMMIT` / `NOCOMMIT`
markers left in code, and a hardcoded or wide-open Vite dev-server `allowedHosts`
(it must be sourced from a gitignored `frontend/.env.local` — see `frontend/vite.config.js`).
Bypass in an emergency with `git commit --no-verify`.

## How to Contribute

### Reporting Bugs

Use the [Bug Report](https://github.com/vulture-s/arkiv/issues/new?template=bug_report.md) template. Include:
- Your OS and Python version
- Steps to reproduce
- Expected vs actual behavior
- `python health.py` output if relevant

### Suggesting Features

Use the [Feature Request](https://github.com/vulture-s/arkiv/issues/new?template=feature_request.md) template.

### Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Test locally: ingest a file, search, verify UI
4. Submit a PR with a clear description

### Commit Messages

Use conventional commits:

```
feat: add ExifTool metadata extraction
fix: handle Unicode paths on Windows
docs: update Quick Start section
chore: update requirements.txt
```

## Code Style

- Python: follow existing patterns in the codebase
- Keep modules small and focused — each `.py` file has a single responsibility
- Use `config.py` for all configurable values (never hardcode paths/URLs)
- Frontend: Svelte 4 + Vite 5 (`frontend/`). No CSS framework — styling goes through the
  design tokens in `frontend/src/app.css` (CSS custom properties); don't introduce Tailwind
  or another framework without discussing it first. Fonts are self-hosted via `@fontsource`.
- Before pushing frontend changes, run `npm run check` (svelte-check with
  `--fail-on-warnings`) — CI blocks on it, and the baseline is currently zero warnings

## Testing & Coverage

Run the suite with `pytest -q`. CI runs it on macOS (Python 3.9 + 3.12) plus a scoped
Windows correctness leg — those are the blocking correctness gates.

**Coverage is a non-blocking regression ratchet, not a quality bar.** The `coverage` CI
job is `continue-on-error` on purpose:

- The percentage (~63%) is *flattered* — `tests/conftest.py` fakes the heavy backends
  (torch, chromadb, mlx_whisper, whisperx, …) via `sys.modules`, so those branches never
  execute and the denominator shrinks. Treat it as a relative ratchet, not an absolute claim.
- The floor (`--cov-fail-under=55`) sits conservatively below the measured ~63%.
- The MCP stdio e2e (`tests/test_mcp_e2e.py`, marked `subprocess_stdio`) is **excluded from
  the `--cov` leg** (`-m "not subprocess_stdio"`). It spawns a real subprocess; under
  coverage the parent-side tracer slows the stdio pump so the async handshake/teardown flakes
  against its `anyio.fail_after` bound. That is the same F3 MCP-SDK×OS stall tracked in the
  health-hardening handoff — not a coverage bug — and the child is un-instrumented, so
  excluding it costs ~0 coverage. The `test` job still runs it (no filter).

**Flip-to-blocking condition:** make coverage a required check only once (a) the F3 MCP stdio
stall has a root-cause fix (so the e2e can rejoin the `--cov` leg) and (b) coverage is
re-measured on a matrix rather than the single `macos-latest` runner. Until then it stays a
ratchet.

## Dependency updates

Dependencies are updated **deliberately, one bump per PR**, so every change carries the
upstream changelog and a one-click rollback:

- **Dependabot** (`.github/dependabot.yml`) opens a weekly PR per bump for pip, npm, cargo,
  GitHub Actions, and Docker base images — each PR links the upstream release notes.
- **Before merging a bump**: let CI's required checks run (they exercise the real
  install / build / audit), and skim the changelog for breaking changes. Add a
  `CHANGELOG.md` entry for anything user-facing.
- **Rollback** = revert the bump PR (that's why bumps stay one-per-PR, not batched).
- **Base-image digests**: `Dockerfile` and `docker-compose.yml` pin images by `@sha256` for
  reproducible rebuilds; dependabot's `docker` ecosystem refreshes those digests — merge the
  dependabot PR rather than hand-editing (or run `docker buildx imagetools inspect <image>`
  for a fresh digest if you must).
- Python deps stay in `requirements.txt` (flat, no lockfile): the 3.9×3.12 matrix + platform
  markers (`mlx-whisper`@Darwin / `faster-whisper`@Linux, `opencc`/`mcp` gated ≥3.10) mean a
  single frozen lockfile can't honestly represent all environments. The `dependency-audit`
  gate + deliberate bumps are the reproducibility contract instead.

## Releases

arkiv ships as a macOS (Apple Silicon) app. Releases are cut from `main` by tag:

1. Move `## Unreleased` in `CHANGELOG.md` to `## vX.Y.Z - YYYY-MM-DD`; leave a fresh
   `## Unreleased` on top.
2. Bump the version to `X.Y.Z` in **both** `src-tauri/tauri.conf.json` and
   `src-tauri/Cargo.toml` so it matches the tag (these historically drifted — stuck at 0.2.0
   across the whole v0.2→v0.10 tag history, so every built DMG embedded 0.2.0).
3. Commit (`docs(changelog): cut vX.Y.Z`), then push an **annotated** tag: `git tag -a vX.Y.Z -m …`.
4. The tag-triggered `release.yml` workflow builds the `.app`/`.dmg` on a macOS-arm runner. It
   **stamps the version from the tag**, so the bundle can't drift from the tag; signs +
   notarizes if the Apple secrets are configured (else builds unsigned — the app already ships
   with a documented right-click→Open Gatekeeper step); and uploads the artifact to the Release.

**Release-artifact matrix:**

| Target | Artifact | Signed | Status |
|---|---|---|---|
| macOS arm64 (Apple Silicon) | `.app` + `.dmg` | yes, if Apple secrets set | supported |
| macOS x86_64 (Intel) | — | — | N/A — `assemble-backend.sh` bundles an aarch64-apple-darwin Python |
| Windows | — | — | deferred — mac-arm-locked backend; `mlx-whisper` has no Windows wheel |

Rollback = the prior tag's artifact; delete the GitHub Release (and tag) if a release is bad.

## Project Structure

[`ARCHITECTURE.md`](ARCHITECTURE.md) is the authoritative map — every backend module, the
frontend routes, and how data flows between them. The list below is just orientation for a
first change:

```
server.py         — FastAPI app: ~70 REST endpoints, and serves the built SPA
config.py         — Centralized configuration
db.py             — SQLite data layer
vectordb.py       — ChromaDB + Ollama embeddings
ingest.py         — Media pipeline: probe → thumbnail → transcribe → vision → embed
transcribe.py     — Whisper transcription + anti-hallucination guard
frames.py         — FFmpeg frame extraction (incl. 360/dual-fisheye)
vision.py         — Ollama vision analysis (JSON output)
health.py         — Environment health check
frontend/src/     — Svelte SPA: routes/ (screens), lib/ (shared), app.css (design tokens)
docs/             — API reference, pipeline notes, ADRs
```

Backend modules sit flat at the repo root (~37 of them). The ones above are the files you're
most likely to touch first, not the full set.

## License

arkiv is licensed under the **PolyForm Noncommercial License 1.0.0**, with a **Commercial
Output Exception** — see [LICENSE](LICENSE). In short: arkiv itself is source-available and
free for noncommercial use, while whatever you *make* with it (videos, timelines,
EDL/FCPXML/OTIO exports) is yours to use commercially.

### Contributor terms

By submitting a contribution — a pull request, a patch, or code posted in an issue — you
confirm that:

1. **It's yours to submit.** You wrote it, or you otherwise have the right to contribute it,
   and you are not knowingly including code owned by someone else under incompatible terms.
   Using an AI assistant to write or package your change is fine; the same condition applies
   to what it produced for you.
2. **It ships under the project's license.** Your contribution is licensed to the project and
   its users under the same terms as arkiv itself (PolyForm NC 1.0.0 + Commercial Output
   Exception).
3. **The maintainer may relicense it.** You grant Hevin Yeh — the copyright holder named in
   [LICENSE](LICENSE) — a perpetual, worldwide, irrevocable, royalty-free right to license
   your contribution under different terms, including proprietary ones. This is what lets
   arkiv's licensing evolve (for example, if a component is later offered commercially)
   without having to track down every past contributor for permission.

You keep the copyright to what you wrote — point 3 is a license you grant, not a transfer of
ownership. Merged contributions are credited in the repository's contributor list; if you'd
like to be credited under a different name than your Git identity, just say so in the PR.
