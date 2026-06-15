# arkiv UI Redesign — Claude Design deliverable (2026, design SSOT)

This directory is the **version-controlled visual record** of Claude Design's arkiv
UI redesign. It exists because the original deliverable (a 7.7 MB self-contained
React prototype) was never committed — only its design tokens were extracted into
`frontend/src/app.css`. These screenshots + this reference close that gap.

## Original deliverable (not in git — too large)

| | |
|---|---|
| **File** | `arkiv - UI redesign (standalone).html` |
| **Format** | Claude Artifact export — self-contained React + Babel bundle (~7.3 MB inline JS), renders 8 stacked "essays" at runtime |
| **Title** | `arkiv — UI redesign · vulture.s` |
| **Size** | 7,722,050 bytes |
| **sha256** | `c6b57ac78773a3f61db7ae87711cb57e489936f81fd91594f5892e7ea7d88886` |
| **Canonical store** | NAS vault — `/volume1/Hevin_AI_Data_Vault/attached/arkiv - UI redesign (standalone).html` (DSM Snapshot Replication backed up) |
| **Brief it answers** | `references/plans/arkiv/2026-05-26-ui-redesign-handoff-claude-design.md` (the handoff spec) |

To view interactively: open the original HTML in a browser, or re-render the PNGs
below with `temp/slice2.mjs` (playwright-core + system Chrome).

## Screens (the 8 essays)

| PNG | Section | Notes |
|---|---|---|
| `01-main-screens-stack.png` | Main view stack | Grid + Pool sidebar + Inspector, real-data styling (Bicycle Diaries) |
| `02-arkiv-ui-redesign-for-vulture-.png` | Cover | Title / brand mark |
| `03-detail-flows.png` | Detail flows | Inspector detail interactions |
| `04-modal-overlay.png` | Modal overlay | Dialog/overlay chrome |
| `05-round-2-main-view-state-varian.png` | Round 2 · Main view state variants | Empty / loading / selected states |
| `06-round-3-operational-flows.png` | Round 3 · Operational flows | **Ingest setup dialog** (Ingest N files · GB, transcribe/vision, START INGEST) — operational/DIT surface |
| `07-v1-5-preview-insta-360-indexin.png` | v1.5 preview · Insta360 indexing | **360 inspector** (equirect player + `.insv` metadata) — matches Phase 8.3b reproject |
| `08-round-4-edge-states.png` | Round 4 · Edge states | ws-error / update banner / splash |

> Each "Round" PNG is a **wide horizontal strip** (multiple artboards side by side, ~8760 px wide). Open at full size to read.

## Why this matters (status)

- This is the **authoritative "build-to" target** for the UI mock→live construction
  plan (`references/plans/arkiv/2026-06-15-...`). It **supersedes the CC-ported Svelte
  mock routes** (`frontend/src/routes/*` Seg artboards) — those are a second-hand port;
  this is the source.
- It is **React** (Claude Design's working medium). Per handoff §13, CC ports it to
  the locked stack (Svelte 4 + Vite + vanilla CSS). The design tokens are already in
  `app.css`; the screens are not yet ported.
- It **already covers DIT** (operational flows) and **360 indexing** — so the DIT-into-SPA
  work (plan S4 / review g3) is largely a *port of an existing design*, not new design.

## Provenance

- 2026-05-26 — handoff brief delivered to Claude Design.
- 2026-05-27 — tokens extracted → `app.css` ("drop-in from design-2026-05-27/tokens.css").
- 2026-06-15 — original located on NAS vault, screenshots committed here as the SSOT record.
