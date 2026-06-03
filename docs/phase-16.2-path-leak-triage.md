# Phase 16.2 — API path-leak triage

Round-3 goal: read-scope API responses shouldn't hand a client the operator's
absolute filesystem layout. A Codex sweep surfaced more absolute-path-in-response
sites than the roadmap's named targets. This is the triage — what got fixed, and
why the rest is out of 16.2 scope.

## Fixed (16.2)

| Endpoint | Fix |
|---|---|
| `/api/media`, `/api/media/{id}`, detail frames | `_display_path`: PROJECT_ROOT-relative, basename if absolute-outside-root |
| `/api/media/{id}/scenes` | already emits `/thumbnails/<basename>` only |
| `/api/media?q=` (semantic + SQL/tag fallback) | goes through `_resolve_record` |
| `/api/cache/info` | dropped the absolute `path` field (kept sizes/counts) |
| `/api/search/all` (federation) | boundary sanitize: drop `absolute_path`, `path`→`_display_path`, `project_path`→basename (incl. the absolute-`relative_path` edge) |

## By design — NOT changed (changing them breaks the feature)

| Site | Why it must keep the absolute path |
|---|---|
| FCPXML export (`/api/media/{id}/export/fcpxml`, `/api/export/timeline/fcpxml`) | The `file://` media URI is how an NLE (FCP/Resolve) relocates the source clip. A relative/basename URI would make the export fail to conform. This is the format's contract, not a leak — and it requires `media_read` + the user is exporting their own library to their own NLE. |
| `/api/export/metadata-csv-to`, `/api/media/{id}/export-to` success JSON | Echoes back the destination path **the client itself supplied** in the request — not disclosure of unknown structure. |

## Deferred — real but lower-priority, separate effort

| Site | Note |
|---|---|
| `/api/projects`, `/api/projects/health` | Return each project's root path to `projects_read`. The projects API is inherently about roots, and `projects_read` is a more privileged scope than `videos_read`; sanitizing here needs a product call on what the projects UI should display. |
| export-safety 403 body (`_assert_export_dest_safe`) | A rejected export lists the allowed absolute roots to help the caller. Minor disclosure to `media_read`; reword to relative/labels if tightened. |
| `/api/media/{id}/reingest` missing-file error | Includes the resolved absolute path, but requires `ingest_write` (privileged / loopback owner). Not a read-scope leak. |

## Verdict

The roadmap's named 16.2 targets (media / scenes / cache) **plus** the federation
search leak are closed and tested. The remaining items are either format-required
(FCPXML), client-supplied echoes, or behind more-privileged scopes — they belong
to a future "API path-disclosure hardening" pass, not 16.2. 16.1 (token hash
salt) remains the other open round-3 item — see `phase-16.1-token-hash-decision.md`.

*Triage 2026-06-03, security round 3.*
