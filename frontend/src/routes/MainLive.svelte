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
  import FilterRow from '../lib/FilterRow.svelte'
  import ViewToggle from '../lib/ViewToggle.svelte'
  import Inspector from '../lib/Inspector.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'

  const theme = 'dark'
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

  const fmtDur = (s) => {
    s = Math.round(s || 0)
    const m = Math.floor(s / 60), ss = s % 60
    const p = (n) => String(n).padStart(2, '0')
    return `00:${p(m)}:${p(ss)}`
  }
  const fmtSize = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`)

  // API media item → MediaCard's expected shape.
  const toCard = (it) => ({
    id: it.id,
    name: it.filename || `#${it.id}`,
    kind: it.has_audio && (it.width === 0 || !it.width) ? 'audio' : 'video',
    rating: it.rating || 'none', // backend null → unrated → '—'
    dur: fmtDur(it.duration_s),
    size: fmtSize(it.size_mb),
    thumb: api.thumbUrlFromPath(it.thumbnail_path),
    _raw: it,
  })

  async function load() {
    state = 'loading'
    try {
      const [s, m] = await Promise.all([api.getStats(), api.getMedia({ limit: 60 })])
      stats = s
      items = (m.items || []).map(toCard)
      if (items.length && selectedId == null) selectedId = items[0].id
      state = 'ok'
    } catch (e) {
      state = 'error'
      err = e.message + (e.body ? ' · ' + JSON.stringify(e.body) : '')
    }
  }

  async function runSearch() {
    if (!query.trim()) return load()
    state = 'loading'
    try {
      // Same-DB search via /api/media?q= → {items, total, search:true}.
      // NOT /api/search/all — that's cross-project federation over the
      // ~/.arkiv-projects.json registry, which is empty here → 0 results.
      const r = await api.getMedia({ q: query, limit: 60 })
      items = (r.items || []).map(toCard)
      selectedId = items.length ? items[0].id : null
      state = 'ok'
    } catch (e) {
      state = 'error'
      err = e.message
    }
  }

  onMount(load)

  $: visible = items.filter((m) => {
    if (activeFilter === 'video' && m.kind !== 'video') return false
    if (activeFilter === 'audio' && m.kind !== 'audio') return false
    if (activeRating && m.rating !== activeRating) return false
    return true
  })
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
      detail = await api.getMediaDetail(id)
    } catch (e) {
      detail = null
    }
  }
  $: if (selected && selected.id !== detailId) fetchDetail(selected.id)

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
  // transcript: segments_json = [{start,end,text}, ...]
  $: inspTranscript = detailLive
    ? (parseJson(detailLive.segments_json) || []).map((sg) => [secToTc(sg.start), sg.text, false])
    : null
  // vision: frame_tags_parsed = [{description, tags, ...}, ...]
  $: inspFrames = detailLive
    ? ((detailLive.frame_tags_parsed || []).map((f) => f.description).filter(Boolean) || null)
    : null
  $: inspThumb = selected ? selected.thumb : null
  $: inspPath = detailLive ? detailLive.path : null
</script>

<div class="artboard" data-theme={theme}>
  <TopBar />
  <div class="body">
    <PoolSidebar />

    <main class="center">
      <div class="toolrow">
        <div class="proj">
          <div class="ak-display projtitle">明燒肉</div>
          <Mono dim style="font-size:11px;margin-top:5px;letter-spacing:0.04em;">
            {#if stats}{stats.total} items · {Math.round(stats.total_duration_s || 0)}s · {Math.round(stats.total_size_mb || 0)} MB · live{:else}loading…{/if}
          </Mono>
        </div>
        <input
          class="ak-input livesearch"
          placeholder="搜尋（語意 + 關鍵字）… 試 餐廳 / 吧檯 / 燈光"
          bind:value={query}
          on:keydown={(e) => e.key === 'Enter' && runSearch()}
        />
        <FilterRow bind:activeFilter bind:activeRating />
        <ViewToggle bind:view />
      </div>

      <div class="gridwrap">
        {#if state === 'loading'}
          <div class="msg"><Mono dim>loading…</Mono></div>
        {:else if state === 'error'}
          <div class="msg"><Mono style="color:var(--cyan);">ERROR: {err}</Mono></div>
        {:else if visible.length === 0}
          <div class="msg"><Eyebrow>No media</Eyebrow><Mono dim>ingest 一些素材,或清搜尋。</Mono></div>
        {:else}
          <div class="mediagrid">
            {#each visible as m (m.id)}
              <MediaCard
                {m}
                {theme}
                thumbUrl={m.thumb}
                selected={m.id === selectedId}
                hover={m.id === hoverId}
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
        thumbUrl={inspThumb}
        pathLabel={inspPath}
        transcriptLines={inspTranscript}
        frameDescriptions={inspFrames}
      />
    {/if}
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; position: relative; display: grid; grid-template-rows: 52px 1fr; background: var(--bg); color: var(--ink); overflow: hidden; margin: 0 auto; }
  .body { display: grid; grid-template-columns: 220px 1fr 340px; min-height: 0; }
  .center { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .toolrow { display: flex; align-items: center; gap: 14px; padding: 14px 22px; border-bottom: 1px solid var(--rule); }
  .proj { min-width: 0; }
  .projtitle { font-size: 28px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  .livesearch { flex: 1; max-width: 360px; font-size: 12px; }
  .gridwrap { flex: 1; overflow: auto; position: relative; }
  .mediagrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; padding: 22px; background: var(--rule); }
  .msg { padding: 40px 22px; display: flex; flex-direction: column; gap: 8px; }
</style>
