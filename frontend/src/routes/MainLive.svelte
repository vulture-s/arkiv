<!-- B1 — main grid wired to LIVE backend data (real ingested media).
     Same chrome as MainDark; data from /api/media + /api/stats + /api/search/all.
     Field map verified vs live DB (2026-05-31): items carry filename,
     duration_s(number), size_mb(number), width/height/fps, has_audio, lang,
     thumbnail_path(abs fs path → /thumbnails/<basename>), rating(null), tags[]. -->
<script>
  import { onMount } from 'svelte'
  import { createExportGuard, downloadedMsg } from '../lib/exportFeedback.js'
  import * as api from '../lib/api.js'
  import { describeDelete } from '../lib/deleteOutcome.js'
  import { describeLearn } from '../lib/transcriptCorrect.js'
  import TopBar from '../lib/TopBar.svelte'
  import PoolSidebar from '../lib/PoolSidebar.svelte'
  import MediaCard from '../lib/MediaCard.svelte'
  import Rating from '../lib/Rating.svelte'
  import FilterRow from '../lib/FilterRow.svelte'
  import ViewToggle from '../lib/ViewToggle.svelte'
  import Inspector from '../lib/Inspector.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import ConfirmDialog from '../lib/ConfirmDialog.svelte'
  import TrashModal from '../lib/TrashModal.svelte'
  import { resolvedTheme } from '../lib/prefs.js'
  import { pushToast } from '../lib/toast.js'
  import { canSwitchLibrary, libraryState, switchLibrary, busyMessage, uniqueName, baseName, libraryRows } from '../lib/librarySwitch.js'
  import { pickFolder } from '../lib/pickFolder.js'
  import { addErrorMessage } from '../lib/projectsFirstRun.js'
  import { shouldShowWizard } from '../lib/setupWizard.js'
  import { canDragOut } from '../lib/dragOut.js'
  import { createViewGen } from '../lib/viewGen.js'
  import { shotYearRows, mediaParams } from '../lib/shotYear.js'
  import { parseStartParam } from '../lib/matchTime.js'
  import { createIdentityNamesLoader } from '../lib/identityNames.js'
  import { rateMany } from '../lib/bulkRating.js'
  import { POOL_SORTS, loadPoolSort, savePoolSort, nextColumnSort, columnArrow } from '../lib/poolSort.js'

  $: theme = $resolvedTheme
  let state = 'loading' // loading | ok | error
  let err = ''
  let items = []
  // Four paths replace the grid (load / runSearch / loadIds / picking a collection) and
  // three of them await the network first, so the response that lands last used to win
  // regardless of what the user asked for last — picking a collection while a search was
  // in flight left the sidebar highlighting the collection and the grid showing the
  // search. Each of them now carries a generation token across its awaits.
  const viewGen = createViewGen()
  // R5-20 (#37): the grid used to cap at 500 with no indicator — clips 501+ were
  // unreachable from browse. `total` is the server-side pool count; `moreParams`
  // holds the request to re-issue with an incrementing offset (null = fully
  // loaded / not a paginated view, e.g. a Smart Collection or deep-link subset).
  const PAGE = 500
  let total = 0
  let moreParams = null
  let loadingMore = false
  let stats = null
  let registryProjects = null // /api/projects rows; null = not loaded / unreachable
  let libState = null // desktop only: {active, default} from the shell (#561)
  let switching = false
  if (canSwitchLibrary()) libraryState().then((s) => (libState = s)).catch(() => {})
  let entitlement = null // /api/entitlements; null = not loaded (section then says nothing)
  // 9.8a: a first-run user (wizard never finished AND no media of their own) lands
  // on #/setup once per app start. Checked once — reloads of the grid must not
  // bounce someone who just pressed 略過 back into the wizard.
  let wizardChecked = false
  function maybeOpenWizard(st, si) {
    if (wizardChecked) return
    wizardChecked = true
    api.setupStatus()
      .then((setup) => { if (shouldShowWizard(setup, st, si)) window.location.hash = '#/setup' })
      .catch(() => {}) // an older backend without /api/setup: no wizard, nothing else changes
  }
  // First-run readiness gate: 'checking' until /api/health answers, then
  // 'ready' | 'notready' (deps missing, 503) | 'unreachable' (backend down).
  let health = null
  let readyState = 'checking'
  let seeding = false
  // A1 pre-built sample: {available, loaded, dismissed, media_ids}. Drives the
  // "範例素材" chip (shown when loaded) so a seeded demo reads as demo + is removable.
  let sampleInfo = null
  let removingSample = false
  let selectedId = null
  let hoverId = null
  let activeFilter = 'all'
  let activeRating = null
  let view = 'grid'
  let query = ''
  let activeCamera = null
  // Shoot-year browse. Unlike the camera pool below, this is a SERVER-side filter
  // (?shot_year=): the sidebar counts come from the whole library, so filtering only
  // the loaded page would leave the row claiming "2025 · 54" beside a grid holding
  // however many of those happened to fall inside the first PAGE rows.
  let activeShotYear = null
  // Server-side for the same reason as the shoot filters: the pool is paginated.
  let poolSort = loadPoolSort()
  let activeShotDate = null // an ISO day inside activeShotYear; narrows it further
  let liveShotYears = null
  // #/main-live?ids=… is a narrowing with no sidebar row to represent it, so the
  // header has no other way to know the grid is a subset.
  let deepLinkSubset = false
  // Spread into every mediaParams() call, so the shoot filters (and the sort) cannot end up present
  // on one request path and missing on another — which is the whole failure this
  // feature's backend went out of its way to prevent.
  const shotArgs = () => ({ shotYear: activeShotYear, shotDate: activeShotDate, sort: poolSort })
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
  // date columns for list view (拍攝日 shot_date／匯入日 processed_at) — YYYY-MM-DD slice
  const fmtDate = (iso) => (iso ? String(iso).slice(0, 10) : '—')

  // backend rating value (good/ng/review/null) → UI value (good/ng/rev/none).
  const ratingToUi = (r) => (r === 'review' ? 'rev' : r || 'none')
  // API media item → MediaCard's expected shape.
  const IMG_EXTS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg']);
  const toCard = (it) => {
    const ext = (it.ext || '').toLowerCase();
    const kind = IMG_EXTS.has(ext)
      ? 'image'
      : (it.has_audio && (it.width === 0 || !it.width) ? 'audio' : 'video');
    return {
      id: it.id,
      name: it.filename || `#${it.id}`,
      kind,
      rating: ratingToUi(it.rating),
      dur: fmtDur(it.duration_s),
      size: fmtSize(it.size_mb),
      // 拍攝日，不是入庫日。null 就讓它是 null —— 卡片上不顯示，好過顯示
      // 一個沒人驗證過的日期。/api/media 一直有回這個欄位，只是沒人用。
      shot: it.shot_date || null,
      thumb: api.thumbUrlFromPath(it.thumbnail_path),
      _raw: it,
    };
  };

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

  // Add the multi-selection to a cross-library 精選集 (bin). A bin item is keyed by
  // (registry project name, media_id); the current project's REGISTRY name comes
  // from stats.project_registered_name — null when this library isn't registered,
  // in which case a bin item couldn't be resolved back, so the add is disabled.
  let binList = []
  let targetBinId = ''
  let newBinName = ''
  $: projectRegName = stats && stats.project_registered_name
  async function loadBins() {
    try { binList = (await api.getBins()).bins || [] } catch (e) { /* none yet */ }
  }
  async function addPicksToBin() {
    if (!projectRegName) { pushToast('本專案未登記，無法加入精選集 — 先到設定登記', 'error'); return }
    const byId = new Map((items || []).map((m) => [m.id, m]))
    const refs = picked.map((id) => ({
      project_name: projectRegName,
      media_id: String(id),
      // 名字拿不到就送空字串，不要送 `#id`：它永遠不會等於真檔名，
      // 會讓後端的 id-reuse 比對每一支都誤判。顯示端本來就有 #id fallback。
      filename: (byId.get(id) || {}).name || '',
    }))
    let binId = targetBinId
    try {
      if (!binId && newBinName.trim()) {
        const b = await api.createBin(newBinName.trim())
        binId = b.id; newBinName = ''; await loadBins(); targetBinId = binId
      }
      if (!binId) { pushToast('先選一個精選集或輸入新名稱', 'error'); return }
      const r = await api.addBinItems(binId, refs)
      const name = (binList.find((b) => b.id === binId) || {}).name || '精選集'
      pushToast(`已加入「${name}」（共 ${r.item_count} 支）`)
      clearPicks(); loadBins()
    } catch (e) { pushToast('加入精選集失敗: ' + e.message, 'error') }
  }
  const EXPORT_FMTS = ['edl', 'fcpxml', 'srt']
  // One export in flight at a time; exportBusy = the running key (e.g.
  // 'timeline:edl') so the clicked button shows … and the rest disable (B11).
  let exportBusy = null
  const exportGuard = createExportGuard((b) => (exportBusy = b))
  function exportTimeline(fmt) {
    if (!picked.length) return
    const n = picked.length
    return exportGuard.run(`timeline:${fmt}`, async () => {
      try {
        const r = await api.downloadFile(api.exportTimelinePath(picked, fmt), `arkiv-timeline.${fmt}`)
        pushToast(downloadedMsg(r.filename, r.size, `時間軸 ${n} 支`))
      } catch (e) {
        pushToast(`時間軸 ${fmt.toUpperCase()} 匯出失敗: ${e.message}`, 'error')
      }
    })
  }
  // DaVinci Resolve metadata CSV (auth-safe download). ids=null → whole library;
  // ids=picked → just the selected clips (camera-report / DIT handoff deliverable).
  function exportMetadataCsv(ids = null) {
    return exportGuard.run(ids ? 'csv:picked' : 'csv:all', async () => {
      try {
        const r = await api.downloadFile(api.metadataCsvPath(ids), 'arkiv_davinci_metadata.csv')
        pushToast(downloadedMsg(r.filename, r.size, ids ? `中繼資料 ${ids.length} 支` : '中繼資料 · 全庫'))
      } catch (e) {
        pushToast(`CSV 匯出失敗: ${e.message}`, 'error')
      }
    })
  }
  // ---- media delete (reversible: original moved to trash, metadata removed) ----
  // confirmDel holds the pending delete so the ConfirmDialog can show context and
  // trigger it; null = no dialog open. Both single (inspector) and bulk (picked)
  // funnel through here — the backend moves originals to trash, so this is safe.
  let confirmDel = null
  let delBusy = false
  let trashOpen = false
  function askDeleteSingle() {
    if (!selected) return
    confirmDel = { kind: 'single', ids: [selected.id], label: selected.name || `#${selected.id}` }
  }
  function askDeleteBulk() {
    if (!picked.length) return
    confirmDel = { kind: 'bulk', ids: [...picked], label: `${picked.length} 支素材` }
  }
  async function confirmDelete() {
    if (!confirmDel) return
    const ids = confirmDel.ids
    delBusy = true
    try {
      // describeDelete reads what the backend actually returns (file_deleted /
      // warning / not_trashed) — a metadata-only delete must not be announced as
      // 「已移至回收桶」 (audit 2026-10-09 K1).
      const r = confirmDel.kind === 'single'
        ? await api.deleteMedia(ids[0], true)
        : await api.bulkDeleteMedia(ids, true)
      const out = describeDelete(confirmDel.kind, r, confirmDel.label, ids)
      pushToast(out.msg, out.kind)
      // remove only the rows that are actually gone; a hard per-item error keeps its card
      const gone = new Set(out.removeIds)
      items = items.filter((m) => !gone.has(m.id))
      if (confirmDel.kind === 'single' && selectedId === ids[0]) selectedId = null
      clearPicks()
      confirmDel = null
    } catch (e) {
      pushToast(`刪除失敗: ${e.message}`, 'error')
      confirmDel = null
    } finally {
      delBusy = false
    }
  }

  // single-clip export from the inspector (auth-safe download).
  function exportClip(id, fmt, name, trim = null) {
    const stem = (name || `media_${id}`).replace(/\.[^.]+$/, '')
    // trim = {in_s, out_s?} from the inspector IN/OUT points → backend trims the export
    // trim.markers = 'transcript' → FCPXML/EDL markers from the transcript (8.2i)
    let path = api.exportPath(id, fmt)
    const q = []
    if (trim && trim.in_s != null) q.push(`in_s=${trim.in_s}`)
    if (trim && trim.out_s != null) q.push(`out_s=${trim.out_s}`)
    if (trim && trim.markers) q.push(`markers=${encodeURIComponent(trim.markers)}`)
    if (q.length) path += `?${q.join('&')}`
    const transcriptMarkers = !!(trim && trim.markers === 'transcript')
    const file = `${stem}${transcriptMarkers ? '.transcript' : ''}.${fmt}`
    return exportGuard.run(`clip:${fmt}${transcriptMarkers ? '.transcript' : ''}`, async () => {
      try {
        const r = await api.downloadFile(path, file)
        pushToast(downloadedMsg(r.filename, r.size))
      } catch (e) {
        pushToast(`${fmt.toUpperCase()} 匯出失敗: ${e.message}`, 'error')
      }
    })
  }
  const _stem = (id, name) => (name || `media_${id}`).replace(/\.[^.]+$/, '')
  function exportChapters(id, name, format = 'youtube') {
    return exportGuard.run('chapters', async () => {
      try {
        const r = await api.getChapters(id, format)
        const d = api.downloadText(r.chapters || '', `${_stem(id, name)}.chapters.txt`)
        pushToast(downloadedMsg(d.filename, d.size, '章節'))
      } catch (e) { pushToast(`章節匯出失敗: ${e.message}`, 'error') }
    })
  }
  function exportRemotion(id, name, trim = null) {
    return exportGuard.run('remotion', async () => {
      try {
        const r = await api.getRemotionProps(id, undefined, trim)
        const d = api.downloadText(JSON.stringify(r, null, 2), `${_stem(id, name)}.remotion.json`, 'application/json')
        pushToast(downloadedMsg(d.filename, d.size, 'Remotion props'))
      } catch (e) { pushToast(`Remotion 匯出失敗: ${e.message}`, 'error') }
    })
  }
  async function revealFile(path, mediaId = null) {
    try { await api.openFile(path, true, undefined, mediaId) } catch (e) { pushToast(`在 Finder 顯示失敗: ${e.message}`, 'error') }
  }

  // Poll /api/health, distinguishing deps-missing (503, immediate) from a still-
  // starting backend (fetch rejects → brief debounce before calling it unreachable).
  async function checkReady() {
    for (let i = 0; i < 3; i++) {
      health = await api.getHealth().catch(() => null)
      if (health) return health.ready ? 'ready' : 'notready' // got 200 or 503
      await new Promise((r) => setTimeout(r, 1000 * (i + 1)))
    }
    return 'unreachable'
  }

  async function onSeed() {
    if (seeding) return
    seeding = true
    try {
      // Prefer the instant pre-built load (zero re-ingest). Fall back to the v1
      // on-demand seed only when there's no artifact (404) or the project isn't
      // fresh (409) — there a merge isn't safe, so re-ingest adds fresh rows.
      if (sampleInfo?.available) {
        try { await api.loadSample(); await load(); return }
        catch (e) { if (e.status !== 404 && e.status !== 409) throw e }
      }
      const r = await api.seedSample()
      if (r.queued === 0) { await load(); return } // already loaded
      await pollSeed()
    } catch (e) {
      pushToast(e.status === 409 ? '有匯入任務進行中，稍後再試' : '載入失敗: ' + e.message, 'error')
    } finally { seeding = false }
  }

  async function removeSampleAction() {
    if (removingSample) return
    removingSample = true
    try {
      await api.removeSample()
      pushToast('已移除範例素材')
      await load()
    } catch (e) {
      pushToast('移除失敗: ' + e.message, 'error')
    } finally { removingSample = false }
  }

  async function pollSeed() {
    const t0 = Date.now()
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500))
      let s = null
      try { s = await api.getSeedStatus() } catch { /* transient */ }
      if (s && !s.running) {
        if (s.ok) { pushToast('sample library ready — 試搜 "llama"'); await load() }
        else { pushToast('載入失敗 — 見 Settings → System', 'error') }
        return
      }
      if (Date.now() - t0 > 20 * 60 * 1000) { pushToast('sample 載入逾時', 'error'); return }
    }
  }

  // Shared by load() and loadIds() so the facet can't fail loudly on one path and
  // silently on the other. Hiding a browse entry point without a word is the B11
  // silent-failure shape — and the grid read the same DB successfully a moment
  // earlier, so a failure here is specific enough to be worth saying out loud.
  function applyShotYearFacets(f) {
    if (f && f._error) {
      liveShotYears = null
      pushToast(`拍攝年份讀取失敗: ${f._error}`, 'error')
      return
    }
    liveShotYears = shotYearRows(f)
  }

  async function load() {
    // load() means "show the whole library again", so no collection is active.
    // Clearing here rather than in each caller keeps the highlight honest on
    // every path back out (search cleared, pool picked, initial mount).
    activeCollection = null
    const gen = viewGen.begin()
    state = 'loading'
    readyState = 'checking'
    const ready = await checkReady()
    // Guard every await, not only the last one: a readyState write from a superseded
    // load is as capable of contradicting the view on screen as an items write.
    if (!viewGen.isCurrent(gen)) return
    readyState = ready
    if (readyState === 'unreachable') { state = 'ok'; return } // panel renders via readyState
    try {
      const [s, m, t, c, si, f, pr, ent] = await Promise.all([
        api.getStats(),
        api.getMedia(mediaParams({ limit: PAGE, ...shotArgs() })),
        api.getTags(), api.getCollections(),
        api.sampleStatus().catch(() => null), // non-fatal: chip just stays hidden
        api.getShootDateFacets().catch((e) => ({ _error: e.message })),
        // Both non-fatal on purpose: the sidebar falls back to naming just the
        // open library, which is strictly what it showed before this existed.
        api.getProjects().catch(() => null),
        api.getEntitlements().catch(() => null),
      ])
      viewGen.apply(gen, () => {
        stats = s
        sampleInfo = si
        maybeOpenWizard(s, si)
        registryProjects = pr?.projects ?? null
        entitlement = ent
        deepLinkSubset = false
        applyShotYearFacets(f)
        items = (m.items || []).map(toCard)
        total = m.total ?? items.length
        moreParams = mediaParams({ limit: PAGE, ...shotArgs() })
        liveTags = (t || []).map((x) => ({ name: x.name, count: x.count }))
        liveCollections = (c?.collections || []).map((col) => ({
          key: col.key, title: col.title, count: col.count,
          items: col.items || [], // full member items (id/filename/thumb/duration_s/score)
        }))
        if (items.length && selectedId == null) selectedId = items[0].id
        state = 'ok'
      })
    } catch (e) {
      // The error banner is a claim about the view on screen, so a request nobody is
      // waiting for must not raise one.
      viewGen.apply(gen, () => {
        state = 'error'
        err = e.message + (e.body ? ' · ' + JSON.stringify(e.body) : '')
      })
    }
  }

  // R5-20 (#37): fetch the next offset page for the current view and append.
  // `moreParams` carries the same filters as the originating request; the id
  // dedupe guard keeps a concurrent ingest that shifts the window from
  // double-inserting a row. Silent failure was the B11 anti-pattern — surface it.
  async function loadMore() {
    if (!moreParams || loadingMore || items.length >= total) return
    // Paging EXTENDS the current view rather than replacing it, so it reads the
    // generation instead of claiming a new one — but it still has to check on the way
    // back, or a page of the old view gets stapled onto whatever replaced it.
    const gen = viewGen.current
    loadingMore = true
    try {
      const r = await api.getMedia({ ...moreParams, offset: items.length })
      viewGen.apply(gen, () => {
        const seen = new Set(items.map((x) => x.id))
        const more = (r.items || []).map(toCard).filter((x) => !seen.has(x.id))
        items = [...items, ...more]
        total = r.total ?? total
      })
    } catch (e) {
      if (viewGen.isCurrent(gen)) pushToast(`載入更多失敗: ${e.message}`, 'error')
    } finally {
      // Outside the guard on purpose: the spinner belongs to this component, not to
      // the view, and leaving it stuck true would disable the button for good.
      loadingMore = false
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
  // 拍攝日 quick-browse. Server-side, so it re-issues the request rather than
  // filtering what is already on screen — and it re-issues through whichever path
  // is current, keeping the date and a live search stacked instead of making the
  // user choose between them (the backend wired these into the search branches for
  // exactly this reason).
  function reloadForShotFilter() {
    if (query.trim()) runSearch()
    else load() // also clears any active collection, whose members carry no shoot date
  }
  function sortByColumn(col) {
    const next = nextColumnSort(col, poolSort)
    if (next === poolSort) return
    poolSort = next
    onPoolSortChange()
  }
  function onPoolSortChange() {
    savePoolSort(poolSort)
    // A collection / ?ids= subset is served whole in its own order; the choice is
    // kept and applies once the user is back on the library.
    if (activeCollection || deepLinkSubset) return
    reloadForShotFilter()
  }
  function onShotYearClick(year) {
    activeShotYear = activeShotYear === year ? null : year
    // Changing or closing the year always drops the day. The day rows belong to the
    // year that is open; keeping one selected after the year moved would leave the
    // grid filtered to a date that is no longer visible anywhere in the sidebar.
    activeShotDate = null
    reloadForShotFilter()
  }
  function onShotDateClick(date) {
    activeShotDate = activeShotDate === date ? null : date
    reloadForShotFilter()
  }
  // Smart Pools → the rating dimension (shared with the toolbar FilterRow).
  // 'Unrated' maps to rating 'none' (ratingToUi(null)), which the visible
  // filter already handles. Clicking the active pool toggles back to all.
  function onPoolClick(label) {
    activeCamera = null
    if (label === 'All media') {
      activeRating = null
      activeFilter = 'all'
      // "All media" has to actually leave a collection. Clearing only the rating
      // filters left the grid showing the collection subset with its sidebar row
      // still highlighted, while the user had just asked for the whole library.
      // Same argument for the shoot year: it is a server-side subset, so leaving it
      // on would hand back one year while the pool row claims the whole library.
      const hadShotFilter = activeShotYear !== null || activeShotDate !== null
      activeShotYear = null
      activeShotDate = null
      if (activeCollection || query || hadShotFilter) { query = ''; load() }
      return
    }
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
    // Clicking the active collection toggles back to the full library, matching
    // onCameraClick / onPoolClick. Without this there is no way out of a
    // collection except picking a different filter — and with the sidebar row now
    // highlighted, a user reasonably expects clicking it again to undo it.
    if (activeCollection === col.key) {
      activeCollection = null
      query = ''
      load()
      return
    }
    query = ''
    activeCamera = null
    // A collection's member list is served whole, with no shoot-date path behind it —
    // a year or day left highlighted here would filter nothing while claiming to.
    activeShotYear = null
    activeShotDate = null
    deepLinkSubset = false // the collection is now what makes this a subset
    // This writer is synchronous — the members are already in hand from
    // /api/collections — so without claiming a generation it would be the one path a
    // slow in-flight search could still overwrite a moment later.
    viewGen.begin()
    activeCollection = col.key
    items = (col.items || []).map((it) => ({
      id: it.id,
      name: it.filename || `#${it.id}`,
      kind: 'video',
      // Through the same converter the normal grid uses. This used to be a
      // hardcoded 'none', which made every rated clip look unrated the moment you
      // opened a collection — data appearing to vanish, on what this release turns
      // into a primary browse path.
      rating: ratingToUi(it.rating),
      dur: fmtDurS(it.duration_s),
      size: '—',
      thumb: it.thumb || null, // already a root-relative /thumbnails/<name> path
      _raw: it,
    }))
    // Collections arrive fully classified server-side (no cap) → nothing more to page.
    total = items.length
    moreParams = null
    selectedId = items.length ? items[0].id : null
    // This path has its data in hand, so it owns `state` too. Previously it inherited
    // whatever the in-flight request had left behind, which was invisible only because
    // that request would later set 'ok' itself — now that a superseded response is
    // correctly suppressed, not claiming 'ok' here leaves the grid stuck on "loading…".
    state = 'ok'
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
  // The sidebar's Projects list is the REGISTRY (~/.arkiv-projects.json), not the
  // open library. It used to be a single hardcoded row naming whatever library was
  // open, which meant a project you added in Settings never appeared here and the
  // header read "Projects · 1" no matter how many you had.
  //
  // The open library still gets a row, because it is what every other number on
  // this page describes — but it is only synthesised when no registry entry claims
  // to be it. `is_current` comes from the backend: it holds `config.PROJECT_ROOT`
  // and the frontend does not, so the frontend must not try to guess the match.
  //
  // Counts: only the open library has one here. A registry entry's item count would
  // need a query per project, and a wrong number is worse than no number.
  $: liveProjects = libraryRows({ registryProjects, stats, projectName, libState })

  // #561 — desktop only. Switching restarts the backend on another library and the
  // shell reloads this window onto it, so success never returns here.
  async function onProjectClick(p) {
    if (switching || p.active || !p.path || !canSwitchLibrary()) return
    try {
      const msg = busyMessage(await api.libraryBusy())
      if (msg) return pushToast(msg, 'error')
    } catch { /* the shell checks again before it stops anything */ }
    switching = true
    pushToast(`切換到「${p.name}」…（後端重新啟動，約 10–30 秒）`)
    try {
      await switchLibrary(p.path)
    } catch (e) {
      pushToast(`沒有切換：${e?.message || e}`, 'error')
    } finally {
      switching = false
    }
  }

  // ＋ 新增素材庫: pick a folder → register it (free-tier cap applies, 403 shows why)
  // → switch to it. The backend creates `.arkiv/` there on first start.
  async function onAddLibrary() {
    if (switching) return
    const path = await pickFolder()
    if (!path) return
    const names = (registryProjects || []).map((r) => r.name)
    const name = uniqueName(baseName(path), names)
    try {
      await api.addProject({ name, path })
    } catch (e) {
      // Already registered → it is a library the user owns; just open it.
      if (!/already registered/i.test(String(e?.body?.detail || e?.message || ''))) {
        return pushToast(addErrorMessage(e, name), 'error')
      }
    }
    await onProjectClick({ name, path, active: false })
  }
  // real disk usage for the sidebar Storage footer (replaces the mock placeholder)
  $: liveStorage = stats?.disk ?? null
  // The header used to always read stats.total, so picking "2025 · 54" left the only
  // count on screen saying 62 while the sidebar and the grid both said 54 — and the
  // "顯示 N / total" line below is hidden once everything is loaded, so there was
  // nothing else to correct it.
  //
  // It reports `visible.length` — literally the cards being rendered — rather than the
  // server-side `total`. Those differ whenever a client-side filter is also on: pick
  // 2025 (54 rows server-side), then Rated good, and the grid shows 10 while `total`
  // is still 54. Counting what is drawn cannot disagree with what is drawn, and it
  // sidesteps the fact that `total` is itself only a bounded match count on the search
  // path (audit M19). How many rows are still unfetched is a different question, and
  // the 顯示 N / total line below already answers it.
  //
  // Gated on an explicit "is anything narrowing this" rather than on the counts
  // differing: in a library larger than one page they differ with no filter at all,
  // and reading 篩選中 then would be a new lie in place of the old one.
  $: isFiltered = !!(
    query.trim() || activeShotYear || activeShotDate || activeCollection ||
    activeRating || activeCamera || activeFilter !== 'all' || deepLinkSubset
  )
  $: subsetTotal = stats && state === 'ok' && isFiltered ? visible.length : null
  // tag click → search that tag
  function onTagClick(name) {
    query = name
    runSearch()
  }

  async function runSearch() {
    if (!query.trim()) return load()
    activeCamera = null
    // Leaving the collection view: clear the highlight too, or the sidebar keeps
    // claiming a collection is active while the grid shows search results.
    activeCollection = null
    deepLinkSubset = false // searching replaces a ?ids= subset with a real query
    const gen = viewGen.begin()
    state = 'loading'
    try {
      // Same-DB search via /api/media?q= → {items, total, search:true}.
      // NOT /api/search/all — that's cross-project federation over the
      // ~/.arkiv-projects.json registry, which is empty here → 0 results.
      const r = await api.getMedia(mediaParams({ limit: PAGE, query, ...shotArgs() }))
      // Also fixes two searches in a row resolving out of order: the slower first
      // query used to repaint the grid while the box already read the second one.
      viewGen.apply(gen, () => {
        items = (r.items || []).map(toCard)
        total = r.total ?? items.length
        moreParams = mediaParams({ limit: PAGE, query, ...shotArgs() })
        selectedId = items.length ? items[0].id : null
        state = 'ok'
      })
    } catch (e) {
      viewGen.apply(gen, () => {
        state = 'error'
        err = e.message
      })
    }
  }

  // Deep-link from the ranked search screen: #/main-live?sel=<id> selects that
  // clip. If it's outside the first page, fetch it by id and prepend so the grid
  // + inspector show it instead of silently falling back to the default (Codex
  // review P2).
  function readHashParam(name) {
    const h = window.location.hash
    const qi = h.indexOf('?')
    if (qi === -1) return null
    return new URLSearchParams(h.slice(qi + 1)).get(name)
  }
  function readSelParam() {
    const v = readHashParam('sel')
    const id = v == null ? null : Number(v)
    return Number.isFinite(id) ? id : null
  }
  // Chat deep-link: #/main-live?ids=2,5,9 shows exactly that relevant subset
  // (not the full library) via the backend's ?ids= filter.
  async function loadIds(ids) {
    const gen = viewGen.begin()
    state = 'loading'
    try {
      // Load the sidebar data (stats/tags/collections) too — otherwise the
      // deep-link path leaves stats=null and the whole sidebar (projects + pools)
      // silently falls back to mock data. Grid items come from the ?ids= subset.
      const [s, m, t, c, f] = await Promise.all([
        api.getStats(), api.getMedia({ ids, limit: PAGE }), api.getTags(), api.getCollections(),
        // The year section is sidebar data like tags and collections, so it belongs
        // on this path too — otherwise arriving from chat silently loses the time
        // entry point until the user navigates back out.
        api.getShootDateFacets().catch((e) => ({ _error: e.message })),
      ])
      viewGen.apply(gen, () => {
        stats = s
        applyShotYearFacets(f)
        deepLinkSubset = true
        items = (m.items || []).map(toCard)
        // A deep-link ?ids= subset is exactly the returned rows — no further pages.
        total = items.length
        moreParams = null
        liveTags = (t || []).map((x) => ({ name: x.name, count: x.count }))
        liveCollections = (c?.collections || []).map((col) => ({
          key: col.key, title: col.title, count: col.count, items: col.items || [],
        }))
        selectedId = items.length ? items[0].id : null
        state = 'ok'
      })
    } catch (e) {
      viewGen.apply(gen, () => {
        state = 'error'
        err = e.message
      })
    }
  }
  // #/main-live?sel=<id>&t=<sec>: a search hit's matched second. Handed to the
  // Inspector, which seeks once the player has metadata.
  let startAt = null
  async function selectFromParam() {
    const sel = readSelParam()
    if (sel == null) return
    const t = parseStartParam(readHashParam('t'))
    startAt = t == null ? null : { id: sel, t }
    if (items.find((m) => m.id === sel)) { selectedId = sel; return }
    // Prepending into whatever view is current: if the user moved on while the detail
    // fetch was in flight, this would splice an unrelated clip into the new grid.
    const gen = viewGen.current
    try {
      const d = await api.getMediaDetail(sel)
      if (d && d.id != null) {
        viewGen.apply(gen, () => { items = [toCard(d), ...items]; selectedId = sel })
      }
    } catch (e) { /* unknown id → keep default selection */ }
  }
  // Engine languages for the retranscribe picker — non-fatal (mock fallback in UI).
  async function loadEngines() {
    try {
      const e = await api.getIngestEngines()
      engineLangs = e?.languages || null
    } catch (e) { /* picker falls back to its built-in zh/en list */ }
  }
  // log → Rec.709 preview profiles for the inspector picker (best-effort: an older
  // backend without the route just hides the picker's options).
  let colorProfiles = []
  async function loadColorProfiles() {
    try { colorProfiles = (await api.getColorProfiles()).profiles || [] } catch { colorProfiles = [] }
  }
  // The poster is rebuilt in place under the same filename, so the inspector's
  // <img> needs a cache-bust after a profile change.
  let thumbBust = {}
  async function setColorProfileLive(pid) {
    if (!selected) return
    const id = selected.id
    await api.setColorProfile(id, pid)
    if (detail && detail.id === id) detail = { ...detail, color_profile: pid }
    thumbBust = { ...thumbBust, [id]: Date.now() }
  }
  async function importLutLive(file) {
    const r = await api.importLut(file)
    await loadColorProfiles()
    pushToast(`已匯入 LUT：${file.name}`)
    return r.id
  }
  onMount(async () => {
    loadColorProfiles()
    // Deep-link from chat: #/main-live?ids=<csv>&sel=<id> filters the grid to that
    // relevant subset (not the full library) before selecting the clicked clip.
    const ids = readHashParam('ids')
    if (ids && ids.trim()) {
      await loadIds(ids)
    } else {
      await load()
    }
    await selectFromParam()
    loadEngines()
    loadBins()
  })

  $: visible = items.filter((m) => {
    if (activeFilter === 'video' && m.kind !== 'video') return false
    if (activeFilter === 'audio' && m.kind !== 'audio') return false
    if (activeFilter === 'image' && m.kind !== 'image') return false
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
        // Precise, unrounded fps for frame-exact IN/OUT. Rounding 23.976→24
        // would drift frame boundaries over a long clip, so keep the raw value
        // separate from the display `fps` above. null → frame features off.
        fpsExact: selected._raw.fps || null,
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
    } catch (e) { pushToast(`切換主要語言失敗: ${e.message}`, 'error') }  // round-5 #43
  }

  // 選字 → 校正: teach the project dictionary one correction (POST
  // /api/corrections/learn) and, when asked, rewrite every stored transcript with
  // it; then reload THIS clip so the inspector shows the corrected text. Throws on
  // failure so the inspector keeps its form open with the error.
  async function correctTranscript(from, to, apply) {
    const id = selected && selected.id
    let res
    try {
      res = await api.learnCorrection({ from, to }, apply)
    } catch (e) {
      pushToast(`校正失敗: ${e.message}`, 'error')
      throw e
    }
    pushToast(describeLearn(res, apply), 'ok', 6000)
    if (id != null && apply) await Promise.all([fetchDetail(id), fetchTranscripts(id)])
  }
  // Only the ACTIVE language is correctable: recorrect rewrites media.* (the
  // active transcript); archived languages would silently not change.
  $: canCorrect = !!selected && (!tdLive || !viewLang || !!(_viewedRow && _viewedRow.active))

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
  $: _viewedRow = (tdLive && viewLang)
    ? (tdLive.transcripts.find((t) => t.lang === viewLang) || null)
    : null
  $: _displaySegs = (tdLive && viewLang)
    ? (parseJson((_viewedRow || {}).segments_json) || [])
    : (detailLive ? (parseJson(detailLive.segments_json) || []) : null)
  // 語氣詞 count/density for the viewed language. Computed server-side so the
  // marker set has exactly one definition; absent (null) when the server declines
  // to answer — a non-CJK transcript would score a structural 0, which reads as a
  // measurement and is not one.
  $: particleStat = _viewedRow && _viewedRow.particle_count != null
    ? { count: _viewedRow.particle_count, density: _viewedRow.particle_density }
    : null
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
  $: inspThumb = selected
    ? (selected.thumb && thumbBust[selected.id]
        ? `${selected.thumb}${selected.thumb.includes('?') ? '&' : '?'}v=${thumbBust[selected.id]}`
        : selected.thumb)
    : null
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
      pushToast(`加標籤失敗: ${e.message}`, 'error')  // round-5 #43
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
      pushToast(`刪標籤失敗: ${e.message}`, 'error')  // round-5 #43 — was a silent revert
    }
  }

  // A6 person annotations. Fetched per clip alongside the detail; the names list
  // (autocomplete) is library-wide and refreshed after every successful add.
  let identities = null
  let identitiesId = null
  let identityNames = []
  async function fetchIdentities(id) {
    identitiesId = id
    identities = null
    try {
      const r = await api.getIdentities(id)
      if (identitiesId === id) identities = r.identities || []
    } catch (e) {
      if (identitiesId === id) identities = null
    }
  }
  // Fetched ONCE per page (then again after each add/remove) — see lib/identityNames.js
  // for why "while the list is empty" looped (#572). The reactive line below depends
  // only on `selected`, so assigning identityNames cannot re-run it.
  const identityNamesLoader = createIdentityNamesLoader(
    () => api.getIdentityNames(),
    (names) => { identityNames = names },
  )
  const fetchIdentityNames = identityNamesLoader.refresh
  $: if (selected && selected.id !== identitiesId) fetchIdentities(selected.id)
  $: if (selected) identityNamesLoader.ensure()
  async function addIdentity(name, range) {
    if (!selected) return false
    const id = selected.id
    try {
      const r = await api.addIdentity(id, { name, ...(range || {}) })
      if (identitiesId === id) identities = r.identities
      pushToast(`已標註：${r.identity.name}`)
      fetchIdentityNames()
      return true
    } catch (e) {
      pushToast(`標人物失敗: ${e.message}`, 'error')
      return false
    }
  }
  async function removeIdentity(identityId) {
    if (!selected) return
    const id = selected.id
    try {
      const r = await api.removeIdentity(id, identityId)
      if (identitiesId === id) identities = r.identities
      pushToast('已移除人物標註')
      fetchIdentityNames()
    } catch (e) {
      pushToast(`移除人物失敗: ${e.message}`, 'error')
    }
  }

  // Live progress label for the running per-clip action ('12/40'), '' when idle.
  let reProgress = ''

  // Stage labels, not a progress bar: whisper decoding is a single opaque call and
  // a percentage would be invented. Naming the stage is the honest version.
  const TRANSCRIBE_STAGES = {
    queued: '排隊中', extracting: '抽音軌', vad: '偵測語音',
    decoding: '辨識中', polishing: '潤稿中', diarizing: '分辨語者',
  }

  // Poll `endpoint` every second and show `labels[stage]`. Shared by the two
  // per-clip jobs that report stages rather than counts.
  function pollStage(id, endpoint, labels) {
    let alive = true
    let timer = null
    const tick = async () => {
      if (!alive) return
      try {
        const s = await endpoint(id)
        if (alive && s && s.state === 'running' && labels[s.stage]) reProgress = labels[s.stage]
      } catch (_) {}
      if (alive) timer = setTimeout(tick, 1000)
    }
    timer = setTimeout(tick, 1000)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
      reProgress = ''
    }
  }

  // Returns a stop function. A failed poll is ignored on purpose: the job is the
  // POST, not this, and a status blip must never turn a working run into an error.
  function pollVisionProgress(id) {
    let alive = true
    let timer = null
    const tick = async () => {
      if (!alive) return
      try {
        const s = await api.retryVisionStatus(id)
        if (alive && s && s.state === 'running' && s.total) {
          const phase = s.phase === 'fallback' ? '備援 ' : ''
          reProgress = `${phase}${s.done || 0}/${s.total}`
        }
      } catch (_) {}
      if (alive) timer = setTimeout(tick, 1000)
    }
    timer = setTimeout(tick, 1000)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
      reProgress = ''
    }
  }

  // Per-clip re-processing. Returns {ok, message} for the inspector to surface;
  // throws bubble up to the inspector's catch. Refetch detail on success so the
  // transcript / vision / tags reflect the new run.
  async function reprocess(action, opts = {}) {
    if (!selected) return { ok: false, message: '未選取素材' }
    const id = selected.id
    if (action === 'retranscribe') {
      const stopPolling = pollStage(id, api.retranscribeStatus, TRANSCRIBE_STAGES)
      let r
      try {
        r = await api.retranscribe(id, opts.language || 'zh')
      } finally {
        stopPolling()
      }
      if (detailId === id) await fetchDetail(id)
      await fetchTranscripts(id)  // G2: surface the (possibly new) language as a tab
      return { ok: r.ok, message: `轉錄完成 · ${r.transcript_length} 字 · ${r.language}` }
    }
    if (action === 'retry-vision') {
      // Poll for progress while the POST is still open. The request contract is
      // untouched — this only makes the middle of a multi-minute call visible.
      const stopPolling = pollVisionProgress(id)
      let r
      try {
        r = await api.retryVision(id)
      } finally {
        stopPolling()
      }
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
    if (action === 'editor-proxy') {
      // ProRes into <source dir>/Proxy/ for the NLE. The POST only queues, so wait
      // for the batch here — the button stays busy and ends on a real answer
      // (written / already there / why it failed) instead of "queued".
      await api.buildEditorProxies([id])
      const deadline = Date.now() + 60 * 60 * 1000
      let s = null
      while (Date.now() < deadline) {
        await new Promise((res) => setTimeout(res, 1500))
        try {
          s = await api.editorProxyStatus()
        } catch (_) {
          continue
        }
        if (s && !s.running) break
        if (s && s.total) reProgress = `${s.done || 0}/${s.total}`
      }
      reProgress = ''
      const res = s && (s.results || []).find((x) => x.media_id === id)
      if (!s || s.running || !res) return { ok: false, message: '仍在背景生成中，稍後再看 Proxy/ 資料夾' }
      if (res.status === 'created') return { ok: true, message: `已寫入 ${res.path}` }
      if (res.status === 'exists') return { ok: true, message: `Proxy/ 已有同名檔，未覆蓋：${res.path}` }
      if (res.status === 'skipped') return { ok: false, message: '不是影片，略過' }
      return { ok: false, message: `失敗：${res.reason || '見後端 log'}` }
    }
    return { ok: false, message: `未知動作: ${action}` }
  }

  // C — rating write. UI value → backend value (db.set_rating: good/ng/review/None).
  const RATING_MAP = { good: 'good', rev: 'review', ng: 'ng', none: null }
  async function rate(uiRating) {
    if (!selected) return
    const backendVal = RATING_MAP[uiRating] ?? null
    const id = selected.id
    // Rating only — no note. The backend is PATCH (omitted = untouched), and the
    // UI has no note editor; sending `detailLive?.rating_note ?? null` wiped the
    // stored note whenever the detail had not loaded yet (audit 2026-10-09 K1).
    // optimistic: reflect immediately in grid + inspector (which both read item.rating)
    const prev = selected.rating
    items = items.map((m) => (m.id === id ? { ...m, rating: uiRating } : m))
    try {
      await api.setRating(id, backendVal)
    } catch (e) {
      // revert on failure
      items = items.map((m) => (m.id === id ? { ...m, rating: prev } : m))
      pushToast(`rating 寫入失敗: ${e.message}`, 'error')  // round-5 #43 — was a silent revert
    }
  }

  // Batch rating for the multi-select bar — same optimistic/revert shape as rate(),
  // but per clip: only the rows whose PATCH failed go back to their old rating.
  let bulkRateBusy = false
  async function rateBulk(uiRating) {
    if (!picked.length || bulkRateBusy) return
    const ids = [...picked]
    const want = new Set(ids)
    const prev = new Map(items.filter((m) => want.has(m.id)).map((m) => [m.id, m.rating]))
    items = items.map((m) => (want.has(m.id) ? { ...m, rating: uiRating } : m))
    bulkRateBusy = true
    try {
      const { failed } = await rateMany(ids, RATING_MAP[uiRating] ?? null, api.setRating)
      if (failed.length) {
        const bad = new Set(failed.map((f) => f.id))
        items = items.map((m) => (bad.has(m.id) && prev.has(m.id) ? { ...m, rating: prev.get(m.id) } : m))
        pushToast(`rating 寫入失敗 ${failed.length}/${ids.length} 支: ${failed[0].error}`, 'error')
      } else {
        pushToast(`已將 ${ids.length} 支設為 ${RATE_LABELS[uiRating]}`)
      }
    } finally {
      bulkRateBusy = false
    }
  }
  const RATE_LABELS = { good: 'GOOD', rev: 'REVIEW', ng: 'N·G', none: '未評分' }

  // Persist the inspector's IN/OUT marks (debounced upstream in the inspector).
  // Optimistically fold into the local detail so re-opening the clip restores the
  // range without a refetch; toast on write failure.
  async function saveInOut(id, inS, outS) {
    if (detail && detail.id === id) detail = { ...detail, in_point: inS ?? null, out_point: outS ?? null }
    try {
      await api.setInOut(id, inS ?? null, outS ?? null)
    } catch (e) {
      pushToast(`IN/OUT 寫入失敗: ${e.message}`, 'error')
    }
  }
</script>

<div class="artboard" data-theme={theme}>
  <TopBar />
  <div class="body">
    <PoolSidebar live {liveProjects} {entitlement} onProject={canSwitchLibrary() ? onProjectClick : null} onAddLibrary={canSwitchLibrary() ? onAddLibrary : null} {switching} {livePools} {liveTags} {liveCollections} {liveStorage} {liveCameras} {liveShotYears} onTag={onTagClick} onCollection={onCollectionClick} {activeCollection} onCamera={onCameraClick} {activeCamera} onShotYear={onShotYearClick} {activeShotYear} onShotDate={onShotDateClick} {activeShotDate} onPool={onPoolClick} {activePool} liveBins={binList} onBin={() => (window.location.hash = '#/bins')} />

    <main class="center">
      <div class="toolrow">
        <div class="tr-main">
          <div class="proj">
            <div class="ak-display projtitle">{projectName}</div>
            <Mono dim style="font-size:11px;margin-top:5px;letter-spacing:0.04em;white-space:nowrap;">
              {#if stats && subsetTotal !== null}{subsetTotal} / {stats.total} items · 篩選中{:else if stats}{stats.total} items · {Math.round(stats.total_duration_s || 0)}s · {Math.round(stats.total_size_mb || 0)} MB · live{:else}loading…{/if}
            </Mono>
          </div>
          <FilterRow bind:activeFilter bind:activeRating />
          <ViewToggle bind:view />
        </div>
        <div class="tr-sub">
          <input
            class="ak-input livesearch"
            placeholder="搜尋（語意 + 關鍵字）…"
            bind:value={query}
            on:keydown={(e) => e.key === 'Enter' && runSearch()}
          />
          <a
            class="ak-btn tbtn"
            href={query.trim() ? `#/search-live?q=${encodeURIComponent(query.trim())}` : '#/search-live'}
            title="排名檢視（score + 摘要）"
          >排名 →</a>
          <div class="tr-end">
          <!-- List view sorts from its column headers; the grid has none, so it keeps the menu. -->
          {#if view !== 'list'}
            <select class="ak-input poolsort" bind:value={poolSort} on:change={onPoolSortChange}
                    title="排序（搜尋結果依相關度；沒有拍攝日期的排最後）" aria-label="排序">
              {#each POOL_SORTS as [key, label]}<option value={key}>{label}</option>{/each}
            </select>
          {/if}
          <button
            class="ak-btn tbtn metacsv"
            on:click={() => exportMetadataCsv(null)}
            disabled={!!exportBusy}
            aria-busy={exportBusy === 'csv:all'}
            title="匯出整庫 metadata CSV（DaVinci Resolve）"
          >{exportBusy === 'csv:all' ? 'CSV …' : 'CSV ↓'}</button>
          <button class="ak-btn tbtn" on:click={() => (trashOpen = true)} title="回收桶（已刪除、可還原）">回收桶</button>
          </div>
        </div>
      </div>

      {#if picked.length}
        <div class="exportbar">
          <Mono style="font-size:11px;letter-spacing:0.04em;">已選 {picked.length} 支</Mono>
          <div class="expbtns">
            <span class="barlabel">評分</span>
            {#each ['good', 'rev', 'ng', 'none'] as r}
              <button class="ak-btn expbtn" on:click={() => rateBulk(r)} disabled={bulkRateBusy}
                      title={r === 'none' ? '清除所選的評分' : `所選全部設為 ${RATE_LABELS[r]}`}
              >{r === 'none' ? '—' : RATE_LABELS[r]}</button>
            {/each}
            <div class="barvrule"></div>
            <span class="barlabel">時間軸</span>
            {#each EXPORT_FMTS as fmt}
              <button class="ak-btn expbtn" on:click={() => exportTimeline(fmt)}
                      disabled={!!exportBusy} aria-busy={exportBusy === `timeline:${fmt}`}
              >{fmt.toUpperCase()}{exportBusy === `timeline:${fmt}` ? ' …' : ''}</button>
            {/each}
            <button class="ak-btn expbtn" on:click={() => exportMetadataCsv(picked)} title="匯出所選 metadata CSV"
                    disabled={!!exportBusy} aria-busy={exportBusy === 'csv:picked'}
            >CSV{exportBusy === 'csv:picked' ? ' …' : ''}</button>
            <div class="barvrule"></div>
            <span class="barlabel">精選集</span>
            <select class="ak-input binsel" bind:value={targetBinId} disabled={!projectRegName}
                    title={projectRegName ? '' : '本專案未登記，無法加入精選集'}>
              <option value="">選精選集…</option>
              {#each binList as b}<option value={b.id}>{b.name}（{b.item_count}）</option>{/each}
            </select>
            <input class="ak-input binnew" placeholder="新精選集…" bind:value={newBinName} disabled={!projectRegName}
                   on:keydown={(e) => e.key === 'Enter' && addPicksToBin()} />
            <button class="ak-btn expbtn" on:click={addPicksToBin} disabled={!projectRegName}
                    title={projectRegName ? '加入跨庫精選集' : '本專案未登記（設定→跨庫專案）'}>加入精選集</button>
            <a class="ak-btn expbtn" href="#/bins" title="檢視精選集">★</a>
            <div class="barvrule"></div>
            <button class="ak-btn expbtn danger" on:click={askDeleteBulk} title="刪除所選（移至回收桶，可還原）">刪除</button>
            <button class="ak-btn expbtn clear" on:click={clearPicks}>清除</button>
          </div>
        </div>
      {/if}

      <div class="gridwrap">
        {#if sampleInfo?.loaded && stats?.total}
          <div class="samplebar">
            <Mono dim>範例素材 · {sampleInfo.media_ids?.length || 4} 支 CC-BY 示範片 — 換成你自己的素材時可移除</Mono>
            <button class="ak-btn sm" on:click={removeSampleAction} disabled={removingSample}>
              {removingSample ? '移除中…' : '移除範例'}
            </button>
          </div>
        {/if}
        {#if state === 'loading'}
          <div class="msg"><Mono dim>loading…</Mono></div>
        {:else if readyState === 'unreachable'}
          <div class="msg readypanel">
            <Eyebrow>◇ BACKEND UNREACHABLE</Eyebrow>
            <Mono dim>後端沒有回應 — 伺服器還在啟動、或還沒開。</Mono>
            <div class="readyactions"><button class="ak-btn" on:click={load}>Retry</button></div>
          </div>
        {:else if readyState === 'notready' && !stats?.total}
          <div class="msg readypanel">
            <Eyebrow>◇ SETUP INCOMPLETE</Eyebrow>
            <Mono dim>arkiv 需要這些才能索引 / 搜尋：</Mono>
            <ul class="checklist">
              {#each Object.entries(health?.checks || {}) as [name, c]}
                {#if !c.ok}
                  <li><Mono>✗ {name}{c.detail ? ' — ' + c.detail : ''}</Mono></li>
                {/if}
              {/each}
            </ul>
            <div class="readyactions">
              <button class="ak-btn" on:click={load}>Recheck</button>
              <a class="ak-btn" href="#/settings">Settings</a>
            </div>
          </div>
        {:else if state === 'error'}
          <div class="msg"><Mono style="color:var(--cyan);">ERROR: {err}</Mono></div>
        {:else if readyState === 'ready' && stats?.total === 0}
          <div class="msg readypanel">
            <Eyebrow>Library is empty</Eyebrow>
            <Mono dim>打一句中文或英文，五年素材唰跳出 — 先載個範例庫試搜。</Mono>
            <div class="readyactions">
              <button class="ak-btn" on:click={onSeed} disabled={seeding}>{seeding ? 'Loading…' : 'Load sample library'}</button>
              <a class="ak-btn" href="#/ingest-setup">or ingest your footage</a>
            </div>
          </div>
        {:else if visible.length === 0}
          <div class="msg"><Eyebrow>No media</Eyebrow><Mono dim>清搜尋或篩選。</Mono></div>
        {:else if view === 'list'}
          <table class="medialist" class:picking={picked.length > 0}>
            <thead>
              <tr>
                <th class="c-chk"></th>
                <th class="c-thumb"></th>
                <th class="c-name"><button class="sorth" class:on={columnArrow('name', poolSort)} on:click={() => sortByColumn('name')} title="依檔名排序（再按一次反向）">檔名<span class="arrow">{columnArrow('name', poolSort)}</span></button></th>
                <th><button class="sorth" class:on={columnArrow('rating', poolSort)} on:click={() => sortByColumn('rating')} title="依評分排序（Good → Review → N·G → 未評）">評分<span class="arrow">{columnArrow('rating', poolSort)}</span></button></th>
                <th>解析度</th>
                <th><button class="sorth" class:on={columnArrow('duration', poolSort)} on:click={() => sortByColumn('duration')} title="依時長排序（再按一次反向）">時長<span class="arrow">{columnArrow('duration', poolSort)}</span></button></th>
                <th><button class="sorth" class:on={columnArrow('size', poolSort)} on:click={() => sortByColumn('size')} title="依容量排序（再按一次反向）">容量<span class="arrow">{columnArrow('size', poolSort)}</span></button></th>
                <th><button class="sorth" class:on={columnArrow('shot', poolSort)} on:click={() => sortByColumn('shot')} title="依拍攝日期排序（再按一次反向；沒有拍攝日期的排最後）">拍攝日期<span class="arrow">{columnArrow('shot', poolSort)}</span></button></th>
                <th><button class="sorth" class:on={columnArrow('date', poolSort)} on:click={() => sortByColumn('date')} title="依匯入日期排序（再按一次反向）">匯入日期<span class="arrow">{columnArrow('date', poolSort)}</span></button></th>
              </tr>
            </thead>
            <tbody>
              {#each visible as m (m.id)}
                <tr
                  class:sel={m.id === selectedId}
                  class:ng={m.rating === 'ng'}
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
                  <td class="mono">{m._raw?.width && m._raw?.height ? `${m._raw.width}×${m._raw.height}` : '—'}</td>
                  <td class="mono">{m.dur}</td>
                  <td class="mono">{m.size}</td>
                  <!-- 拍攝日讀不到就顯示「—」，不 fallback 到入庫日（理由見 toCard 的 shot） -->
                  <td class="mono">{fmtDate(m.shot)}</td>
                  <td class="mono">{fmtDate(m._raw?.processed_at)}</td>
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
                dragOut={canDragOut()}
                checked={pickedSet.has(m.id)}
                on:toggle={(e) => togglePick(e.detail)}
                on:click={() => (selectedId = m.id)}
                on:mouseenter={() => (hoverId = m.id)}
                on:mouseleave={() => (hoverId = null)}
              />
            {/each}
          </div>
        {/if}

        {#if state === 'ok' && moreParams && items.length < total}
          <div class="loadmore">
            <Mono dim>顯示 {items.length} / {total} 支</Mono>
            <button class="ak-btn" on:click={loadMore} disabled={loadingMore}>
              {loadingMore ? '載入中…' : `載入更多 (+${Math.min(PAGE, total - items.length)})`}
            </button>
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
        fps={inspectorMedia ? inspectorMedia.fpsExact : null}
        peaks={inspPeaks}
        pathLabel={inspPath}
        transcriptLines={inspTranscript}
        {startAt}
        {particleStat}
        {transcriptLangs}
        {viewLang}
        {onViewLang}
        {onActivateLang}
        onCorrect={canCorrect ? correctTranscript : null}
        frameDescriptions={inspFrames}
        frameScenes={inspScenes}
        tags={inspTags}
        canonicalTags={inspCanonTags}
        onAddTag={selected ? addTag : null}
        onRemoveTag={selected ? removeTag : null}
        {identities}
        {identityNames}
        onAddIdentity={selected ? addIdentity : null}
        onRemoveIdentity={selected ? removeIdentity : null}
        onReprocess={selected ? reprocess : null}
        colorProfile={detailLive ? detailLive.color_profile : null}
        {colorProfiles}
        onSetColorProfile={selected && colorProfiles.length ? setColorProfileLive : null}
        onImportLut={importLutLive}
        getCube={api.getColorCube}
        {reProgress}
        languages={engineLangs}
        mediaLang={detailLive ? detailLive.lang : null}
        onExport={selected ? (fmt, trim) => exportClip(selected.id, fmt, selected.name, trim) : null}
        onChapters={selected ? (fmt) => exportChapters(selected.id, selected.name, fmt) : null}
        onRemotion={selected ? (trim) => exportRemotion(selected.id, selected.name, trim) : null}
        {exportBusy}
        onReveal={inspPath ? () => revealFile(inspPath, selected && selected.id) : null}
        onDelete={selected ? askDeleteSingle : null}
        onRate={rate}
        inPoint={detailLive ? detailLive.in_point : null}
        outPoint={detailLive ? detailLive.out_point : null}
        onInOut={selected ? (inS, outS, id) => saveInOut(id ?? selected.id, inS, outS) : null}
      />
    {/if}
  </div>

  <ConfirmDialog
    open={!!confirmDel}
    title={confirmDel ? (confirmDel.kind === 'bulk' ? '刪除所選素材' : '刪除素材') : '確認'}
    message={confirmDel
      ? `確定要將「${confirmDel.label}」移至回收桶嗎？\n原始檔會保留 30 天，可從回收桶還原；\n但標籤、評分／備註、IN/OUT、手改逐字稿不會隨還原回來。`
      : ''}
    confirmLabel="移至回收桶"
    danger={true}
    busy={delBusy}
    on:confirm={confirmDelete}
    on:cancel={() => (confirmDel = null)}
  />

  <TrashModal open={trashOpen} on:close={() => (trashOpen = false)} />
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; position: relative; display: grid; grid-template-rows: 52px 1fr; background: var(--bg); color: var(--ink); overflow: hidden; margin: 0 auto; }
  .body { display: grid; grid-template-columns: 220px 1fr var(--inspector-w); min-height: 0; }
  .center { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  /* Row 1 is the design's tool row (01 / 01a): title + stats left, kind/rating
     filters + view toggle right. Row 2 holds what the design doesn't draw but the
     app needs — pool search, 排名, CSV, 回收桶 — as a quieter utility line. Both
     wrap on a narrow window instead of clipping (the artboard is overflow:hidden).
     Every control is --tb-h tall so they share a baseline. */
  .toolrow { --tb-h: 26px; display: flex; flex-direction: column; gap: 10px; padding: 14px 22px 10px; border-bottom: 1px solid var(--rule); }
  .tr-main, .tr-sub { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; }
  .tr-main { gap: 10px 14px; }
  /* Right-hand groups wrap as a unit and stay on the right edge. */
  .tr-end { display: flex; align-items: center; gap: 8px 10px; margin-left: auto; }
  /* flex-basis 0 + ellipsis: the title block gives up width first, so the filters
     and view toggle stay on the title's line in a 780px center column. */
  .proj { flex: 1 1 0; min-width: 120px; overflow: hidden; }
  .proj :global(*) { overflow: hidden; text-overflow: ellipsis; }
  .projtitle { font-size: 28px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  /* min-width so the search shrinks then WRAPS rather than collapsing the buttons
     off-screen. */
  .livesearch { flex: 1 1 220px; min-width: 160px; max-width: 420px; font-size: 12px; height: var(--tb-h); box-sizing: border-box; }
  .tbtn { font-size: 10px; height: var(--tb-h); box-sizing: border-box; padding: 0 12px; display: inline-flex; align-items: center; white-space: nowrap; text-decoration: none; }
  /* A <select> is not covered by input.ak-input, so WKWebView drew its own rounded
     control; square it off to match the buttons beside it. */
  .poolsort {
    appearance: none; -webkit-appearance: none; border-radius: 0;
    font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.04em; color: var(--ink-2);
    height: var(--tb-h); box-sizing: border-box; padding: 0 24px 0 9px; width: auto;
    border: 1px solid var(--rule); background-color: transparent; cursor: pointer;
    background-image: linear-gradient(45deg, transparent 50%, var(--ink-2) 50%), linear-gradient(135deg, var(--ink-2) 50%, transparent 50%);
    background-position: calc(100% - 12px) 50%, calc(100% - 8px) 50%;
    background-size: 4px 4px; background-repeat: no-repeat;
  }
  .expbtn.danger { color: #d9534f; border-color: #d9534f; }
  .expbtn.danger:hover { background: #d9534f; color: #fff; }
  .gridwrap { flex: 1; overflow: auto; position: relative; }
  /* A1: pre-built sample demo bar — flags the seeded clips as demo + one-click remove */
  .samplebar {
    position: sticky; top: 0; z-index: 3;
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
    padding: 8px 22px; border-bottom: 1px solid var(--rule); background: var(--surface-2);
  }
  .samplebar .ak-btn.sm { padding: 3px 10px; font-size: 11px; }
  /* G9: prototype card width = 198px (runtime-measured from the 1400px design canvas).
     repeat(4,1fr) ballooned cards on wide windows (310px @1920) and squeezed them on
     narrow (168px @1280). auto-fill holds the design rhythm and reflows column count;
     auto-fill (not auto-fit) so a sparse last row keeps card size instead of stretching. */
  .mediagrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(198px, 1fr)); gap: 1px; padding: 22px; background: var(--rule); }

  /* G1 list view — design 01a: 16:9 thumbs at 110px, mono throughout, values in
     the quiet tone with only the filename at full ink, selection as a 2px left bar
     on the raised surface (not a full inverted row), N·G rows dimmed. */
  .medialist { width: 100%; border-collapse: collapse; font-family: var(--ak-mono); font-size: 11px; }
  .medialist thead th {
    position: sticky; top: 0; z-index: 1; background: var(--bg);
    text-align: left; padding: 10px 7px 8px; border-bottom: 1px solid var(--rule);
    font-size: 9.5px; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--quiet); font-weight: 400; white-space: nowrap;
  }
  .medialist th:first-child, .medialist td:first-child { padding-left: 22px; }
  .medialist th:last-child, .medialist td:last-child { padding-right: 22px; }
  /* Sortable headers: the whole label is the button; the arrow slot is always
     there (fixed width) so switching the active column doesn't shift labels. */
  .sorth {
    font: inherit; letter-spacing: inherit; text-transform: inherit; color: inherit;
    background: none; border: none; padding: 0; margin: 0; cursor: pointer;
    display: inline-flex; align-items: center; gap: 3px; white-space: nowrap;
  }
  .sorth:hover, .sorth.on { color: var(--ink); }
  .sorth .arrow { display: inline-block; width: 8px; text-align: center; }
  .medialist tbody tr { cursor: pointer; border-bottom: 1px solid var(--rule); }
  .medialist tbody tr:hover { background: var(--surface); }
  .medialist tbody tr.sel { background: var(--surface); box-shadow: inset 2px 0 0 var(--invert); }
  .medialist tbody tr.ng { opacity: 0.55; }
  .medialist tbody tr.ng td.c-name { text-decoration: line-through; }
  .medialist td { padding: 7px; vertical-align: middle; white-space: nowrap; color: var(--quiet); }
  /* max-width:0 + width:100% lets the name column absorb the slack and ellipsize,
     so the date columns stay on screen at 1400px instead of being cut off. */
  .medialist td.c-name { color: var(--ink); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; max-width: 0; width: 100%; }
  .c-chk { width: 16px; }
  /* The design has no checkbox column; it shows on hover, when checked, or while
     anything is picked, so multi-select stays one click away. */
  .medialist td.c-chk input { opacity: 0; margin: 0; }
  .medialist tr:hover td.c-chk input, .medialist td.c-chk input:checked, .medialist.picking td.c-chk input { opacity: 1; }
  .c-thumb { width: 110px; }
  .medialist td.c-thumb img, .medialist td.c-thumb .lthumbph {
    width: 110px; height: 62px; object-fit: cover; display: block; background: var(--surface-2);
  }
  .lthumbph.audio { background: repeating-linear-gradient(90deg, var(--rule-hi) 0 2px, transparent 2px 4px); }

  .msg { padding: 40px 22px; display: flex; flex-direction: column; gap: 8px; }
  .readypanel { align-items: flex-start; max-width: 580px; gap: 12px; }
  .readypanel .checklist { list-style: none; margin: 2px 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .readyactions { display: flex; gap: 10px; margin-top: 6px; flex-wrap: wrap; align-items: center; }
  .readyactions .ak-btn { text-decoration: none; }
  .loadmore { display: flex; align-items: center; justify-content: center; gap: 14px; padding: 20px 22px; border-top: 1px solid var(--rule); }
  .exportbar {
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
    padding: 9px 22px; border-bottom: 1px solid var(--rule); background: var(--surface-2);
  }
  .expbtns { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .expbtn { font-size: 10px; padding: 5px 10px; text-decoration: none; }
  .expbtn.clear { opacity: 0.7; }
  .expbtn:disabled { opacity: 0.4; cursor: default; }
  /* B11: other exports dim while one runs; the running one stays legible */
  .expbtn[aria-busy='true'] { opacity: 1; cursor: progress; }
  .metacsv:disabled { opacity: 0.4; cursor: default; }
  .metacsv[aria-busy='true'] { opacity: 1; cursor: progress; }
  .barlabel { font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--quiet); }
  .barvrule { width: 1px; height: 16px; background: var(--rule-hi); margin: 0 4px; }
  .binsel, .binnew { font-size: 10px; padding: 4px 6px; }
  .binnew { width: 110px; }
  .binsel:disabled, .binnew:disabled { opacity: 0.4; }
</style>
