# ADR 0001 — DIT offload wrapper (build the UX, not the engine)

- **Status:** Accepted (2026-06-11, greenlit); all four candidates ①②③④ shipped
  (④ landed 2026-08-07 — this record previously claimed it shipped in v0.8.1, which
  was never true; see the correction under Decision ④)
- **Phase:** roadmap 13.4–13.6 (DIT wrapper)
- **Supersedes/relates:** Phase 13.1–13.3 (ASC MHL v2 / multi-dst offload / camera report)

## Context

影視颶風's **Gate.exe** (a Windows DIT offload tool) was reverse-engineered in the
2026-06-10 `worktree-dit` dev-log §12 (the repo `mediastormDev/mediastorm-assistant`
has since been pulled from GitHub). The teardown asked: does arkiv need to build a
DIT offload engine to be a credible "DIT companion"?

**Finding: no — arkiv's `offload.py` engine is already stronger than Gate.** It has
xxh3-128 hashing, multiple destinations, ASC MHL v2 generation/verify, and resumable
byte-verified copies (Phase 13.1–13.3). Gate's actual pain points were **UX, not
engine**: an Electron shell that janks while copying a 44 GB card, and weak
naming/folder logic. The competitive gap is the *wrapper experience*, not the
copy/verify core.

A same-day test (StoryCube, ASUS×GoPro) confirmed the category vacuum on the
professional-archive side — see `references/case-studies/arkiv/mediastorm-gate-teardown-20260611.md`
and `storycube-asus-gopro-20260612.md`.

## Decision

Build a thin DIT **wrapper** on top of the existing `offload.py` engine, with one
hard constraint borrowed from what Gate got wrong: **never jank** (the UI must stay
smooth while a large card copies). Four candidates, engine-before-UI:

1. **Naming / folder policy in `offload.py`** — configurable `--organize "{date}/{camera}/{reel}"`
   template with live source→dest preview, token sanitisation (path-traversal safe),
   case-fold collision REFUSE, dry-run, resume. **Shipped.** (Solves Gate's worst part.)
2. **Card-watcher** — `--watch` auto-offloads on card insert (copy-only, never deletes
   source; raw-mount tracking to debounce re-trigger flicker; empty-raw guard).
   **Shipped.** Depends on ①.
3. **This ADR** — the decision record, so the "why build a DIT wrapper at all" reasoning
   is findable and we don't re-litigate it. (You are here.)
4. **Format whitelist `.mxf`** — Sony XAVC `.mxf` probes + extracts `start_tc`; added to
   `mediatypes.VIDEO_EXT` (hence `ingest.SUPPORTED`) so FX6/FX9/Venice index.
   `.braw`/`.r3d`/`.ari` need vendor SDKs (ffmpeg has no decoder) — out of scope.
   **Shipped 2026-08-07.** Note: the offload *copy* layer is format-agnostic; the
   whitelist gap was only in the *index* layer.

   > **Correction (2026-08-07).** This entry read "**Shipped (v0.8.1)**" from the day
   > the ADR was written, and the Status line above said ①②④ shipped. Neither was
   > true: `.mxf` was never added to the whitelist, and `ingest.py` in fact listed it
   > in `_PRO_UNSUPPORTED_EXT` alongside `.braw`/`.r3d`, so Sony cards indexed as zero
   > files for ~2 months while three separate documents recorded the feature as done.
   > The misclassification was over-broad: `.mxf` is a *container*, not a codec, and
   > ffmpeg decodes what Sony wraps in it. Verified before landing, whole chain
   > (probe + thumbnail + frames + the 16 kHz mono audio decode whisper consumes) on
   > two samples: `samples.ffmpeg.org/MXF/C0023S01.mxf` (Sony 2006, MPEG-4 part 2,
   > `start_tc 01:43:48:21` from format-level tags) and a synthesised XAVC-Intra file
   > (H.264 High 4:2:2 all-I, 1920×1080, `start_tc 01:00:00:00`). An MXF whose inner
   > codec ffmpeg cannot decode degrades exactly as any unreadable file does —
   > `probe()` returns `None` and the clip is skipped — so admitting the container
   > adds no new failure mode. `.m2ts` was left in `_PRO_UNSUPPORTED_EXT`: it is also
   > a container, but frame extraction failed on the sample tested, so it needs its
   > own measurement rather than being swept along with this one.

The DIT Offload UI is the 7th flow in the existing 6-screen Svelte baseline
(`/#/offload`, ported from the old standalone island — see the Svelte cutover Phase 3),
with progress streamed as ndjson so the UI stays responsive mid-copy.

## Consequences

- arkiv markets as a **DIT companion** on a true engine, not vaporware — the话術
  upgrade from "AI metadata layer for DIT workflows" (soft) to "DIT companion" (hard)
  is now backed by shipped code.
- Maintenance surface stays small: the wrapper reuses `offload.py`; no second copy
  engine to keep correct.
- **Out of scope** (documented, not silently dropped): cinema-RAW decode (`.braw`/`.r3d`/
  `.ari`) needs vendor SDKs; `dit.py classify`/`full` one-button orchestration and
  per-clip `.arkiv.json` sidecar export remain future items.

## References

- `references/plans/arkiv/2026-06-11-dit-wrapper-ui-requirements.md` (UI requirements R0–R4)
- `references/case-studies/arkiv/mediastorm-gate-teardown-20260611.md`
- `media-asset-manager-plan.md` §11
