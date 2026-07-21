# Frame-exact IN/OUT — spike result & roadmap

Status: **P1 + P2 shipped.** The spike is the shipped behaviour and the marks are
first-class (persisted, honoured by export). P3 (WebCodecs) is deliberately out of
scope until measured.

## Shipped so far

| item | where |
|---|---|
| Spike core — rVFC frame marking, `◂格/格▸` step, `HH:MM:SS:FF` readout, `frameExact` gate | #190 |
| Keyboard shortcuts — `,`/`.` step, `i`/`o` mark (guarded against inputs / modifiers) | **this branch** |
| Persist IN/OUT per clip (survives clip-switch/reload) | D1 · #193 |
| Timeline export lays the MARKED sub-clip, not the whole file | D2 · #194 |
| Proxy hardware-decode on Apple Silicon (adjacent perf win) | D3 · #195 |
| `_edl_timecode` NDF/DF + fcpxml rational (29.97/23.976) pinned by test | `tests/test_r5_25_export_builders_module.py` |

**Still open (minor P2 polish, not blocking):** the frame-exact readout is
`00:00:00:00`-relative — it does not yet offset by the camera-body `start_tc`
(already ingested, already passed to the metadata grid as `tc`), so the displayed
TC won't match the camera. Tracked below under P2.

## Context

The inspector's IN/OUT trim marks (`Inspector.svelte`, `setIn`/`setOut`) read
`playerEl.currentTime` — a float in seconds that snaps to wherever the browser's
decoder happened to land. For a DAM whose whole job is precise handoff to an
edit (EDL/FCPXML/SRT export already does frame timecode math in
`export_builders.py`), "off by a frame" is a real annoyance for the editor.

This came out of exploring whether the WebCodecs techniques from
[pixel-point/aval](https://github.com/pixel-point/aval) apply to arkiv.
Frame-exact IN/OUT was the highest-value, lowest-risk fit.

## The key decision: native, not WebCodecs

**Frame-exact IN/OUT does not need WebCodecs.** The spike is entirely native:

- Seeking an H.264 `<video>` to a time IS frame-accurate (just not instant).
- `requestVideoFrameCallback`'s `mediaTime` reports the exact presentation time
  of the frame on screen, so we always know which frame we're looking at.
- The precise fps is already in the DB (`media.fps REAL`, set at ingest from
  `r_frame_rate`), already in the light query (`LIGHT_COLS`), and already
  threaded to the frontend (`_raw.fps`).
- Export already accepts `?in_s/out_s` seconds and does the timecode math, so a
  mark expressed as `frame/fps` seconds needs **zero backend change**.

No new dependency, no demuxer, no WebCodecs. That is the honest tool for this
job. WebCodecs would only earn its weight for two *later* things, both out of
scope here: fast multi-frame scrubbing (decode-forward beats per-frame seek
latency), and a seamless trim-loop preview (the AVAL seekless-loop trick).

## What the spike does

Frontend-only, ~70 lines, all gated behind `frameExact = useVideo && fps > 0`
so audio and unknown-fps clips (and every design-mock screen) are untouched.

- `MainLive.svelte`: adds `fpsExact: selected._raw.fps` (unrounded — rounding
  23.976→24 would drift frame boundaries over a long clip) and passes it as
  `fps` to `<Inspector>`.
- `Inspector.svelte`:
  - a self-re-arming `requestVideoFrameCallback` loop maintains `curFrame`
    (`round(mediaTime * fps)`) — the frame actually on screen;
  - `setIn`/`setOut` mark at `curFrame / fps` (exact frame boundary in seconds,
    export-compatible) instead of raw `currentTime`;
  - `◂格 / 格▸` step one frame by seeking to the frame **midpoint**
    `(f + 0.5) / fps` (nudging past the frame edge so float/PTS rounding can't
    land on the neighbour);
  - timecode readout upgrades to `HH:MM:SS:FF`, plus a live `f{n}` / fps badge;
  - IN/OUT duration shown in frames.

## Verification

All against a real Chromium (Playwright) with a test asset whose frame number is
**burned into the pixels** as a binary strip, read back with `getImageData` as
ground truth — so a mismatch between what the code thinks and what is on screen
is caught, not assumed.

| check | result |
|---|---|
| Marking math round-trip, 7 rates incl. 23.976/29.97/59.94, ~1h each | exact, 0 drift (Node) |
| Seek-to-midpoint lands on the exact frame @ 30fps | 30 / 30 |
| Seek-to-midpoint lands on the exact frame @ 59.94fps | 60 / 60 |
| Continuous rVFC tracks `curFrame` after paused seeks @ 59.94fps | 8 / 8, matches pixels |
| `npm run build` | clean |

One notable non-bug caught during verification: `ffmpeg -ss` output-seek lands
on frame *f+1* for a midpoint seek, because its semantics are "first frame at or
after t". Browsers use "the frame whose interval contains t" → frame *f*. The
real-Chromium test confirmed the browser semantics; the midpoint seek is correct.

## Roadmap to production

**P1 — land the spike as the shipped behaviour.**
- Keep the native approach. Fold the spike's Inspector changes in behind the
  existing `frameExact` gate (already graceful for audio / unknown fps).
- ✅ Add keyboard shortcuts on the focused player: `,`/`.` step, `i`/`o` mark
  (guard against firing inside inputs). Editors expect these. — shipped this
  branch: a guarded `svelte:window` `keydown` in `Inspector.svelte` (ignores
  input/textarea/select/contenteditable targets and any modifier chord, so ⌘K etc.
  and tag-field typing are untouched; frame steppers stay inert without a fps).
- Guarantee `fps` reaches the inspector on the **detail** path too, not only the
  grid `_raw` (confirm `/api/media/{id}` carries `fps`; fall back to grid value).
- Empty/So-what state: when `fps` is missing or 0, keep today's second-based
  marking silently (no frame UI) — never show wrong frame numbers.

**P2 — make the marks first-class.**
- Persist IN/OUT per clip (currently reset on selection change) so a set range
  survives navigation. Small table or a column on `media`.
- Round-trip the exact frame through export: verify `_edl_timecode(in_s, fps)`
  reproduces the intended frame (it should, since `in_s = frame/fps`); add a
  test in the export suite pinning a few (frame, fps) → timecode pairs,
  including 23.976 and 29.97 drop/non-drop.
- ⬜ **(still open)** Surface `start_tc` (camera-body start timecode, already
  ingested) so the displayed TC matches the camera, not just 00:00:00:00-relative.
  `start_tc` is already threaded to the inspector (metadata grid `tc`) but the
  frame readout (`_tcf`) does not yet offset by it.

**P3 — where WebCodecs finally earns its place (separate effort, measure first).**
- Fast scrub: decode-forward through a GOP instead of per-frame `<video>` seek,
  for the scene-cut filmstrip review (`InspectorFull.svelte`, currently mock).
  Note the scene boundaries themselves are sample-approximated, not true cuts —
  fix that data first; frame-exact decode is downstream of it.
- Seamless trim-loop preview: loop the marked IN/OUT sub-clip without a seam
  hitch (the AVAL seekless-loop trick). Prototype + measurement lives at
  `~/Projects/seekless-loop`; on 1080p30 the effect was small, so gate this on a
  real need (4K/60, weak hardware, many simultaneous previews).

## Caveats

- **Safari**: `requestVideoFrameCallback` is Safari 15.4+. Older Safari falls
  back to second-based marking (the `frameExact` gate handles this — verify the
  fallback path on a real old Safari before claiming support).
- **Test limitation**: the one real clip in the dev DB (`黑沙灘.mov`, 59.94fps,
  HEVC) has no source file present and no proxy built, so end-to-end against
  arkiv's own stream endpoint was not run. Verification used a byte-identical
  H.264 asset through the same `<video>` seek path; re-run against a live proxy
  before shipping P1.
- Non-integer fps (23.976/29.97/59.94) only stays exact if the **unrounded**
  `_raw.fps` is used everywhere — `fpsExact`, not the display-rounded `fps`.
