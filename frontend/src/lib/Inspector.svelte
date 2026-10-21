<!-- Right inspector panel: header · preview · metadata · waveform · transcript · rate. -->
<script>
  import Eyebrow from './Eyebrow.svelte'
  import Mono from './Mono.svelte'
  import Thumb from './Thumb.svelte'
  import Waveform from './Waveform.svelte'
  import { diagnosePlaybackFailure, playbackFailureMessage, proxyButtonLabel } from './streamDiag.js'
  import { onDestroy } from 'svelte'
  import { createInOutWriter } from './inoutWriter.js'
  import { pendingSeek } from './matchTime.js'
  import { parseCube, webgl2Available, createLutRenderer } from './lutGL.js'
  import { selectionToFrom, validateCorrection } from './transcriptCorrect.js'
  import { identityRange, suggestNames, formatIdentityRange, nameKeyStep } from './identities.js'
  import { canDragOut, prepare, ready, startDragOut, sourceKey } from './dragOut.js'
  export let media
  export let theme = 'dark'
  // 360 footage (.insv / .360): the preview thumbnail is an equirectangular frame,
  // so show a drag-to-look sphere viewer instead of the flat still. The viewer
  // (three.js, ~600KB) is lazy-loaded only when a 360 clip is first opened, so the
  // main bundle isn't bloated for the common non-360 case.
  $: is360 = /\.(insv|360)$/i.test((media && media.name) || '')
  let Pano360 = null
  $: if (is360 && !Pano360) import('./Pano360.svelte').then((m) => (Pano360 = m.default))
  // Live overrides — all default to mock behaviour so mock screens are unchanged.
  export let thumbUrl = null // real <img> when set, else abstract Thumb
  // Real stream URL (api.streamUrl) → the preview becomes a playable <video>/<audio>
  // element instead of a static poster. This is the actual playback the
  // mock-derived inspector was missing (the old .controls overlay was a fake
  // scrubber on a still image). null → poster/placeholder behaviour unchanged.
  export let videoSrc = null
  // Precise frames-per-second (unrounded, e.g. 23.976). When set on a video, the
  // IN/OUT marks snap to exact frame boundaries and frame stepping is enabled.
  // null → the old second-based behaviour (audio, or fps unknown) is unchanged.
  export let fps = null
  $: useImage = !!videoSrc && !!media && media.kind === 'image'
  $: useVideo = !!videoSrc && !is360 && !!media && media.kind !== 'audio' && media.kind !== 'image'
  $: useAudio = !!videoSrc && !!media && media.kind === 'audio'
  // Frame features only make sense for a video with a known, sane fps.
  $: frameExact = useVideo && typeof fps === 'number' && fps > 0

  // ---- frame-exact machinery ------------------------------------------------
  // The trick is entirely native: seeking an H.264 <video> IS frame-accurate
  // (just not instant), and requestVideoFrameCallback's `mediaTime` reports the
  // EXACT presentation time of the frame now on screen — so we always know which
  // frame we're looking at, independent of any bookkeeping. No WebCodecs, no
  // demuxer, no new dependency. (WebCodecs would only earn its place for fast
  // multi-frame scrubbing or a seamless trim-loop — see the roadmap.)
  let curFrame = 0 // the frame index actually displayed, from rVFC mediaTime
  let rvfcId = 0
  const secToFrame = (s) => Math.round(s * fps)
  // Seek to a frame's MIDPOINT: nudging past the frame's start time avoids the
  // browser landing on the previous frame due to float/PTS rounding at the edge.
  const frameToSeekSec = (f) => (f + 0.5) / fps

  function trackFrame() {
    if (!playerEl || !playerEl.requestVideoFrameCallback) return
    rvfcId = playerEl.requestVideoFrameCallback((_now, meta) => {
      if (frameExact) curFrame = Math.round(meta.mediaTime * fps)
      trackFrame() // re-arm; rVFC is one-shot
    })
  }
  function stepFrame(delta) {
    if (!playerEl || !frameExact) return
    if (playerEl.pause) playerEl.pause()
    const total = mediaDuration ? Math.round(mediaDuration * fps) : Infinity
    const target = Math.max(0, Math.min(total - 1, curFrame + delta))
    playerEl.currentTime = frameToSeekSec(target)
    curFrame = target // optimistic; rVFC will confirm the true displayed frame
  }
  // Timecode with frames: HH:MM:SS:FF (non-drop). Falls back to MM:SS if no fps.
  function _tcf(sec) {
    if (sec == null) return '—'
    if (!frameExact) return _tc(sec)
    const f = secToFrame(sec)
    const ff = f % Math.round(fps)
    const s = Math.floor(f / fps)
    const hh = Math.floor(s / 3600)
    const mm = Math.floor((s % 3600) / 60)
    const ss = s % 60
    const p2 = (n) => String(n).padStart(2, '0')
    return `${p2(hh)}:${p2(mm)}:${p2(ss)}:${p2(ff)}`
  }
  // Real audio waveform peaks (0..1) from the backend; null → flat fallback.
  export let peaks = null
  // The player element (video or audio) — bound so clicking a transcript line or
  // the waveform seeks playback, and so the waveform playhead tracks currentTime.
  let playerEl = null
  let playProgress = 0 // 0..1 playhead position, driven by the player's timeupdate
  function seekTo(t) {
    if (playerEl && typeof t === 'number' && !Number.isNaN(t)) {
      playerEl.currentTime = t
      playerEl.play && playerEl.play().catch(() => {})
    }
  }
  let mediaDuration = 0 // seconds, from the player once metadata loads
  function onTimeUpdate() {
    if (playerEl && playerEl.duration) {
      mediaDuration = playerEl.duration
      playProgress = playerEl.currentTime / playerEl.duration
    }
  }
  function seekFraction(frac) {
    if (playerEl && playerEl.duration) seekTo(frac * playerEl.duration)
  }
  // IN/OUT trim points (seconds). The backend export accepts ?in_s&out_s, so a
  // set range exports just that sub-clip (EDL/SRT/FCPXML). Set at the playhead.
  let inSec = null
  let outSec = null
  // 12.6 drag-out (desktop only). The path has to exist BEFORE the drag starts
  // (macOS only begins a native drag while the button is still down), so it is
  // prepared ahead: the original as soon as the clip opens, an IN/OUT sub-clip
  // ~0.6 s after the marks stop moving (stream copy — fast, but not free).
  // Every answer is checked against the CURRENT clip + window before it is shown:
  // 2026-10-21 a late answer for the original landed after the persisted IN/OUT
  // hydrated and showed 「拖出 IN/OUT 片段」 with nothing cut behind it.
  const dragOutOn = canDragOut()
  let dragState = 'idle' // idle | preparing | ready | error
  let dragMsg = ''
  let dragImgEl
  let dragTimer = null
  $: dragTrim = (inSec != null || outSec != null) ? { in_s: inSec ?? 0, out_s: outSec ?? undefined } : null
  $: if (dragOutOn && live && media) refreshDrag(media.id, dragTrim)
  function refreshDrag(id, trim) {
    clearTimeout(dragTimer)
    dragMsg = ''
    if (ready(id, trim)) { dragState = 'ready'; return }
    dragState = 'preparing'
    dragTimer = setTimeout(() => prepareDrag(id, trim), trim ? 600 : 0)
  }
  function prepareDrag(id = media.id, trim = dragTrim) {
    const key = sourceKey(id, trim)
    const current = () => media && sourceKey(media.id, dragTrim) === key
    dragState = 'preparing'
    prepare(id, trim)
      .then((src) => {
        if (!current()) return
        dragState = 'ready'
        dragMsg = src.keyframe_cut ? '從最近的關鍵格開始切，開頭可能多幾格' : ''
      })
      .catch((e) => { if (current()) { dragState = 'error'; dragMsg = e?.message || String(e) } })
  }
  onDestroy(() => clearTimeout(dragTimer))
  function onDragHandle(e) {
    e.preventDefault()
    if (!startDragOut(media.id, dragTrim, dragImgEl)) prepareDrag()
  }
  // Hydrate the marks from the persisted backend values when a clip opens (and
  // once more if the detail lands after the switch), but never clobber an edit the
  // user already made on this clip (tracked by _ioTouched). Keyed on media.id so a
  // clip-switch resets, fixing the old bug where marks leaked across clips / were lost.
  // The clip id and values are bound when the write is scheduled (not when the
  // timer fires) — see inoutWriter.js for the cross-clip clobber this prevents.
  const _ioWriter = createInOutWriter(350)
  onDestroy(() => _ioWriter.flush())
  let _ioLoadedId = null
  let _ioTouched = false
  $: _hydrateInOut(media && media.id, inPoint, outPoint)
  function _hydrateInOut(id, ip, op) {
    if (id == null) return
    if (id !== _ioLoadedId) {
      // Land any still-debounced mark on the clip it was made on BEFORE the
      // marks are reset for the new clip (audit N1).
      _ioWriter.flush()
      _ioLoadedId = id
      _ioTouched = false
      inSec = ip ?? null
      outSec = op ?? null
    } else if (!_ioTouched && (ip != null || op != null) && inSec == null && outSec == null) {
      inSec = ip ?? null // detail arrived after the clip switch — hydrate once
      outSec = op ?? null
    }
  }
  // Debounced persist — waveform marker drags fire onTrim rapidly, so coalesce the
  // writes; marking the clip as touched locks out further auto-hydration.
  function _persistInOut() {
    _ioTouched = true
    if (!onInOut) return
    _ioWriter.schedule(onInOut, media && media.id, inSec, outSec)
  }
  // When frameExact, mark at the exact frame boundary (curFrame from rVFC),
  // expressed back in seconds so the ?in_s/out_s export contract is unchanged —
  // the value just now lands precisely on a frame edge instead of wherever the
  // decoder happened to be.
  const markSec = () => (frameExact ? curFrame / fps : (playerEl ? playerEl.currentTime : null))
  function setIn() {
    if (!playerEl) return
    const t = markSec()
    if (t == null) return
    inSec = t
    if (outSec != null && outSec <= inSec) outSec = null
    _persistInOut()
  }
  function setOut() {
    if (!playerEl) return
    const t = markSec()
    if (t == null) return
    outSec = t
    if (inSec != null && inSec >= outSec) inSec = null
    _persistInOut()
  }
  // Space = play/pause (editor muscle memory). With a LUT on, the visible picture is
  // the LUT canvas, which has its own toggle; otherwise the <video>/<audio> itself.
  function togglePlay() {
    if (lutShown) return lutTogglePlay()
    if (!playerEl) return
    if (playerEl.paused) playerEl.play().catch(() => {})
    else playerEl.pause()
  }
  function clearInOut() { inSec = null; outSec = null; _persistInOut() }
  // Keyboard shortcuts editors reach for: , / . step one frame, i / o mark IN/OUT.
  // Guarded so they never fire while typing (tag/search fields) or when a modifier
  // is held (don't hijack ⌘K etc.), and only when this clip actually has a player —
  // so the design-mock inspector (no videoSrc) and audio-without-fps stay inert for
  // the frame steppers while i/o still mark on any player.
  function onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return
    const t = e.target
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
    if (!(useVideo || useAudio)) return
    switch (e.key) {
      // Hevin 2026-10-21: space = play/pause, always. A focused <video>/<audio> with native
      // controls already toggles on space — handling it here too would toggle twice (= nothing).
      case ' ': if (t && (t.tagName === 'VIDEO' || t.tagName === 'AUDIO')) break; togglePlay(); e.preventDefault(); break
      case 'i': case 'I': setIn(); e.preventDefault(); break
      case 'o': case 'O': setOut(); e.preventDefault(); break
      case ',': if (frameExact) { stepFrame(-1); e.preventDefault() } break
      case '.': if (frameExact) { stepFrame(1); e.preventDefault() } break
    }
  }
  // Waveform marker drag → set IN/OUT directly (no need to scrub the playhead there)
  function onTrim(e) {
    if (!mediaDuration) return
    const t = e.detail.frac * mediaDuration
    if (e.detail.which === 'in') {
      inSec = t
      if (outSec != null && outSec <= inSec) outSec = null
    } else {
      outSec = t
      if (inSec != null && inSec >= outSec) inSec = null
    }
    _persistInOut()
  }
  function onLoadedMeta() {
    if (playerEl && playerEl.duration) mediaDuration = playerEl.duration
    if (frameExact) { curFrame = 0; trackFrame() }
    applyStartAt()
  }
  // Deep-link from a search hit: {id, t} — open the clip at the matched second.
  // Seeks only (no autoplay), once per link, and only for that clip.
  export let startAt = null
  let startApplied = null
  function applyStartAt() {
    const s = pendingSeek(startAt, media && media.id, startApplied)
    if (!s || !playerEl || !playerEl.duration) return
    playerEl.currentTime = Math.min(s.t, playerEl.duration)
    startApplied = s.key
  }
  // the clip may already be loaded when the link arrives (same clip re-opened)
  $: if (startAt && media) applyStartAt()
  const _tc = (s) => {
    if (s == null) return '—'
    s = Math.max(0, Math.round(s))
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  }
  $: inFrac = inSec != null && mediaDuration ? inSec / mediaDuration : null
  $: outFrac = outSec != null && mediaDuration ? outSec / mediaDuration : null
  // export args: include the trim window only when a real range is set
  $: trimArgs = (inSec != null || outSec != null) ? { in_s: inSec ?? 0, out_s: outSec ?? undefined } : null
  export let pathLabel = null // file path string; null → mock /vol/... path
  export let transcriptLines = null // [[tc,text,hl],...]; null → mock lines
  // G2: per-language transcript tabs. transcriptLangs = [{lang, active}]; viewLang
  // = which one's lines are shown; callbacks switch the view / set the active.
  export let transcriptLangs = null
  export let viewLang = null
  export let onViewLang = null // (lang) => void — view a language (no activate)
  export let onActivateLang = null // (lang) => void — make it the active transcript
  // Spoken-texture reading for the language being viewed: {count, density}.
  // Server-computed (one definition of the marker set, in transcript_compare) —
  // `density` is null when the transcript is too short for a percentage to be
  // anything but noise, and the count is shown alone. A count is honest at any
  // length; a percentage needs a denominator.
  export let particleStat = null
  export let frameDescriptions = null // string[]; when set, render a Vision block
  // Richer per-frame vision metadata for the scene timeline. When set, supersedes
  // frameDescriptions. [{tc?, description, content_type, atmosphere, energy,
  // edit_position, edit_reason, focus_score}]. null → mock screens unchanged.
  export let frameScenes = null
  export let onRate = null // (uiRating) => void; when set, rate buttons are live
  // Persisted IN/OUT trim points (seconds) for THIS clip, from the backend detail.
  // The inspector hydrates its marks from these on clip-open so a range survives a
  // clip-switch/reload; null → no saved marks. onInOut(inSec, outSec) persists a
  // change (debounced) — null keeps the marks purely local (mock screens unchanged).
  export let inPoint = null
  export let outPoint = null
  export let onInOut = null // (inSec, outSec) => void
  // (fmt) => void; triggers an authenticated download for this clip. When set,
  // the export buttons become live; null → mock screens keep the inert buttons.
  export let onExport = null
  // Extra clip actions (live only): onChapters(format) downloads YouTube chapters,
  // onRemotion(trim) downloads Remotion caption props (trim = IN/OUT window or null), onReveal() reveals the source
  // file in Finder/Explorer. null → button hidden (mock screens unchanged).
  export let onChapters = null
  export let onRemotion = null
  // Parent's in-flight export key ('clip:edl', 'chapters', …) or null. While set,
  // export buttons disable and the running one shows … (B11 — no silent wait).
  export let exportBusy = null
  export let onReveal = null
  // Live delete. onDelete = () => void; when set, a "刪除素材" button appears. The
  // parent owns the confirm dialog + the API call (so it can also drive bulk delete).
  export let onDelete = null
  // Live tag editing. tags = [{id,name,source}] → renders the editable Tags block;
  // null → no block (mock screens unchanged). onAddTag/onRemoveTag wire the writes.
  export let tags = null
  export let onAddTag = null // (name) => void
  export let onRemoveTag = null // (name) => void
  // A6 person annotations (manual — never face recognition). identities =
  // [{id,name,start_s,end_s}] → renders the 人物 block; null → no block (mock
  // screens unchanged). identityNames = [{name,count}] for autocomplete.
  // onAddIdentity(name, {start_s,end_s}|{}) → Promise<boolean>; onRemoveIdentity(id).
  export let identities = null
  export let identityNames = []
  export let onAddIdentity = null
  export let onRemoveIdentity = null
  let personOpen = false
  let personInput = ''
  let personHi = -1
  let personBusy = false
  let personEl = null
  // The range is decided when the box opens AND re-read on submit, so moving the
  // marks while typing is honoured; the label shows what will be written.
  $: personRange = identityRange(inSec, outSec, mediaDuration)
  $: personSuggest = personOpen
    ? suggestNames(identityNames, personInput, (identities || []).map((i) => i.name))
    : []
  $: if (personHi >= personSuggest.length) personHi = -1
  function openPerson() {
    personOpen = true
    personInput = ''
    personHi = -1
    setTimeout(() => personEl && personEl.focus(), 0)
  }
  function closePerson() { personOpen = false; personInput = ''; personHi = -1 }
  async function submitPerson(name) {
    const n = (name || '').trim()
    if (!n || !onAddIdentity || personBusy) return
    personBusy = true
    try {
      const ok = await onAddIdentity(n, identityRange(inSec, outSec, mediaDuration))
      if (ok !== false) closePerson()
    } finally {
      personBusy = false
    }
  }
  function onPersonKey(e) {
    const step = nameKeyStep(e.key, personInput, personSuggest, personHi)
    if (step.action || e.key === 'Enter') e.preventDefault()
    personHi = step.highlight
    if (step.action === 'cancel') closePerson()
    else if (step.action === 'submit') submitPerson(step.name)
  }
  // LLM-canonicalized tag list (string[]) — semantic-deduped (生肉/生魚→肉類).
  // When present, a toggle switches the Tags block raw ⇄ canonical; default
  // canonical (the cleaner view) unless the user flipped it (persisted).
  export let canonicalTags = null
  let showCanonical =
    typeof localStorage === 'undefined' || localStorage.getItem('arkiv.tagview') !== 'raw'
  function toggleTagView() {
    showCanonical = !showCanonical
    try { localStorage.setItem('arkiv.tagview', showCanonical ? 'canonical' : 'raw') } catch (e) { /* private mode */ }
  }
  $: hasCanon = !!(canonicalTags && canonicalTags.length)
  $: useCanon = hasCanon && showCanonical
  // G8: tag-source breakdown for the section header (auto = vision, manual = hand-added)
  $: autoCount = tags ? tags.filter((t) => t.source === 'auto').length : 0
  $: manualCount = tags ? tags.filter((t) => t.source !== 'auto').length : 0
  // Live re-processing. onReprocess = async (action, opts) => {ok, message};
  // action ∈ 'retranscribe' | 'retry-vision' | 'reingest'. null → no block (mock).
  export let onReprocess = null
  // ---- log → Rec.709 preview (color profiles) -------------------------------
  // colorProfile: the clip's profile id ('sony-slog3-…', 'user:<lut>', 'none', or
  // null). colorProfiles: [{id,label,vendor,source}] for the picker. The proxy is
  // log; when a profile is set the player is drawn through its LUT in WebGL and
  // the toggle shows the original. getCube(id) → .cube text.
  export let colorProfile = null
  export let colorProfiles = []
  export let onSetColorProfile = null // (id|null) => Promise
  export let onImportLut = null // (File) => Promise<id>
  export let getCube = null // (id) => Promise<string>
  const glOK = typeof document !== 'undefined' && webgl2Available()
  let lutOn = true
  let lutCanvas = null
  let lutRenderer = null
  let lutErr = ''
  let lutFor = null // profile id currently uploaded to the renderer
  const cubeCache = new Map()
  $: lutApplies = useVideo && !videoFailed && glOK && !!getCube && !!colorProfile && colorProfile !== 'none'
  $: lutShown = lutApplies && lutOn && !lutErr
  $: if (lutShown && lutCanvas && playerEl) _ensureLut(colorProfile)
  $: if (!lutShown && lutRenderer) _dropLut()
  async function _ensureLut(id) {
    try {
      if (!lutRenderer) { lutRenderer = createLutRenderer(lutCanvas, playerEl); lutFor = null }
      if (lutFor === id) return
      lutFor = id
      if (!cubeCache.has(id)) cubeCache.set(id, getCube(id).then(parseCube))
      const cube = await cubeCache.get(id)
      if (lutRenderer && lutFor === id) lutRenderer.setLut(cube)
    } catch (e) {
      cubeCache.delete(id)
      lutErr = 'LUT 載入失敗：' + ((e && e.message) || e)
      _dropLut()
    }
  }
  function _dropLut() {
    if (lutRenderer) lutRenderer.destroy()
    lutRenderer = null
    lutFor = null
  }
  onDestroy(_dropLut)
  // The canvas covers the native control bar, so it takes over play/pause.
  function lutTogglePlay() {
    if (!playerEl) return
    if (playerEl.paused) playerEl.play && playerEl.play().catch(() => {})
    else playerEl.pause && playerEl.pause()
  }
  let cpBusy = false
  let cpMsg = ''
  async function pickProfile(e) {
    const v = e.target.value
    if (v === '__import') { e.target.value = colorProfile || ''; lutFile && lutFile.click(); return }
    if (!onSetColorProfile) return
    cpBusy = true; cpMsg = ''; lutErr = ''
    try { await onSetColorProfile(v || null) } catch (err) { cpMsg = (err && err.message) || String(err) }
    cpBusy = false
  }
  let lutFile = null
  async function importPicked(e) {
    const f = e.target.files && e.target.files[0]
    e.target.value = ''
    if (!f || !onImportLut) return
    cpBusy = true; cpMsg = ''
    try {
      const id = await onImportLut(f)
      if (id && onSetColorProfile) await onSetColorProfile(id)
    } catch (err) { cpMsg = (err && err.message) || String(err) }
    cpBusy = false
  }
  $: vendors = [...new Set((colorProfiles || []).filter((p) => p.source === 'builtin').map((p) => p.vendor))]
  $: userLuts = (colorProfiles || []).filter((p) => p.source === 'user')
  export let languages = null // [{code,label}] for the retranscribe picker
  export let mediaLang = null // current clip language → default selection
  let imgFailed = false
  // Reset per clip: a failure that stuck would make the NEXT clip look broken
  // too, which is worse than the black pane it replaces.
  let videoFailed = false
  // What actually went wrong, asked of the endpoint. `null` = not known yet, and
  // the panel says nothing specific until it is — claiming "此編碼播不了" before
  // we have the status is exactly the guess this replaces.
  let videoDiag = null
  $: if (media) { videoFailed = false; videoDiag = null }

  async function onVideoError() {
    videoFailed = true
    videoDiag = null
    if (!videoSrc) return
    const asked = videoSrc
    const d = await diagnosePlaybackFailure(videoSrc)
    // A slow probe must not relabel a clip the user has already moved past.
    if (asked === videoSrc) videoDiag = d
  }
  let showMore = false // reveal the ancillary (成品輔助) export group
  let tagInput = ''
  function submitTag() {
    const v = tagInput.trim()
    if (!v || !onAddTag) return
    onAddTag(v)
    tagInput = ''
  }
  // reprocess local state, reset when the selected clip changes
  let reBusy = null // action currently running, or null
  let reMsg = ''
  // Live progress for the running action, e.g. '12/40'. Supplied by the parent,
  // which polls the job's status endpoint while its own POST is still in flight.
  export let reProgress = ''
  let reLang = 'zh'
  let lastMediaId = null
  $: if (media && media.id !== lastMediaId) {
    lastMediaId = media.id
    reBusy = null
    reMsg = ''
    reLang = mediaLang || 'zh'
    inSec = null
    outSec = null
    mediaDuration = 0
    curFrame = 0
    // The <video> element is reused across clips (same bind), so a live rVFC
    // callback from the previous clip must be cancelled; onLoadedMeta re-arms it.
    if (playerEl && playerEl.cancelVideoFrameCallback && rvfcId) {
      playerEl.cancelVideoFrameCallback(rvfcId)
      rvfcId = 0
    }
  }
  async function doReprocess(action) {
    if (!onReprocess || reBusy) return
    reBusy = action
    reMsg = ''
    try {
      const opts = action === 'retranscribe' ? { language: reLang || 'zh' } : {}
      const r = await onReprocess(action, opts)
      reMsg = (r && r.message) || '完成'
    } catch (e) {
      reMsg = `失敗: ${(e && e.message) || e}`
    } finally {
      reBusy = null
    }
  }
  const RE_LANGS = [{ code: 'zh', label: '中文' }, { code: 'en', label: 'English' }]
  $: langOpts = languages && languages.length ? languages : RE_LANGS
  const EXPORT_FMTS = ['edl', 'fcpxml', 'srt', 'vtt', 'txt']
  // The server has served .txt since Phase 12; only the button was missing, so the
  // one format an editor actually pastes into a script doc was unreachable from
  // the UI. The trim note is not padding: without ✂ the file is the LLM-polished
  // transcript, with ✂ it is the raw Whisper segment text inside the window —
  // those are not the same document.
  const EXPORT_TITLES = {
    txt: '逐字稿純文字（完整標點）。設了 ✂ 範圍時輸出的是該範圍內的 segment 原文，未經潤稿',
  }
  // B13: CMX3600 only knows 23.976/24/25/29.97/30. media.fps arrives rounded
  // (MainLive), so 24/25/30 cover the NTSC rates too. Off-spec clips still export
  // (the server adds a WARNING comment to the EDL) — this just says so up front.
  $: edlOffSpec = media && typeof media.fps === 'number' && media.fps > 0 && ![24, 25, 30].includes(media.fps)
  $: edlTitle = edlOffSpec ? `EDL 規格（CMX3600）不支援 ${media.fps}p，Resolve conform 時 TC 可能對不上，建議改用 FCPXML` : ''

  // 8.2i: same FCPXML, but its markers come from the transcript segments (who
  // said what, when) instead of the scene frames. Resolve: marker name = the
  // line (≤60 chars), marker notes = the full text. Honours ✂ like the others.
  const TRANSCRIPT_MARKERS_TITLE =
    'FCPXML，marker 來源改成逐字稿：每段話一個 marker（名稱＝前 60 字，備註＝全文）。設了 ✂ 只輸出範圍內的段落'

  const MOCK_TRANSCRIPT = [
    ['00:05', '我們從上海一路騎到拉薩，第十七天。', false],
    ['00:12', '中間最難的是格爾木到沱沱河那段。', true],
    ['00:24', '氧氣比想像中還少，海拔 4800。', false],
    ['00:36', '車架被打到變形，但人沒事。', false],
  ]
  // `live` (set by the real product) makes empty states HONEST — an empty
  // transcript shows 「無語音」, an absent path shows the filename — instead of the
  // design-mock placeholders. The /_design/* mock screens leave live=false so they
  // stay populated for reference.
  export let live = false
  $: lines = live ? (transcriptLines ?? []) : (transcriptLines ?? MOCK_TRANSCRIPT)
  // 語氣詞 readout, appended to the segment count rather than given its own row —
  // it is a footnote on the transcript, not a headline about the clip.
  $: particleLabel = particleStat
    ? ` · 語氣 ${particleStat.count}` +
      (particleStat.density != null ? ` · ${particleStat.density.toFixed(1)}%` : '')
    : ''
  $: particleTitle = !particleStat
    ? undefined
    : particleStat.density != null
      ? '語氣詞密度：每 100 字裡的語氣詞（啦齁嘛蛤欸咧吼唷呴）。' +
        '高＝保留了口說語氣，低＝被整理成書面語。' +
        '這是相對指標 —— 比同一段聲音的兩份逐字稿有意義；跨片比較比到的是講話的人，不是引擎。'
      : '這份逐字稿太短，百分比會被單一個字左右，所以只給個數。'
  $: pathStr = live
    ? (pathLabel ?? media.name)
    : (pathLabel ?? `/vol/nas01/bicycle-diaries/raw/${media.name}`)
  const rateBtns = [['good', 'Good'], ['rev', 'Review'], ['ng', 'N·G'], ['none', '—']]

  // ── 選字 → 校正 ──────────────────────────────────────────────────────────
  // Select text inside ONE transcript segment (drag, or double-click a word) →
  // a small 「校正…」 chip appears → type the right spelling → Enter sends it to
  // onCorrect(from, to, apply). onCorrect is the parent's learnCorrection call; it
  // resolves on success (parent toasts + reloads the transcript) and throws on
  // failure (the error stays in the form so the user can fix and resend).
  // null → transcript stays read-only (mock screens, archived languages).
  export let onCorrect = null
  // corr: null | {stage:'chip'|'refuse'|'form', from, seg, reason, x, y, to, apply, busy, err}
  let corr = null
  let linesEl = null
  let corrEl = null
  const POP_W = 300
  // A selection boundary can be (text node, offset) or — WebKit does this when a
  // drag crosses block boundaries — (element, child index). Resolve the latter to
  // the child it points at before looking for the segment.
  function segOf(node, offset) {
    if (node && node.nodeType === 1 && node.childNodes.length) {
      node = node.childNodes[Math.min(offset || 0, node.childNodes.length - 1)]
    }
    const el = node && (node.nodeType === 1 ? node : node.parentElement)
    const host = el && el.closest ? el.closest('[data-seg]') : null
    return host && linesEl && linesEl.contains(host) ? Number(host.dataset.seg) : null
  }
  function hasSelection() {
    const s = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null
    return !!(s && s.rangeCount && !s.isCollapsed && s.toString().trim())
  }
  function readSelection() {
    if (!onCorrect || (corr && corr.stage === 'form')) return
    const s = window.getSelection()
    if (!s || !s.rangeCount || s.isCollapsed) { corr = null; return }
    const range = s.getRangeAt(0)
    // a selection that never touches the transcript is someone else's business
    if (!linesEl || !linesEl.contains(range.commonAncestorContainer)) {
      if (!(linesEl && range.intersectsNode(linesEl))) { corr = null; return }
    }
    const startSeg = segOf(range.startContainer, range.startOffset)
    const endSeg = segOf(range.endContainer, range.endOffset)
    const segText = startSeg != null && lines[startSeg] ? lines[startSeg][1] : null
    const r = selectionToFrom({ startSeg, endSeg, text: s.toString(), segText })
    if (!r.ok && r.reason === 'empty') { corr = null; return }
    const rect = range.getBoundingClientRect()
    const x = Math.max(8, Math.min(rect.left, window.innerWidth - POP_W - 8))
    const pos = { x, top: rect.top, bottom: rect.bottom }
    corr = r.ok
      ? { stage: 'chip', from: r.from, seg: r.seg, ...pos }
      : { stage: 'refuse', reason: r.reason, ...pos }
  }
  // Below the selection when it fits, else above it (the transcript sits at the
  // bottom of a scrolling panel, so "above" is the common case for late lines).
  function popStyle(c) {
    const h = c.stage === 'form' ? 190 : 34
    if (c.bottom + 6 + h <= window.innerHeight - 8) return `left:${c.x}px;top:${c.bottom + 6}px;`
    return `left:${c.x}px;bottom:${Math.max(8, window.innerHeight - c.top + 6)}px;`
  }
  // WebKit finalises the selection after mouseup's handlers; read it a tick later.
  function onLinesMouseUp() { if (onCorrect) setTimeout(readSelection, 0) }
  function openCorrForm() {
    if (!corr || corr.stage !== 'chip') return
    corr = { ...corr, stage: 'form', to: corr.from, apply: true, busy: false, err: null }
  }
  function closeCorr() { corr = null }
  async function submitCorr() {
    if (!corr || corr.stage !== 'form' || corr.busy) return
    const v = validateCorrection(corr.from, corr.to)
    if (!v.ok) { corr = { ...corr, err: v.reason }; return }
    const mine = corr
    corr = { ...corr, busy: true, err: null }
    try {
      await onCorrect(v.from, v.to, mine.apply)
      if (corr && corr.from === mine.from) corr = null
      const s = window.getSelection()
      if (s) s.removeAllRanges()
    } catch (e) {
      if (corr && corr.from === mine.from) corr = { ...corr, busy: false, err: (e && e.message) || String(e) }
    }
  }
  function onCorrKey(e) {
    // IME: the Enter that commits a 注音/拼音 candidate must not submit
    // (WebKit reports it as keyCode 229 and/or isComposing).
    if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submitCorr() }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeCorr() }
  }
  function onDocMouseDown(e) {
    if (!corr) return
    if (corrEl && corrEl.contains(e.target)) return
    if (corr.stage === 'form') { if (!corr.busy) closeCorr(); return }
    // chip/refuse: a new mousedown starts a new selection; mouseup re-reads it
    corr = null
  }
  function onWinKey(e) {
    if (corr && e.key === 'Escape' && corr.stage !== 'form') { closeCorr(); return }
    onKey(e)
  }
  function focusInput(node) { node.focus(); node.select() }
  // switching clips (or the parent turning correction off) drops a pending correction
  let _corrMid = null
  $: if ((media && media.id) !== _corrMid) { _corrMid = media && media.id; corr = null }
  $: if (!onCorrect) corr = null
</script>

<svelte:window on:keydown={onWinKey} on:mousedown={onDocMouseDown} />

<aside class="inspector" on:scroll={() => { if (corr && corr.stage !== 'form') corr = null }}>
  <div class="header">
    <Eyebrow style="margin-bottom:8px;">
      Inspector · {media.kind === 'image' ? 'IMAGE' : media.kind === 'audio' ? 'AUDIO' : `${media.fps}p · ${media.res}`}
    </Eyebrow>
    <div class="fname">{media.name}</div>
    <Mono dim style="font-size:10.5px;margin-top:4px;letter-spacing:0.04em;">
      {pathStr}
    </Mono>
    {#if dragOutOn && live}
      <div class="dragout">
        {#if dragState === 'ready'}
          <!-- svelte-ignore a11y-no-static-element-interactions -->
          <div class="draghandle" draggable="true" on:dragstart={onDragHandle} title="拖到 DaVinci Resolve、Premiere 或 Finder">
            <img bind:this={dragImgEl} src={thumbUrl || ''} alt="" draggable="false" style="display:none;" />
            ⇱ {dragTrim ? '拖出 IN/OUT 片段' : '拖出原始檔'}
          </div>
        {:else if dragState === 'preparing'}
          <Mono dim style="font-size:10.5px;">{dragTrim ? '切 IN/OUT 片段中…' : '準備中…'}</Mono>
        {:else if dragState === 'error'}
          <button class="ak-btn" on:click={() => prepareDrag()}>重試拖出</button>
        {/if}
        {#if dragMsg}<Mono dim style="font-size:10px;display:block;margin-top:3px;">{dragMsg}</Mono>{/if}
      </div>
    {/if}
  </div>

  <div class="preview" class:hasplayer={useVideo || useAudio || useImage}>
    {#if is360 && thumbUrl && !imgFailed}
      {#if Pano360}<svelte:component this={Pano360} src={thumbUrl} />{:else}<div class="panoload"><Mono dim style="font-size:11px;">360 · loading…</Mono></div>{/if}
    {:else if useImage}
      <img class="previmg" src={videoSrc} alt={media.name} on:error={() => (imgFailed = true)} />
    {:else if useVideo && videoFailed}
      <!-- The stream endpoint answers 409 {need_proxy} for a codec no browser
           decodes, but a <video> has no way to show a JSON body: it just fails
           to load and leaves a black pane. Without this the 409 is invisible
           and the clip reads as broken rather than as needing one click (#420).
           🔴 `on:error` fires for EVERY load failure though — 401, 404, backend
           down — so this panel used to send those users to build a proxy for a
           file they cannot read. The status is asked for now, and the button
           appears only when the endpoint itself says a proxy is the fix. -->
      <div class="playfail">
        <Mono dim style="font-size:11px;">{playbackFailureMessage(videoDiag)}</Mono>
        {#if onReprocess && proxyButtonLabel(videoDiag)}
          <button class="ak-btn" disabled={!!reBusy} on:click={() => doReprocess('proxy')}>
            {reBusy === 'proxy' ? '排入中…' : proxyButtonLabel(videoDiag)}
          </button>
        {/if}
      </div>
    {:else if useVideo}
      <!-- svelte-ignore a11y-media-has-caption -->
      <video bind:this={playerEl} on:timeupdate={onTimeUpdate} on:loadedmetadata={onLoadedMeta} on:error={onVideoError} class="previmg" controls playsinline preload="metadata" poster={thumbUrl || undefined} src={videoSrc}></video>
      {#if lutShown}
        <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
        <canvas bind:this={lutCanvas} class="lutcanvas" on:click={lutTogglePlay} title="點擊播放／暫停（空白鍵）"></canvas>
      {/if}
      {#if lutApplies}
        <div class="lutpill">
          <button class="ak-btn lutbtn" class:on={lutOn && !lutErr} on:click={() => { lutOn = !lutOn; lutErr = '' }}
            title={lutOn ? '顯示原始 log 畫面' : '套用色彩設定檔'}>{lutOn && !lutErr ? 'LUT ●' : 'LOG ○'}</button>
          {#if lutShown}<button class="ak-btn lutbtn" on:click={lutTogglePlay} title="播放／暫停（空白鍵）">▶︎❚❚</button>{/if}
        </div>
      {/if}
    {:else if useAudio}
      {#if thumbUrl && !imgFailed}
        <img class="previmg" src={thumbUrl} alt={media.name} on:error={() => (imgFailed = true)} />
      {/if}
      <audio bind:this={playerEl} on:timeupdate={onTimeUpdate} on:loadedmetadata={onLoadedMeta} class="prevaudio" controls preload="metadata" src={videoSrc}></audio>
    {:else if thumbUrl && !imgFailed}
      <img class="previmg" src={thumbUrl} alt={media.name} on:error={() => (imgFailed = true)} />
    {:else}
      <Thumb seed={media.id} kind={media.kind} {theme} />
    {/if}
    {#if !useVideo && !useAudio && !useImage && !live}
      <!-- design-mock only: fake scrubber overlay. In live (no player available)
           the poster shows alone — no faked playback chrome. -->
      <div class="scrim"></div>
      <div class="controls">
        <Mono style="font-size:11px;color:#f3f2ee;">{media.dur}</Mono>
        <div class="track">
          <div class="trackfill"></div>
          <div class="trackhead"></div>
        </div>
        <Mono style="font-size:11px;color:#f3f2ee;">{media.dur}</Mono>
      </div>
    {/if}
  </div>

  <div class="block">
    <Eyebrow style="margin-bottom:8px;">Metadata</Eyebrow>
    <div class="metagrid">
      <Mono dim>CAMERA</Mono><Mono>{media.cam}</Mono>
      <Mono dim>LENS</Mono><Mono>{media.lens}</Mono>
      <Mono dim>ISO</Mono><Mono>{media.iso} · {media.ap} · {media.fl}</Mono>
      <Mono dim>TC</Mono><Mono>{media.tc}</Mono>
      <Mono dim>SIZE</Mono><Mono>{media.size} · {media.dur}</Mono>
      {#if onSetColorProfile && media.kind !== 'audio'}
        <Mono dim>COLOR</Mono>
        <div class="cprow">
          <select class="cpsel" value={colorProfile || ''} disabled={cpBusy} on:change={pickProfile} title="log 素材的色彩設定檔（縮圖、關鍵畫面、播放器套用）">
            <option value="">未指定</option>
            <option value="none">非 log（不套用）</option>
            {#each vendors as v}
              <optgroup label={v}>
                {#each colorProfiles.filter((p) => p.source === 'builtin' && p.vendor === v) as p}
                  <option value={p.id} title={p.note || ''}>{p.label}</option>
                {/each}
              </optgroup>
            {/each}
            {#if userLuts.length}
              <optgroup label="自訂 LUT">
                {#each userLuts as p}<option value={p.id}>{p.label}</option>{/each}
              </optgroup>
            {/if}
            {#if onImportLut}<option value="__import">匯入 .cube…</option>{/if}
          </select>
          <input bind:this={lutFile} type="file" accept=".cube" style="display:none" on:change={importPicked} />
        </div>
        {#if cpMsg || lutErr}<span></span><Mono style="font-size:10px;color:#d9534f;">{cpMsg || lutErr}</Mono>{/if}
      {/if}
    </div>
  </div>

  {#if tags || hasCanon}
    <div class="block">
      <div class="blockhead">
        <Eyebrow>Tags</Eyebrow>
        <div class="taghead-right">
          {#if tags && (autoCount || manualCount)}
            <Mono dim style="font-size:9.5px;letter-spacing:0.06em;">{autoCount} AUTO · {manualCount} MANUAL</Mono>
          {/if}
          {#if hasCanon}
            <button class="tagtoggle" on:click={toggleTagView} title="原始 vision tags ⇄ LLM 精簡（語意去重）">
              {useCanon ? '精簡' : '原始'} ⇄
            </button>
          {/if}
        </div>
      </div>
      {#if useCanon}
        <div class="tagrow">
          {#each canonicalTags as t}<span class="tagchip canon">{t}</span>{/each}
        </div>
      {:else if tags}
        <div class="tagrow">
          {#each tags as t (t.name)}
            <span class="tagchip" class:auto={t.source === 'auto'}>
              <span class="tagname">{t.name}</span>
              {#if onRemoveTag}
                <button class="tagx" title="移除標籤" on:click={() => onRemoveTag(t.name)}>×</button>
              {/if}
            </span>
          {/each}
          {#if tags.length === 0}
            <Mono dim style="font-size:10.5px;">（無標籤）</Mono>
          {/if}
        </div>
        {#if onAddTag}
          <form class="tagadd" on:submit|preventDefault={submitTag}>
            <input class="ak-input taginput" placeholder="加標籤…" bind:value={tagInput} />
            <button class="ak-btn tagaddbtn" type="submit" disabled={!tagInput.trim()}>＋</button>
          </form>
        {/if}
      {/if}
    </div>
  {/if}

  {#if identities}
    <div class="block" data-testid="people-block">
      <div class="blockhead">
        <Eyebrow>人物</Eyebrow>
        <Mono dim style="font-size:9.5px;letter-spacing:0.06em;">人工標註 · {identities.length}</Mono>
      </div>
      <div class="people">
        {#each identities as p (p.id)}
          <div class="person">
            <span class="pname">{p.name}</span>
            {#if p.start_s != null && (useVideo || useAudio)}
              <button class="ptime seek" title="跳到 {formatIdentityRange(p)} 起點" on:click={() => seekTo(p.start_s)}>{formatIdentityRange(p)}</button>
            {:else}
              <Mono dim style="font-size:10px;">{formatIdentityRange(p)}</Mono>
            {/if}
            {#if onRemoveIdentity}
              <button class="tagx" title="移除人物標註" aria-label="移除 {p.name}" on:click={() => onRemoveIdentity(p.id)}>×</button>
            {/if}
          </div>
        {/each}
        {#if identities.length === 0}
          <Mono dim style="font-size:10.5px;">（尚未標註）</Mono>
        {/if}
      </div>
      {#if onAddIdentity}
        {#if personOpen}
          <div class="personadd">
            <Mono dim style="font-size:9.5px;">範圍：{formatIdentityRange(personRange)}{personRange.start_s == null ? '（未設 IN/OUT）' : '（IN/OUT）'}</Mono>
            <div class="personrow">
              <input class="ak-input taginput" placeholder="名字… Enter 送出 · Esc 取消" bind:this={personEl}
                bind:value={personInput} on:keydown={onPersonKey} disabled={personBusy}
                role="combobox" aria-expanded={personSuggest.length > 0} aria-autocomplete="list" aria-controls="person-suggest" />
              <button class="ak-btn tagaddbtn" on:click={() => submitPerson(personHi >= 0 ? personSuggest[personHi] : personInput)}
                disabled={personBusy || !personInput.trim() && personHi < 0}>＋</button>
              <button class="ak-btn tagaddbtn" on:click={closePerson} title="取消（Esc）">取消</button>
            </div>
            {#if personSuggest.length}
              <ul class="psuggest" id="person-suggest" role="listbox">
                {#each personSuggest as s, i (s)}
                  <li role="option" aria-selected={i === personHi}>
                    <button class="psug" class:hi={i === personHi} on:mousedown|preventDefault={() => submitPerson(s)}>{s}</button>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {:else}
          <button class="ak-btn personbtn" on:click={openPerson}
            title={personRange.start_s == null ? '標註整支片出現的人物' : '標註 IN/OUT 範圍內出現的人物'}>
            標人物{personRange.start_s == null ? '（整支片）' : `（${formatIdentityRange(personRange)}）`}
          </button>
        {/if}
      {/if}
    </div>
  {/if}

  <div class="block">
    <div class="blockhead">
      <Eyebrow>Waveform</Eyebrow>
      <Mono dim style="font-size:9.5px;">IN {_tcf(inSec)} · OUT {_tcf(outSec)}</Mono>
    </div>
    <Waveform {peaks} progress={playProgress} {inFrac} {outFrac} on:seek={(e) => seekFraction(e.detail)} on:trim={onTrim} />
    {#if frameExact}
      <!-- frame-exact stepper: step one frame, mark at the exact frame boundary.
           The readout shows the frame rVFC confirms is actually on screen. -->
      <div class="frametools">
        <button class="ak-btn fstep" on:click={() => stepFrame(-1)} title="上一格（,）">◂ 格</button>
        <button class="ak-btn fstep" on:click={() => stepFrame(1)} title="下一格（.）">格 ▸</button>
        <Mono dim style="font-size:9.5px;margin-left:6px;align-self:center;">
          f{curFrame} · {_tcf(curFrame / fps)} · {fps.toFixed(fps % 1 ? 3 : 0)}fps
        </Mono>
      </div>
    {/if}
    {#if useVideo || useAudio}
      <div class="inout">
        <button class="ak-btn io" on:click={setIn} title={frameExact ? '設 IN（貼齊目前影格 · i）' : '設 IN（目前播放位置 · i）'}>設 IN</button>
        <button class="ak-btn io" on:click={setOut} title={frameExact ? '設 OUT（貼齊目前影格 · o）' : '設 OUT（目前播放位置 · o）'}>設 OUT</button>
        {#if inSec != null || outSec != null}
          <button class="ak-btn io clr" on:click={clearInOut} title="清除 IN/OUT">清除</button>
          <Mono dim style="font-size:9.5px;margin-left:auto;align-self:center;">
            {#if frameExact && inSec != null && outSec != null}{secToFrame(outSec) - secToFrame(inSec)} 格{:else}匯出將套用此區間{/if}
          </Mono>
        {/if}
      </div>
    {/if}
  </div>

  <div class="block transcript">
    <div class="blockhead">
      <Eyebrow>Transcript</Eyebrow>
      <Mono dim style="font-size:9.5px;" title={particleTitle}>{lines.length} 段{particleLabel}</Mono>
    </div>
    {#if transcriptLangs && transcriptLangs.length}
      <div class="langtabs">
        {#each transcriptLangs as lt}
          <button
            class="langtab"
            class:on={lt.lang === viewLang}
            title={lt.active ? '主要（被搜尋／匯出）' : '檢視此語言'}
            on:click={() => onViewLang && onViewLang(lt.lang)}
          >{(lt.lang || '?').toUpperCase()}{lt.active ? ' ●' : ''}</button>
        {/each}
        {#if onActivateLang && viewLang && transcriptLangs.find((l) => l.lang === viewLang && !l.active)}
          <button class="langset" on:click={() => onActivateLang(viewLang)}>設為主要</button>
        {/if}
      </div>
    {/if}
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="lines" class:correctable={!!onCorrect} bind:this={linesEl} on:mouseup={onLinesMouseUp}>
      {#if lines.length === 0}
        <Mono dim style="font-size:11px;">（無語音 · no speech detected）</Mono>
      {:else}
        {#each lines as [tc, text, hl, t], i}
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="line" class:seekable={typeof t === 'number' && (useVideo || useAudio)}
            on:click={() => (typeof t === 'number') && !hasSelection() && seekTo(t)}>
            <Mono dim style="font-size:10.5px;flex:0 0 36px;">{tc}</Mono>
            <span class="ttext" class:hl data-seg={i}>{text}</span>
          </div>
        {/each}
      {/if}
    </div>
    {#if onCorrect && lines.length}
      <div class="corrhint">選取一段字（或雙擊一個詞）可校正錯字</div>
    {/if}
    {#if corr}
      <div class="corrpop" class:form={corr.stage === 'form'} bind:this={corrEl}
        style={popStyle(corr)}>
        {#if corr.stage === 'chip'}
          <button class="corrchip" on:click={openCorrForm} title="把這段字的正確寫法教給本專案">校正「{corr.from}」…</button>
        {:else if corr.stage === 'refuse'}
          <div class="corrrefuse" role="status">{corr.reason}</div>
        {:else}
          <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
          <div class="corrform" role="dialog" aria-label="校正逐字稿" tabindex="-1" on:keydown={onCorrKey}>
            <div class="corrfrom">原文 <b>{corr.from}</b></div>
            <input class="corrinput" type="text" bind:value={corr.to} use:focusInput
              disabled={corr.busy} aria-label="正確寫法" placeholder="正確寫法" />
            <label class="corrapply">
              <input type="checkbox" bind:checked={corr.apply} disabled={corr.busy} />
              套用到本專案所有逐字稿
            </label>
            {#if corr.err}<div class="correrr" role="alert">{corr.err}</div>{/if}
            <div class="corractions">
              <button class="ak-btn" on:click={closeCorr} disabled={corr.busy}>取消 · Esc</button>
              <button class="ak-btn ak-btn--primary" on:click={submitCorr} disabled={corr.busy}>{corr.busy ? '處理中…' : '校正 · Enter'}</button>
            </div>
          </div>
        {/if}
      </div>
    {/if}
  </div>

  {#if frameScenes && frameScenes.length}
    <div class="block">
      <div class="blockhead">
        <Eyebrow>場景時間軸 · qwen3-vl</Eyebrow>
        <Mono dim style="font-size:9.5px;">{frameScenes.length} 場景</Mono>
      </div>
      <div class="scenes">
        {#each frameScenes as sc, i}
          <div class="scene">
            <div class="scenetime">
              <Mono dim style="font-size:10px;">{sc.tc || `f${i + 1}`}</Mono>
              <span class="dot" class:hi={(sc.focus_score ?? 0) >= 4} class:lo={(sc.focus_score ?? 3) <= 2} title="focus {sc.focus_score ?? '—'}"></span>
            </div>
            <div class="scenebody">
              <div class="chips">
                {#if sc.content_type && sc.content_type !== 'Undefined'}<span class="chip">{sc.content_type}</span>{/if}
                {#if sc.edit_position}<span class="chip pos">{sc.edit_position}</span>{/if}
                {#if sc.atmosphere}<span class="chip dim">{sc.atmosphere}</span>{/if}
                {#if sc.energy}<span class="chip dim">能量 {sc.energy}</span>{/if}
              </div>
              {#if sc.description}<div class="scenedesc">{sc.description}</div>{/if}
              {#if sc.edit_reason}<div class="scenereason">▸ {sc.edit_reason}</div>{/if}
            </div>
          </div>
        {/each}
      </div>
    </div>
  {:else if frameDescriptions}
    <div class="block">
      <div class="blockhead">
        <Eyebrow>Vision · qwen3-vl</Eyebrow>
        <Mono dim style="font-size:9.5px;">{frameDescriptions.length} frame(s)</Mono>
      </div>
      <div class="lines">
        {#each frameDescriptions as desc, i}
          <div class="line">
            <Mono dim style="font-size:10.5px;flex:0 0 36px;">f{i + 1}</Mono>
            <span class="ttext">{desc}</span>
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#if onReprocess}
    <div class="block reproc">
      <Eyebrow style="margin-bottom:8px;">Reprocess</Eyebrow>
      <div class="reprow">
        <select class="ak-input relang" bind:value={reLang} disabled={!!reBusy} title="重新轉錄語言">
          {#each langOpts as l}
            <option value={l.code}>{l.label || l.code}</option>
          {/each}
        </select>
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('retranscribe')}>
          {reBusy === 'retranscribe' ? (reProgress ? `轉錄中… ${reProgress}` : '轉錄中…') : '重轉錄'}
        </button>
      </div>
      <div class="reprow">
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('retry-vision')}>
          {reBusy === 'retry-vision' ? (reProgress ? `分析中… ${reProgress}` : '分析中…') : '重試視覺'}
        </button>
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('reingest')}>
          {reBusy === 'reingest' ? '重建中…' : '完整重建'}
        </button>
      </div>
      <div class="reprow">
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('proxy')}>
          {reBusy === 'proxy' ? '排入中…' : '建立 proxy'}
        </button>
        <!-- ProRes Proxy into <source dir>/Proxy/<name>.mov, with source TC and
             every audio track, so Resolve can relink it. Not the button above:
             that one is the 720p H.264 the browser plays. -->
        <button class="ak-btn rebtn" disabled={!!reBusy} title="ProRes Proxy 寫到原始檔旁的 Proxy/，保留 TC 與全部音軌；不覆蓋既有檔" on:click={() => doReprocess('editor-proxy')}>
          {reBusy === 'editor-proxy' ? (reProgress ? `生成中… ${reProgress}` : '生成中…') : '剪輯用 proxy'}
        </button>
      </div>
      {#if reMsg}<Mono dim style="font-size:10px;line-height:1.45;display:block;">{reMsg}</Mono>{/if}
    </div>
  {/if}

  <div class="rate">
    <Eyebrow>Rate</Eyebrow>
    <div class="ratebtns">
      {#each rateBtns as [r, label]}
        <button
          class="ratebtn"
          class:active={media.rating === r}
          class:rev={r === 'rev'}
          on:click={() => onRate && onRate(r)}
        >{label}</button>
      {/each}
    </div>
    <!-- primary exports = footage→edit handoff (Resolve). Per the scope spec,
         finished-product-adjacent formats (Remotion / YouTube chapters) are NOT
         here — they're demoted to the ancillary "更多" group below. -->
    <div class="exports">
      {#if onExport}
        {#each EXPORT_FMTS as fmt}
          <button class="ak-btn exp" title={(fmt === 'edl' && edlTitle) || EXPORT_TITLES[fmt] || ''}
                  disabled={!!exportBusy} aria-busy={exportBusy === `clip:${fmt}`}
                  on:click={() => onExport(fmt, trimArgs)}>{fmt.toUpperCase()}{fmt === 'edl' && edlOffSpec ? ' ⚠' : ''}{trimArgs ? ' ✂' : ''}{exportBusy === `clip:${fmt}` ? ' …' : ''}</button>
        {/each}
        <button class="ak-btn exp" title={TRANSCRIPT_MARKERS_TITLE}
                disabled={!!exportBusy} aria-busy={exportBusy === 'clip:fcpxml.transcript'}
                on:click={() => onExport('fcpxml', { ...(trimArgs || {}), markers: 'transcript' })}
        >字幕 MARKER{trimArgs ? ' ✂' : ''}{exportBusy === 'clip:fcpxml.transcript' ? ' …' : ''}</button>
      {:else}
        {#each EXPORT_FMTS as fmt}
          <button class="ak-btn exp">{fmt.toUpperCase()}</button>
        {/each}
      {/if}
    </div>
    {#if onChapters || onRemotion}
      <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
      <span class="moreexp" on:click={() => (showMore = !showMore)}>{showMore ? '成品輔助 ▴' : '更多 · 成品輔助 ▾'}</span>
      {#if showMore}
        <div class="exports sub">
          {#if onChapters}<button class="ak-btn exp ghost" disabled={!!exportBusy} aria-busy={exportBusy === 'chapters'} on:click={() => onChapters('youtube')} title="YouTube 章節文字（成品輔助，非 arkiv 核心）">YT 章節</button>{/if}
          {#if onRemotion}<button class="ak-btn exp ghost" disabled={!!exportBusy} aria-busy={exportBusy === 'remotion'} on:click={() => onRemotion(trimArgs)} title="Remotion 字幕 props（成品輔助，非 arkiv 核心）">Remotion{trimArgs ? ' ✂' : ''}{exportBusy === 'remotion' ? ' …' : ''}</button>{/if}
        </div>
      {/if}
    {/if}
    {#if onReveal}
      <button class="ak-btn reveal" on:click={onReveal} title="在 Finder / 檔案總管中顯示原始檔">⊙ 在 Finder 顯示</button>
    {/if}
    {#if onDelete}
      <button class="ak-btn del" on:click={onDelete} title="刪除素材（移至回收桶，可還原）">刪除素材</button>
    {/if}
  </div>
</aside>

<style>
  .inspector {
    border-left: 1px solid var(--rule); display: flex; flex-direction: column;
    /* the WHOLE inspector scrolls — preview + metadata + waveform + transcript +
       scenes + tags + reprocess + rate can exceed the viewport, and the bottom
       (rate buttons) was being clipped under overflow:hidden with no way to reach
       it. The transcript no longer scrolls internally; the panel scrolls as one. */
    min-height: 0; overflow-y: auto;
  }
  .header { padding: 18px 18px 16px; border-bottom: 1px solid var(--rule); }
  .fname {
    font-family: var(--ak-mono); font-size: 13px; font-weight: 500;
    letter-spacing: 0.005em; line-height: 1.3; color: var(--ink); word-break: break-all;
  }
  .preview {
    position: relative; aspect-ratio: 16 / 9; background: var(--surface-2);
    border-bottom: 1px solid var(--rule);
  }
  .reveal { font-size: 11px; padding: 4px 10px; margin-top: 8px; width: 100%; }
  .del { font-size: 11px; padding: 4px 10px; margin-top: 8px; width: 100%; color: #d9534f; border-color: #d9534f; }
  .del:hover { background: #d9534f; color: #fff; }
  /* ancillary export disclosure (成品輔助) — visually secondary to the primary row */
  .moreexp { display: inline-block; margin-top: 8px; font-size: 10.5px; color: var(--quiet); cursor: pointer; }
  .moreexp:hover { color: var(--ink); }
  .exports.sub { margin-top: 6px; }
  .exp.ghost { font-size: 10.5px; color: var(--quiet); border-style: dashed; }
  .exp.ghost:hover { color: var(--ink); }
  .inout { display: flex; gap: 6px; margin-top: 8px; }
  .io { font-size: 10.5px; padding: 3px 8px; }
  .io.clr { color: var(--quiet); }
  .frametools { display: flex; gap: 6px; margin-top: 8px; align-items: center; }
  .fstep { font-size: 10.5px; padding: 3px 8px; font-variant-numeric: tabular-nums; }
  .previmg { width: 100%; height: 100%; object-fit: cover; display: block; }
  /* the real player: contain (don't crop footage) on black; audio sits at the bottom */
  video.previmg { object-fit: contain; background: #000; }
  /* LUT preview: drawn over the video (same contain box); takes over play/pause
     because it covers the native control bar. */
  .lutcanvas { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; background: #000; cursor: pointer; }
  .lutpill { position: absolute; top: 8px; right: 8px; display: flex; gap: 4px; }
  .lutbtn { font-size: 10px; padding: 2px 8px; background: rgba(0,0,0,0.55); color: #f3f2ee; border-color: rgba(255,255,255,0.25); }
  .lutbtn.on { border-color: #e8b04a; color: #e8b04a; }
  .cprow { display: flex; gap: 6px; align-items: center; }
  .cpsel { font: inherit; font-size: 11px; background: var(--surface-2); color: inherit; border: 1px solid var(--rule); padding: 1px 4px; max-width: 100%; }
  /* The native control bar renders light-on-white by default, which in this
     dark inspector reads as a broken white box rather than a play control
     (#420). `color-scheme` is what tells the engine to draw its own widgets
     dark — restyling the bar by hand would mean rebuilding play/scrub/time. */
  .prevaudio { position: absolute; left: 12px; right: 12px; bottom: 12px; width: auto; color-scheme: dark; }
  .playfail {
    position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 10px; background: #000;
  }
  .panoload { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: var(--surface-2); }
  .scrim {
    position: absolute; left: 0; right: 0; bottom: 0; height: 40%;
    background-image: linear-gradient(to top, rgba(0, 0, 0, 0.55), transparent); pointer-events: none;
  }
  .controls { position: absolute; left: 12px; bottom: 12px; right: 12px; display: flex; align-items: center; gap: 10px; }
  .track { flex: 1; height: 2px; background: rgba(243, 242, 238, 0.25); position: relative; }
  .trackfill { position: absolute; left: 0; top: 0; bottom: 0; width: 25%; background: #f3f2ee; }
  .trackhead { position: absolute; left: 25%; top: -3px; width: 1px; height: 8px; background: #f3f2ee; }
  .block { padding: 14px 18px; border-bottom: 1px solid var(--rule); }
  .metagrid { display: grid; grid-template-columns: 64px 1fr; row-gap: 4px; column-gap: 12px; font-size: 11.5px; }
  .blockhead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
  /* transcript flows naturally now — the whole inspector scrolls (see .inspector),
     so a long transcript extends the panel and is reached by scrolling it; no rule needed. */
  .langtabs { display: flex; align-items: center; gap: 4px; margin-bottom: 10px; flex-wrap: wrap; }
  .langtab {
    appearance: none; background: transparent; border: 1px solid var(--rule);
    font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.06em;
    color: var(--ink-2); padding: 3px 8px; cursor: pointer; line-height: 1;
  }
  .langtab.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); font-weight: 700; }
  .langset {
    appearance: none; background: transparent; border: 1px dashed var(--rule-hi);
    font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--quiet); padding: 3px 8px; cursor: pointer; margin-left: 4px;
  }
  .langset:hover { color: var(--ink); }
  .lines { display: flex; flex-direction: column; gap: 8px; font-size: 12px; line-height: 1.5; padding-right: 4px; }
  .line { display: flex; gap: 10px; }
  .line.seekable { cursor: pointer; }
  .line.seekable:hover .ttext { color: var(--invert); }
  .ttext { color: var(--ink); }
  .ttext.hl { border-bottom: 1px solid var(--invert); padding-bottom: 1px; }
  .lines.correctable .ttext { cursor: text; }
  .corrhint { margin-top: 8px; font-size: 10px; color: var(--quiet); }
  .corrpop { position: fixed; z-index: 900; max-width: 300px; }
  .corrchip {
    appearance: none; cursor: pointer; font-family: var(--ak-mono); font-size: 10.5px;
    background: var(--invert); color: var(--invert-ink); border: 1px solid var(--invert);
    padding: 4px 8px; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .corrrefuse {
    font-size: 11px; color: var(--ink); background: var(--surface-2);
    border: 1px dashed var(--rule-hi); padding: 6px 8px;
  }
  .corrform {
    width: 300px; box-sizing: border-box; background: var(--bg); color: var(--ink);
    box-shadow: inset 0 0 0 1px var(--invert); padding: 12px; display: flex; flex-direction: column; gap: 8px;
  }
  .corrfrom { font-size: 11px; color: var(--ink-2); word-break: break-all; }
  .corrfrom b { color: var(--ink); font-weight: 600; }
  .corrinput {
    font: inherit; font-size: 13px; background: var(--surface-2); color: var(--ink);
    border: 1px solid var(--rule-hi); padding: 5px 7px; width: 100%; box-sizing: border-box;
  }
  .corrapply { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--ink-2); }
  .correrr { font-size: 11px; color: var(--ink); border-left: 2px solid var(--invert); padding-left: 6px; word-break: break-all; }
  .corractions { display: flex; justify-content: flex-end; gap: 6px; }
  /* tags */
  .tagrow { display: flex; flex-wrap: wrap; gap: 5px; }
  .tagchip {
    display: inline-flex; align-items: center; gap: 4px;
    font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.03em;
    padding: 2px 4px 2px 7px; border: 1px solid var(--rule-hi); color: var(--ink);
    background: var(--surface-2);
  }
  .tagchip.auto { border-style: dashed; border-color: var(--rule); color: var(--ink-2); }
  .tagchip.canon { border-color: var(--invert); }
  .taghead-right { display: flex; align-items: center; gap: 10px; }
  .tagtoggle {
    appearance: none; background: transparent; border: 1px solid var(--rule);
    font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.06em;
    color: var(--ink-2); padding: 2px 6px; cursor: pointer;
  }
  .tagtoggle:hover { color: var(--ink); border-color: var(--ink); }
  .tagchip .tagname { white-space: nowrap; }
  .tagx {
    appearance: none; background: transparent; border: none; cursor: pointer;
    color: var(--ink-2); font-size: 13px; line-height: 1; padding: 0 2px;
  }
  .tagx:hover { color: var(--cyan); }
  .tagadd { display: flex; gap: 6px; margin-top: 8px; }
  .taginput { flex: 1; font-size: 11px; padding: 5px 8px; }
  .tagaddbtn { flex: 0 0 auto; padding: 5px 10px; }
  /* people (A6) */
  .people { display: flex; flex-direction: column; gap: 4px; }
  .person { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ink); }
  .person .pname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ptime {
    appearance: none; background: transparent; border: none; padding: 0; cursor: pointer;
    font-family: var(--ak-mono); font-size: 10px; color: var(--ink-2); text-decoration: underline dotted;
  }
  .ptime:hover { color: var(--cyan); }
  .personbtn { margin-top: 8px; font-size: 10px; width: 100%; }
  .personadd { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
  .personrow { display: flex; gap: 6px; }
  .psuggest { list-style: none; margin: 0; padding: 0; border: 1px solid var(--rule); background: var(--surface-2); }
  .psug {
    appearance: none; display: block; width: 100%; text-align: left; background: transparent; border: none;
    font: inherit; font-size: 11.5px; color: var(--ink); padding: 4px 8px; cursor: pointer;
  }
  .psug:hover, .psug.hi { background: var(--invert); color: var(--invert-ink); }
  /* reprocess */
  .reproc { display: flex; flex-direction: column; gap: 6px; }
  .reprow { display: flex; gap: 6px; }
  .relang { flex: 0 0 auto; font-size: 11px; padding: 5px 6px; }
  .rebtn { flex: 1; font-size: 10px; }
  .rebtn:disabled { opacity: 0.5; cursor: default; }
  /* B11: other exports dim while one runs; the running one stays legible */
  .exp:disabled { opacity: 0.45; cursor: default; }
  .exp[aria-busy='true'] { opacity: 1; cursor: progress; }
  .rate { padding: 14px 18px; display: flex; flex-direction: column; gap: 10px; }
  .ratebtns { display: flex; gap: 4px; }
  .ratebtn {
    flex: 1; font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 7px 0; background: transparent; color: var(--ink-2);
    border: 1px solid var(--rule); cursor: pointer; font-weight: 400;
  }
  .ratebtn.rev { border: 1px dashed var(--rule-hi); }
  .ratebtn.active { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); font-weight: 700; }
  .exports { display: flex; gap: 6px; margin-top: 6px; }
  .exp { flex: 1; }
  /* scene timeline */
  .scenes { display: flex; flex-direction: column; }
  .scene { display: flex; gap: 10px; padding: 8px 0; border-top: 1px solid var(--rule); }
  .scene:first-child { border-top: none; }
  .scenetime { flex: 0 0 40px; display: flex; align-items: center; gap: 5px; padding-top: 1px; }
  .dot { width: 5px; height: 5px; border-radius: 50%; background: var(--ink-2); flex: 0 0 auto; }
  .dot.hi { background: var(--invert); }
  .dot.lo { background: var(--quiet); }
  .scenebody { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .chip {
    font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.04em;
    padding: 2px 5px; border: 1px solid var(--rule); color: var(--ink-2); white-space: nowrap;
  }
  .chip.pos { border-color: var(--rule-hi); color: var(--ink); }
  .chip.dim { color: var(--quiet); }
  .scenedesc { font-size: 11.5px; line-height: 1.45; color: var(--ink); }
  .scenereason { font-size: 10.5px; line-height: 1.4; color: var(--quiet); font-style: italic; }
  .dragout { margin-top: 8px; }
  .draghandle {
    -webkit-user-drag: element;
    display: inline-block; padding: 3px 8px; border: 1px dashed var(--rule-hi);
    font-family: var(--ak-mono); font-size: 11px; cursor: grab; user-select: none;
  }
  .draghandle:active { cursor: grabbing; }
</style>
