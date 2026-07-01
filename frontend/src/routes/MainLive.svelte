<!-- B1 — main grid wired to LIVE backend data (real ingested media).
     Same chrome as MainDark; data from /api/media + /api/stats + /api/search/all.
     Field map verified vs live DB (2026-05-31): items carry filename,
     duration_s(number), size_mb(number), width/height/fps, has_audio, lang,
     thumbnail_path(abs fs path → /thumbnails/<basename>), rating(null), tags[]. -->
<script>
  import { onMount } from 'svelte'
  import * as api from '../lib/api.js'
  import TopBar from '../lib/TopBar.svelte'
  import PoolSidebar from '../lib/PoolSidebar.svelte'
  import MediaCard from '../lib/MediaCard.svelte'
  import Rating from '../lib/Rating.svelte'
  import FilterRow from '../lib/FilterRow.svelte'
  import ViewToggle from '../lib/ViewToggle.svelte'
  import Inspector from '../lib/Inspector.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { resolvedTheme } from '../lib/prefs.js'
  import { pushToast } from '../lib/toast.js'

  $: theme = $resolvedTheme
  let state = 'loading' // loading | ok | error
  let err = ''
  let items = []
  let stats = null
  let selectedId = null
  let hoverId = null
  let activeFilter = 'all'
  let activeRating = null
  let view = 'grid'
  let query = ''
  let activeCamera = null
  // Normalize a raw camera_model into a browsable machine category so the pool
  // groups clips by device (A7 V / FX30 / iPhone) instead of fragmenting on
  // per-focal-length model strings ("...iPhone 16 Pro 48mm" etc.).
  const camCategory = (model) => {
    if (!model) return null
    const m = model.toLowerCase()
    if (m.includes('ilce-7m5') || m.includes('a7')) return 'Sony A7 V'
    if (m.includes('fx30')) return 'Sony FX30'
    if (m.includes('iphone')) return 'iPhone'
    return model
  }

  const fmtDur = (s) => {
    s = Math.round(s || 0)
    const m = Math.floor(s / 60), ss = s % 60
    const p = (n) => String(n).padStart(2, '0')
    return `00:${p(m)}:${p(ss)}`
  }
  const fmtSize = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`)
  // date column for list view — just the YYYY-MM-DD slice of processed_at
  const fmtDate = (iso) => (iso ? String(iso).slice(0, 10) : '—')

  // backend rating value (good/ng/review/null) → UI value (good/ng/rev/none).
  const ratingToUi = (r) => (r === 'review' ? 'rev' : r || 'none')
  // API media item → MediaCard's expected shape.
  const toCard = (it) => ({
    id: it.id,
    name: it.filename || `#${it.id}`,
    kind: it.has_audio && (it.width === 0 || !it.width) ? 'audio' : 'video',
    rating: ratingToUi(it.rating),
    dur: fmtDur(it.duration_s),
    size: fmtSize(it.size_mb),
    thumb: api.thumbUrlFromPath(it.thumbnail_path),
    _raw: it,
  })

  let liveTags = null
  let liveCollections = null
  let engineLangs = null // [{code,label}] for the inspector's retranscribe picker

  // Multi-select for batch timeline export. Click order = sequence order on the
  // exported timeline (what the user picks first lands first in Resolve).
  let picked = [] // array of media ids, in pick order
  $: pickedSet = new Set(picked)
  function togglePick(id) {
    picked = picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]
  }
  const clearPicks = () => (picked = [])
  const EXPORT_FMTS = ['edl', 'fcpxml', 'srt']
  async function exportTimeline(fmt) {
    if (!picked.length) return
    try {
      await api.downloadFile(api.exportTimelinePath(picked, fmt), `arkiv-timeline.${fmt}`)
      pushToast(`時間軸已匯出 · ${picked.length} 支 · ${fmt.toUpperCase()}`)
    } catch (e) {
      pushToast(`匯出失敗: ${e.message}`, 'error')
    }
  }
  // DaVinci Resolve metadata CSV (auth-safe download). ids=null → whole library;
  // ids=picked → just the selected clips (camera-report / DIT handoff deliverable).
  async function exportMetadataCsv(ids = null) {
    try {
      await api.downloadFile(api.metadataCsvPath(ids), 'arkiv_davinci_metadata.csv')
      pushToast(ids ? `中繼資料 CSV 已匯出 · ${ids.length} 支` : '中繼資料 CSV 已匯出 · 全庫')
    } catch (e) {
      pushToast(`CSV 匯出失敗: ${e.message}`, 'error')
    }
  }
  // single-clip export from the inspector (auth-safe download).
  async function exportClip(id, fmt, name, trim = null) {
    const stem = (name || `media_${id}`).replace(/\.[^.]+$/, '')
    // trim = {in_s, out_s?} from the inspector IN/OUT points → backend trims the export
    let path = api.exportPath(id, fmt)
    if (trim && (trim.in_s != null || trim.out_s != null)) {
      const q = []
      if (trim.in_s != null) q.push(`in_s=${trim.in_s}`)
      if (trim.out_s != null) q.push(`out_s=${trim.out_s}`)
      path += `?${q.join('&')}`
    }
    try {
      await api.downloadFile(path, `${stem}.${fmt}`)
      pushToast(`已匯出 · ${stem}.${fmt}`)
    } catch (e) {
      pushToast(`匯出失敗: ${e.message}`, 'error')
    }
  }
  const _stem = (id, name) => (name || `media_${id}`).replace(/\.[^.]+$/, '')
  async function exportChapters(id, name, format = 'youtube') {
    try {
      const r = await api.getChapters(id, format)
      api.downloadText(r.chapters || '', `${_stem(id, name)}.chapters.txt`)
      pushToast(`章節已匯出 · ${_stem(id, name)}.chapters.txt`)
    } catch (e) { pushToast(`章節匯出失敗: ${e.message}`, 'error') }
  }
  async function exportRemotion(id, name) {
    try {
      const r = await api.getRemotionProps(id)
      api.downloadText(JSON.stringify(r, null, 2), `${_stem(id, name)}.remotion.json`, 'application/json')
      pushToast(`Remotion props 已匯出 · ${_stem(id, name)}.remotion.json`)
    } catch (e) { pushToast(`Remotion 匯出失敗: ${e.message}`, 'error') }
  }
  async function revealFile(path) {
    try { await api.openFile(path, true) } catch (e) { pushToast(`在 Finder 顯示失敗: ${e.message}`, 'error') }
  }

  async function load() {
    state = 'loading'
    try {
      const [s, m, t, c] = await Promise.all([
        api.getStats(), api.getMedia({ limit: 500 }), api.getTags(), api.getCollections(),
      ])
      stats = s
      items = (m.items || []).map(toCard)
      liveTags = (t || []).map((x) => ({ name: x.name, count: x.count }))
      liveCollections = (c?.collections || []).map((col) => ({
        key: col.key, title: col.title, count: col.count,
        items: col.items || [], // full member items (id/filename/thumb/duration_s/score)
      }))
      if (items.length && selectedId == null) selectedId = items[0].id
      state = 'ok'
    } catch (e) {
      state = 'error'
      err = e.message + (e.body ? ' · ' + JSON.stringify(e.body) : '')
    }
  }

  // E1 — click a Smart Collection → show its members directly. /api/collections
  // already returns every member with id/filename/thumb/duration_s (classified
  // over the FULL library server-side), so build cards straight from those —
  // no /api/media re-fetch, no first-N cap (Codex review P2).
  const fmtDurS = (s) => fmtDur(s)
  let activeCollection = null
  // 機型 quick-browse: toggle a camera filter. Camera browse operates on the
  // full library, so if we're in a collection/search subset, reset to the full
  // pool first (that view carries camera_model; a collection subset doesn't).
  function onCameraClick(model) {
    const next = activeCamera === model ? null : model
    activeCamera = next
    if (next && (activeCollection || query)) { activeCollection = null; query = ''; load() }
  }
  // Smart Pools → the rating dimension (shared with the toolbar FilterRow).
  // 'Unrated' maps to rating 'none' (ratingToUi(null)), which the visible
  // filter already handles. Clicking the active pool toggles back to all.
  function onPoolClick(label) {
    activeCamera = null
    if (label === 'All media') { activeRating = null; activeFilter = 'all'; return }
    const map = { 'Needs review': 'rev', 'Rated good': 'good', 'N·G': 'ng', 'Unrated': 'none' }
    const target = map[label]
    if (target === undefined) return
    activeRating = activeRating === target ? null : target
  }
  // Derived pool highlight — kept in sync with activeRating so the sidebar and
  // the toolbar rating buttons never disagree.
  $: activePool =
    activeRating === 'good' ? 'Rated good'
    : activeRating === 'rev' ? 'Needs review'
    : activeRating === 'ng' ? 'N·G'
    : activeRating === 'none' ? 'Unrated'
    : 'All media'
  function onCollectionClick(col) {
    query = ''
    activeCamera = null
    activeCollection = col.key
    items = (col.items || []).map((it) => ({
      id: it.id,
      name: it.filename || `#${it.id}`,
      kind: 'video',
      rating: 'none',
      dur: fmtDurS(it.duration_s),
      size: '—',
      thumb: it.thumb || null, // already a root-relative /thumbnails/<name> path
      _raw: it,
    }))
    selectedId = items.length ? items[0].id : null
  }

  // D — live sidebar derived data.
  $: livePools = stats
    ? [
        ['All media', stats.total],
        ['Needs review', stats.rating?.review ?? 0],
        ['Rated good', stats.rating?.good ?? 0],
        ['N·G', stats.rating?.ng ?? 0],
        ['Unrated', stats.rating?.unrated ?? 0],
      ]
    : null
  $: projectName = (stats && stats.project) || '素材庫'
  $: liveProjects = stats ? [{ id: 'proj', name: projectName, count: stats.total, active: true }] : null
  // real disk usage for the sidebar Storage footer (replaces the mock placeholder)
  $: liveStorage = stats?.disk ?? null
  // tag click → search that tag
  function onTagClick(name) {
    query = name
    runSearch()
  }

  async function runSearch() {
    if (!query.trim()) return load()
    activeCamera = null
    state = 'loading'
    try {
      // Same-DB search via /api/media?q= → {items, total, search:true}.
      // NOT /api/search/all — that's cross-project federation over the
      // ~/.arkiv-projects.json registry, which is empty here → 0 results.
      const r = await api.getMedia({ q: query, limit: 500 })
      items = (r.items || []).map(toCard)
      selectedId = items.length ? items[0].id : null
      state = 'ok'
    } catch (e) {
      state = 'error'
      err = e.message
    }
  }

  // Deep-link from the ranked search screen: #/main-live?sel=<id> selects that
  // clip. If it's outside the first page, fetch it by id and prepend so the grid
  // + inspector show it instead of silently falling back to the default (Codex
  // review P2).
  function readSelParam() {
    const h = window.location.hash
    const qi = h.indexOf('?')
    if (qi === -1) return null
    const v = new URLSearchParams(h.slice(qi + 1)).get('sel')
    const id = v == null ? null : Number(v)
    return Number.isFinite(id) ? id : null
  }
  async function selectFromParam() {
    const sel = readSelParam()
    if (sel == null) return
    if (items.find((m) => m.id === sel)) { selectedId = sel; return }
    try {
      const d = await api.getMediaDetail(sel)
      if (d && d.id != null) { items = [toCard(d), ...items]; selectedId = sel }
    } catch (e) { /* unknown id → keep default selection */ }
  }
  // Engine languages for the retranscribe picker — non-fatal (mock fallback in UI).
  async function loadEngines() {
    try {
      const e = await api.getIngestEngines()
      engineLangs = e?.languages || null
    } catch (e) { /* picker falls back to its built-in zh/en list */ }
  }
  onMount(async () => {
    await load()
    await selectFromParam()
    loadEngines()
  })

  $: visible = items.filter((m) => {
    if (activeFilter === 'video' && m.kind !== 'video') return false
    if (activeFilter === 'audio' && m.kind !== 'audio') return false
    if (activeRating && m.rating !== activeRating) return false
    if (activeCamera && camCategory(m._raw && m._raw.camera_model) !== activeCamera) return false
    return true
  })
  // Camera categories present in the current pool, for the sidebar 機型 browser.
  $: liveCameras = (() => {
    const c = {}
    for (const m of items) {
      const cat = camCategory(m._raw && m._raw.camera_model)
      if (cat) c[cat] = (c[cat] || 0) + 1
    }
    const arr = Object.entries(c).map(([model, count]) => ({ model, count })).sort((a, b) => b.count - a.count)
    return arr.length ? arr : null
  })()
  $: selected = items.find((m) => m.id === selectedId) || items[0] || null

  // Inspector base (from grid item; always available so panel renders instantly).
  $: inspectorMedia = selected
    ? {
        id: selected.id, name: selected.name, kind: selected.kind,
        dur: selected.dur, size: selected.size,
        fps: selected._raw.fps ? Math.round(selected._raw.fps) : 24,
        res: selected._raw.width ? `${selected._raw.width}×${selected._raw.height}` : '—',
        cam: [selected._raw.camera_make, selected._raw.camera_model].filter(Boolean).join(' ') || '—',
        lens: selected._raw.lens_model || '—',
        tc: selected._raw.start_tc || '—',
        iso: selected._raw.iso ?? '—', ap: selected._raw.aperture || '—', fl: selected._raw.focal_length || '—',
        rating: selected.rating,
      }
    : null

  // Live detail: fetch /api/media/{id} on selection change for transcript + vision.
  // Detail carries segments_json (transcript) + frame_tags_parsed (vision).
  let detail = null
  let detailId = null
  async function fetchDetail(id) {
    detailId = id
    detail = null
    try {
      const d = await api.getMediaDetail(id)
      // Ignore a stale response: if the selection moved on (e.g. deep-link
      // selecting a clip while the default's fetch was still in flight) the
      // current detailId no longer matches, and assigning would leave the
      // inspector showing nothing for the now-selected clip (Codex review P2).
      if (detailId === id) detail = d
    } catch (e) {
      if (detailId === id) detail = null
    }
  }
  $: if (selected && selected.id !== detailId) fetchDetail(selected.id)

  // G2: per-language transcripts for the inspector's language tabs.
  let transcriptsData = null // {active_lang, transcripts:[{lang, segments_json, active}]}
  let transcriptsId = null
  let viewLang = null
  async function fetchTranscripts(id) {
    transcriptsId = id; transcriptsData = null; viewLang = null
    try {
      const d = await api.getTranscripts(id)
      if (transcriptsId === id) { transcriptsData = d; viewLang = d.active_lang }
    } catch { if (transcriptsId === id) transcriptsData = null }
  }
  $: if (selected && selected.id !== transcriptsId) fetchTranscripts(selected.id)
  function onViewLang(lang) { viewLang = lang }
  async function onActivateLang(lang) {
    if (!selected) return
    try {
      await api.activateTranscript(selected.id, lang)
      await Promise.all([fetchDetail(selected.id), fetchTranscripts(selected.id)])
    } catch (e) { err = `切換主要語言失敗: ${e.message}` }
  }

  // Real audio waveform peaks (0..1) from the backend ffmpeg endpoint — fetched
  // per clip, passed to the inspector's <Waveform>. Replaces the old mock sin bars.
  let wavePeaks = null
  let waveId = null
  async function fetchWaveform(id) {
    waveId = id; wavePeaks = null
    try {
      const w = await api.getWaveform(id)
      if (waveId === id) wavePeaks = (w && w.peaks) || null
    } catch (e) { if (waveId === id) wavePeaks = null }
  }
  $: if (selected && selected.id !== waveId) fetchWaveform(selected.id)
  $: inspPeaks = waveId === (selected && selected.id) ? wavePeaks : null

  const secToTc = (s) => {
    s = Math.round(s || 0)
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  }
  const parseJson = (v) => {
    if (!v) return null
    if (typeof v !== 'string') return v
    try { return JSON.parse(v) } catch { return null }
  }
  $: detailLive = detail && detail.id === (selected && selected.id) ? detail : null
  // G2: language tabs + the segments of whichever language is being viewed.
  $: tdLive = transcriptsData && transcriptsId === (selected && selected.id) ? transcriptsData : null
  $: transcriptLangs = tdLive ? tdLive.transcripts.map((t) => ({ lang: t.lang, active: !!t.active })) : null
  // transcript: show the viewed language's segments_json when tabs are loaded,
  // else fall back to the live detail's active transcript.
  $: _displaySegs = (tdLive && viewLang)
    ? (parseJson((tdLive.transcripts.find((t) => t.lang === viewLang) || {}).segments_json) || [])
    : (detailLive ? (parseJson(detailLive.segments_json) || []) : null)
  $: inspTranscript = _displaySegs
    ? _displaySegs.map((sg) => [secToTc(sg.start), sg.text, false, sg.start])
    : null
  // vision: frame_tags_parsed = [{description, tags, ...}, ...]
  $: inspFrames = detailLive
    ? ((detailLive.frame_tags_parsed || []).map((f) => f.description).filter(Boolean) || null)
    : null
  // scene timeline: richer per-frame vision metadata. Frames are sampled evenly
  // across the clip, so approximate each frame's timecode from the clip duration.
  $: inspScenes = detailLive
    ? (() => {
        const frames = detailLive.frame_tags_parsed || []
        if (!frames.length) return null
        const dur = (selected && selected._raw && selected._raw.duration_s) || 0
        const denom = frames.length > 1 ? frames.length - 1 : 1
        return frames.map((f, i) => ({
          tc: dur ? secToTc((i / denom) * dur) : `f${i + 1}`,
          description: f.description || '',
          content_type: f.content_type || null,
          atmosphere: f.atmosphere || null,
          energy: f.energy || null,
          edit_position: f.edit_position || null,
          edit_reason: f.edit_reason || null,
          focus_score: f.focus_score ?? null,
        }))
      })()
    : null
  $: inspThumb = selected ? selected.thumb : null
  // Real playback stream for the inspector player (loopback = token-free; remote
  // carries ?token). Inspector turns this into a <video>/<audio> element.
  $: inspVideoSrc = selected ? api.streamUrl(selected.id) : null
  $: inspPath = detailLive ? detailLive.path : null
  // tags: detail carries the quality-filtered tag list [{id,name,source}].
  $: inspTags = detailLive ? (detailLive.tags || []) : null
  // LLM-canonicalized tags (string[]) when the canonicalize pass has run; null otherwise.
  $: inspCanonTags = detailLive ? (detailLive.canonical_tags || null) : null

  // Tag editing. Both endpoints return the full updated tag list, so reconcile
  // `detail.tags` from the response (reassign detail so detailLive recomputes).
  async function addTag(name) {
    const n = (name || '').trim()
    if (!n || !selected) return
    const id = selected.id
    try {
      const r = await api.addTag(id, n)
      if (detail && detail.id === id) detail = { ...detail, tags: r.tags }
    } catch (e) {
      err = `加標籤失敗: ${e.message}`
    }
  }
  async function removeTag(name) {
    if (!selected) return
    const id = selected.id
    const prev = detail && detail.id === id ? detail.tags : null
    // optimistic: drop it immediately, reconcile (or revert) when the call lands
    if (detail && detail.id === id) {
      detail = { ...detail, tags: (detail.tags || []).filter((t) => t.name !== name) }
    }
    try {
      const r = await api.removeTag(id, name)
      if (detail && detail.id === id) detail = { ...detail, tags: r.tags }
    } catch (e) {
      if (detail && detail.id === id && prev) detail = { ...detail, tags: prev }
      err = `刪標籤失敗: ${e.message}`
    }
  }

  // Per-clip re-processing. Returns {ok, message} for the inspector to surface;
  // throws bubble up to the inspector's catch. Refetch detail on success so the
  // transcript / vision / tags reflect the new run.
  async function reprocess(action, opts = {}) {
    if (!selected) return { ok: false, message: '未選取素材' }
    const id = selected.id
    if (action === 'retranscribe') {
      const r = await api.retranscribe(id, opts.language || 'zh')
      if (detailId === id) await fetchDetail(id)
      await fetchTranscripts(id)  // G2: surface the (possibly new) language as a tab
      return { ok: r.ok, message: `轉錄完成 · ${r.transcript_length} 字 · ${r.language}` }
    }
    if (action === 'retry-vision') {
      const r = await api.retryVision(id)
      if (detailId === id) await fetchDetail(id)
      return {
        ok: r.ok,
        message: r.message || `視覺補上 ${r.patched}/${r.total_frames} 幀，剩 ${r.still_empty}`,
      }
    }
    if (action === 'reingest') {
      const r = await api.reingest(id)
      if (detailId === id) await fetchDetail(id)
      return { ok: r.ok, message: r.ok ? '完整重建完成' : '重建失敗（見後端 log）' }
    }
    if (action === 'proxy') {
      // background build — returns immediately, no detail change to refetch
      const r = await api.buildProxyOne(id)
      return { ok: true, message: r.message || '已排入 proxy 生成（背景）' }
    }
    return { ok: false, message: `未知動作: ${action}` }
  }

  // C — rating write. UI value → backend value (db.set_rating: good/ng/review/None).
  const RATING_MAP = { good: 'good', rev: 'review', ng: 'ng', none: null }
  async function rate(uiRating) {
    if (!selected) return
    const backendVal = RATING_MAP[uiRating] ?? null
    const id = selected.id
    // preserve any existing rating_note (backend PATCH overwrites both fields →
    // omitting note would silently delete it). Codex review P2.
    const note = detailLive && detailLive.id === id ? detailLive.rating_note ?? null : null
    // optimistic: reflect immediately in grid + inspector (which both read item.rating)
    const prev = selected.rating
    items = items.map((m) => (m.id === id ? { ...m, rating: uiRating } : m))
    try {
      await api.setRating(id, backendVal, note)
    } catch (e) {
      // revert on failure
      items = items.map((m) => (m.id === id ? { ...m, rating: prev } : m))
      err = `rating 寫入失敗: ${e.message}`
    }
  }
</script>

<div class="artboard" data-theme={theme}>
  <TopBar />
  <div class="body">
    <PoolSidebar {liveProjects} {livePools} {liveTags} {liveCollections} {liveStorage} {liveCameras} onTag={onTagClick} onCollection={onCollectionClick} onCamera={onCameraClick} {activeCamera} onPool={onPoolClick} {activePool} />

    <main class="center">
      <div class="toolrow">
        <div class="proj">
          <div class="ak-display projtitle">{projectName}</div>
          <Mono dim style="font-size:11px;margin-top:5px;letter-spacing:0.04em;">
            {#if stats}{stats.total} items · {Math.round(stats.total_duration_s || 0)}s · {Math.round(stats.total_size_mb || 0)} MB · live{:else}loading…{/if}
          </Mono>
        </div>
        <input
          class="ak-input livesearch"
          placeholder="搜尋（語意 + 關鍵字）…"
          bind:value={query}
          on:keydown={(e) => e.key === 'Enter' && runSearch()}
        />
        <a
          class="ak-btn ranked"
          href={query.trim() ? `#/search-live?q=${encodeURIComponent(query.trim())}` : '#/search-live'}
          title="排名檢視（score + 摘要）"
        >排名 →</a>
        <button
          class="ak-btn metacsv"
          on:click={() => exportMetadataCsv(null)}
          title="匯出整庫 metadata CSV（DaVinci Resolve）"
        >CSV ↓</button>
        <FilterRow bind:activeFilter bind:activeRating />
        <ViewToggle bind:view />
      </div>

      {#if picked.length}
        <div class="exportbar">
          <Mono style="font-size:11px;letter-spacing:0.04em;">已選 {picked.length} 支 → 合成一條時間軸</Mono>
          <div class="expbtns">
            {#each EXPORT_FMTS as fmt}
              <button class="ak-btn expbtn" on:click={() => exportTimeline(fmt)}>{fmt.toUpperCase()}</button>
            {/each}
            <button class="ak-btn expbtn" on:click={() => exportMetadataCsv(picked)} title="匯出所選 metadata CSV">CSV</button>
            <button class="ak-btn expbtn clear" on:click={clearPicks}>清除</button>
          </div>
        </div>
      {/if}

      <div class="gridwrap">
        {#if state === 'loading'}
          <div class="msg"><Mono dim>loading…</Mono></div>
        {:else if state === 'error'}
          <div class="msg"><Mono style="color:var(--cyan);">ERROR: {err}</Mono></div>
        {:else if visible.length === 0}
          <div class="msg"><Eyebrow>No media</Eyebrow><Mono dim>ingest 一些素材,或清搜尋。</Mono></div>
        {:else if view === 'list'}
          <table class="medialist">
            <thead>
              <tr>
                <th class="c-chk"></th>
                <th class="c-thumb"></th>
                <th class="c-name">檔名</th>
                <th>評分</th>
                <th class="num">解析度</th>
                <th class="num">時長</th>
                <th class="num">容量</th>
                <th class="num">匯入日期</th>
              </tr>
            </thead>
            <tbody>
              {#each visible as m (m.id)}
                <tr
                  class:sel={m.id === selectedId}
                  on:click={() => (selectedId = m.id)}
                >
                  <td class="c-chk">
                    <input
                      type="checkbox"
                      checked={pickedSet.has(m.id)}
                      on:click|stopPropagation={() => togglePick(m.id)}
                    />
                  </td>
                  <td class="c-thumb">
                    {#if m.thumb}<img src={m.thumb} alt="" />{:else}<div class="lthumbph" class:audio={m.kind === 'audio'}></div>{/if}
                  </td>
                  <td class="c-name" title={m.name}>{m.name}</td>
                  <td><Rating value={m.rating} /></td>
                  <td class="num mono">{m._raw?.width && m._raw?.height ? `${m._raw.width}×${m._raw.height}` : '—'}</td>
                  <td class="num mono">{m.dur}</td>
                  <td class="num mono">{m.size}</td>
                  <td class="num mono dim">{fmtDate(m._raw?.processed_at)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else}
          <div class="mediagrid">
            {#each visible as m (m.id)}
              <MediaCard
                {m}
                {theme}
                thumbUrl={m.thumb}
                selected={m.id === selectedId}
                hover={m.id === hoverId}
                selectable={true}
                checked={pickedSet.has(m.id)}
                on:toggle={(e) => togglePick(e.detail)}
                on:click={() => (selectedId = m.id)}
                on:mouseenter={() => (hoverId = m.id)}
                on:mouseleave={() => (hoverId = null)}
              />
            {/each}
          </div>
        {/if}
      </div>
    </main>

    {#if inspectorMedia}
      <Inspector
        media={inspectorMedia}
        {theme}
        live={true}
        thumbUrl={inspThumb}
        videoSrc={inspVideoSrc}
        peaks={inspPeaks}
        pathLabel={inspPath}
        transcriptLines={inspTranscript}
        {transcriptLangs}
        {viewLang}
        {onViewLang}
        {onActivateLang}
        frameDescriptions={inspFrames}
        frameScenes={inspScenes}
        tags={inspTags}
        canonicalTags={inspCanonTags}
        onAddTag={selected ? addTag : null}
        onRemoveTag={selected ? removeTag : null}
        onReprocess={selected ? reprocess : null}
        languages={engineLangs}
        mediaLang={detailLive ? detailLive.lang : null}
        onExport={selected ? (fmt, trim) => exportClip(selected.id, fmt, selected.name, trim) : null}
        onChapters={selected ? (fmt) => exportChapters(selected.id, selected.name, fmt) : null}
        onRemotion={selected ? () => exportRemotion(selected.id, selected.name) : null}
        onReveal={inspPath ? () => revealFile(inspPath) : null}
        onRate={rate}
      />
    {/if}
  </div>
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; position: relative; display: grid; grid-template-rows: 52px 1fr; background: var(--bg); color: var(--ink); overflow: hidden; margin: 0 auto; }
  .body { display: grid; grid-template-columns: 220px 1fr 340px; min-height: 0; }
  .center { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .toolrow { display: flex; align-items: center; gap: 14px; padding: 14px 22px; border-bottom: 1px solid var(--rule); }
  .proj { min-width: 0; }
  .projtitle { font-size: 28px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  .livesearch { flex: 1; max-width: 360px; font-size: 12px; }
  .ranked { font-size: 10px; padding: 6px 10px; text-decoration: none; white-space: nowrap; }
  .metacsv { font-size: 10px; padding: 6px 10px; white-space: nowrap; }
  .gridwrap { flex: 1; overflow: auto; position: relative; }
  /* G9: prototype card width = 198px (runtime-measured from the 1400px design canvas).
     repeat(4,1fr) ballooned cards on wide windows (310px @1920) and squeezed them on
     narrow (168px @1280). auto-fill holds the design rhythm and reflows column count;
     auto-fill (not auto-fit) so a sparse last row keeps card size instead of stretching. */
  .mediagrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(198px, 1fr)); gap: 1px; padding: 22px; background: var(--rule); }

  /* G1 list view */
  .medialist { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .medialist thead th {
    position: sticky; top: 0; z-index: 1; background: var(--bg);
    text-align: left; padding: 9px 14px; border-bottom: 1px solid var(--rule);
    font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--quiet-2); font-weight: 400; white-space: nowrap;
  }
  .medialist th.num, .medialist td.num { text-align: right; }
  .medialist tbody tr { cursor: pointer; border-bottom: 1px solid var(--rule); }
  .medialist tbody tr:hover { background: var(--rule); }
  .medialist tbody tr.sel { background: var(--invert); color: var(--invert-ink); }
  .medialist td { padding: 7px 14px; vertical-align: middle; white-space: nowrap; }
  .medialist td.c-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 320px; width: 100%; }
  .medialist td.mono { font-family: var(--ak-mono); font-size: 11px; }
  .medialist td.dim { color: var(--quiet); }
  .medialist tr.sel td.dim { color: var(--invert-ink); }
  .c-chk { width: 28px; }
  .c-thumb { width: 56px; }
  .medialist td.c-thumb img, .medialist td.c-thumb .lthumbph {
    width: 44px; height: 25px; object-fit: cover; display: block; background: var(--rule-hi);
  }
  .lthumbph.audio { background: repeating-linear-gradient(90deg, var(--rule-hi) 0 2px, transparent 2px 4px); }

  .msg { padding: 40px 22px; display: flex; flex-direction: column; gap: 8px; }
  .exportbar {
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
    padding: 9px 22px; border-bottom: 1px solid var(--rule); background: var(--surface-2);
  }
  .expbtns { display: flex; gap: 6px; }
  .expbtn { font-size: 10px; padding: 5px 10px; text-decoration: none; }
  .expbtn.clear { opacity: 0.7; }
</style>
