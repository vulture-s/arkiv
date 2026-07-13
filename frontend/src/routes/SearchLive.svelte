<!-- Search screen wired to LIVE search. Two modes:
     • Current project (default) — same-DB semantic + lexical via /api/media?q=.
     • All projects — cross-project federation via /api/search/all, fanned out
       read-only across every project in ~/.arkiv-projects.json (managed in
       Settings). Federated rows live in OTHER projects' DBs, so this server can't
       stream/thumbnail/open them — they render read-only (no Open, placeholder
       thumb) and grouped by project. Toggle with the facet or arrive with ?all=1
       (the TopBar "All projects" button seeds it). -->
<script>
  import { onMount } from 'svelte'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Thumb from '../lib/Thumb.svelte'
  import { pushToast } from '../lib/toast.js'
  import { resolvedTheme } from '../lib/prefs.js'

  $: theme = $resolvedTheme
  let query = ''
  let state = 'idle' // idle | loading | ok | error
  let err = ''
  let elapsedMs = 0
  // Unified render model: one group in current-project mode, N in federation mode.
  // { name, path, items:[…], count, openable, live }
  let groups = []
  let total = 0
  $: shown = groups.reduce((a, g) => a + g.items.length, 0)
  // Derived from the project registry when available; neutral label otherwise,
  // so results outside the demo project aren't mislabeled (Codex review P3).
  let projectName = '目前專案'

  // Cross-project federation. Off = current project only (/api/media). On = fan out
  // read-only across the registry (/api/search/all). Federation errors (a stalled
  // NAS mount, an unreadable project.db) surface per-project instead of failing the
  // whole search.
  let crossProject = false
  let projectCount = 0 // registered projects, for the "All projects · N" label
  let fedErrors = []
  let projectsQueried = 0
  let projectsFailed = 0

  // Multi-select of federated results → 加入精選集 (a cross-library bin). Only in
  // federation mode: an item's (project_name, media_id) is the identity a bin
  // stores. Ordered array (add order) + derived Set for O(1) membership — same
  // shape as MainLive's `picked`.
  let picks = [] // [{project_name, media_id, filename}]
  $: pickKeys = new Set(picks.map((p) => p.project_name + ':' + p.media_id))
  let binList = [] // [{id, name, item_count}]
  let targetBinId = ''
  let newBinName = ''

  function togglePick(project_name, media_id, filename) {
    const key = project_name + ':' + media_id
    picks = pickKeys.has(key)
      ? picks.filter((p) => p.project_name + ':' + p.media_id !== key)
      : [...picks, { project_name, media_id, filename }]
  }
  const clearPicks = () => (picks = [])

  async function loadBins() {
    try { binList = (await api.getBins()).bins || [] } catch (e) { /* no bins yet */ }
  }

  async function addPicksToBin() {
    let binId = targetBinId
    try {
      if (!binId && newBinName.trim()) {
        const b = await api.createBin(newBinName.trim())
        binId = b.id
        newBinName = ''
        await loadBins()
        targetBinId = binId
      }
      if (!binId) { pushToast('先選一個精選集或輸入新名稱', 'error'); return }
      const r = await api.addBinItems(binId, picks)
      const name = (binList.find((b) => b.id === binId) || {}).name || '精選集'
      pushToast(`已加入「${name}」（共 ${r.item_count} 支）`)
      clearPicks()
      loadBins()
    } catch (e) { pushToast('加入精選集失敗: ' + e.message, 'error') }
  }

  // Media-type facet — wired to /api/media?media_type= (real backend filter; audit
  // H14 made it apply on the semantic-search branch too). 'all' omits the param.
  // /api/search/all has no media_type filter, so the facet is disabled (not silently
  // ignored) in federation mode — evidence discipline over a fake toggle.
  let mediaType = 'all' // all | video | audio
  let counts = { all: null, video: null, audio: null } // library totals for labels
  const FACETS = [
    { key: 'all', label: 'All' },
    { key: 'video', label: 'Video' },
    { key: 'audio', label: 'Audio' },
  ]

  const fmtDur = (s) => {
    s = Math.round(s || 0)
    const m = Math.floor(s / 60), ss = s % 60
    const p = (n) => String(n).padStart(2, '0')
    return `${p(m)}:${p(ss)}`
  }
  // highlight first occurrence of the query inside an excerpt
  const hl = (text, q) => {
    if (!text) return { b: '', m: '', a: '' }
    if (!q) return { b: text, m: '', a: '' }
    const i = text.indexOf(q)
    return i === -1 ? { b: text, m: '', a: '' } : { b: text.slice(0, i), m: q, a: text.slice(i + q.length) }
  }

  // Update the hash so a query+facet+mode is shareable/reloadable (?q= seed plus
  // ?type= and ?all=1). replaceState avoids spamming history per keystroke.
  function syncHash() {
    const p = new URLSearchParams()
    if (query.trim()) p.set('q', query.trim())
    if (!crossProject && mediaType !== 'all') p.set('type', mediaType)
    if (crossProject) p.set('all', '1')
    const qs = p.toString()
    history.replaceState(null, '', `#/search-live${qs ? '?' + qs : ''}`)
  }

  function setType(key) {
    if (crossProject || mediaType === key) return // media_type doesn't apply to federation
    mediaType = key
    syncHash()
    if (query.trim()) runSearch()
  }

  function setCrossProject(on) {
    if (crossProject === on) return
    crossProject = on
    syncHash()
    if (query.trim()) runSearch()
  }

  // Current-project search (/api/media?q=) → one group of openable rows.
  // round-5 #36: monotonic search sequence. Toggling Current ↔ federated (or
  // re-searching) while a request is in flight must not let the STALE response
  // paint over the newer one — a slow /api/search/all resolving after a fast
  // Current-only search used to clobber `groups` with contradictory, unclickable
  // rows. Each run captures its seq and bails on assignment if superseded.
  let _searchSeq = 0

  async function runCurrent(q, mySeq) {
    const params = { q, limit: 40 }
    if (mediaType !== 'all') params.media_type = mediaType
    const r = await api.getMedia(params)
    if (mySeq !== _searchSeq) return  // superseded by a newer search
    total = r.total ?? (r.items || []).length
    const items = (r.items || []).map((it) => ({
      id: it.id,
      name: it.filename || `#${it.id}`,
      dur: fmtDur(it.duration_s),
      score: typeof it.score === 'number' ? it.score : null,
      excerpt: it.excerpt || it.transcript || '',
      lang: it.lang || '',
      thumb: api.thumbUrlFromPath(it.thumbnail_path),
      tags: (it.tags || []).map((t) => t.name).slice(0, 4),
    }))
    groups = [{ name: projectName, path: '', items, count: total, openable: true, live: true }]
    fedErrors = []
    projectsQueried = 0
    projectsFailed = 0
  }

  // Federated search (/api/search/all) → one group per project, read-only rows.
  async function runFederated(q, mySeq) {
    const r = await api.search(q, { limit: 40 })
    if (mySeq !== _searchSeq) return  // superseded by a newer search
    total = r.total_results ?? (r.items || []).length
    projectsQueried = r.projects_queried ?? 0
    projectsFailed = r.projects_failed ?? 0
    // The "no matching projects" preflight error means an empty registry — surface
    // it as guidance, not as a per-project failure row.
    fedErrors = (r.errors || []).filter((e) => e.stage !== 'preflight' || e.project_name)
    const byProject = new Map()
    for (const it of r.items || []) {
      const name = it.project_name || '（未命名專案）'
      if (!byProject.has(name)) byProject.set(name, { name, path: it.project_path || '', items: [], count: 0, openable: false, live: false })
      byProject.get(name).items.push({
        id: it.media_id,
        name: it.filename || `#${it.media_id}`,
        dur: fmtDur(it.duration_s),
        score: typeof it.score === 'number' ? it.score : null,
        excerpt: it.excerpt || '',
        lang: it.lang || '',
        thumb: null, // cross-project: this server can't serve another project's thumbs
        tags: [],
      })
    }
    for (const g of byProject.values()) g.count = g.items.length
    groups = [...byProject.values()]
  }

  async function runSearch() {
    const q = query.trim()
    if (!q) { _searchSeq++; state = 'idle'; groups = []; total = 0; fedErrors = []; return }
    const mySeq = ++_searchSeq  // round-5 #36: claim this run's sequence
    state = 'loading'
    syncHash()
    const t0 = performance.now()
    try {
      if (crossProject) await runFederated(q, mySeq)
      else await runCurrent(q, mySeq)
      if (mySeq !== _searchSeq) return  // a newer search superseded us — don't flip state
      elapsedMs = Math.round(performance.now() - t0)
      state = 'ok'
    } catch (e) {
      if (mySeq !== _searchSeq) return
      state = 'error'
      err = e.message + (e.body ? ' · ' + JSON.stringify(e.body) : '')
    }
  }

  // Real library totals for the facet labels (mock shows "Video · 124"). Cheap —
  // limit:1, we only read .total. Degrades silently to label-without-count.
  async function loadCounts() {
    const get = async (mt) => {
      try {
        const r = await api.getMedia(mt ? { media_type: mt, limit: 1 } : { limit: 1 })
        return r.total ?? null
      } catch { return null }
    }
    const [all, video, audio] = await Promise.all([get(null), get('video'), get('audio')])
    counts = { all, video, audio }
  }

  onMount(async () => {
    // label the current-project group + count the registry for the "All projects" facet
    try {
      const r = await api.getProjects()
      projectCount = r?.total ?? (r?.projects?.length ?? 0)
      const name = r?.projects?.[0]?.name
      if (name) projectName = name
    } catch (e) { /* no registry → keep neutral label, projectCount 0 */ }
    loadCounts()
    loadBins()
    // seed from the hash, e.g. #/search-live?q=餐廳&type=video  or  ?q=…&all=1
    const h = window.location.hash
    const qi = h.indexOf('?')
    if (qi !== -1) {
      const p = new URLSearchParams(h.slice(qi + 1))
      if (p.get('all') === '1') crossProject = true
      const t = p.get('type')
      if (!crossProject && (t === 'video' || t === 'audio')) mediaType = t
      const q = p.get('q')
      if (q) { query = q; runSearch() }
    }
  })
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">live</Mono>
    <div class="grow"></div>
    <a class="ak-btn" href="#/query-live">⊞ query builder</a>
    <a class="ak-btn" href="#/bins">★ 精選集</a>
    <a class="ak-btn" href="#/main-live">← back to grid</a>
  </div>

  <div class="main">
    <div class="hero">
      <Eyebrow style="margin-bottom:10px;">
        Search · 語意 + 關鍵字（{crossProject ? '跨專案聯邦' : 'current project'}）
      </Eyebrow>
      <div class="queryrow">
        <Mono dim style="font-size:26px;font-weight:400;">⌕</Mono>
        <input
          class="ak-input query"
          placeholder="用一句話描述你要的鏡頭… 試 生肉特寫 / 吧檯空景 / 切割"
          bind:value={query}
          on:keydown={(e) => e.key === 'Enter' && runSearch()}
        />
        <button class="ak-btn" on:click={runSearch}>搜尋</button>
      </div>
      <div class="facets">
        {#each FACETS as f}
          <button
            class="facet" class:active={!crossProject && mediaType === f.key}
            disabled={crossProject}
            title={crossProject ? '跨專案搜尋不支援媒體類型過濾' : ''}
            on:click={() => setType(f.key)}
          >
            {f.label}{#if counts[f.key] != null} · {counts[f.key]}{/if}
          </button>
        {/each}
        <div class="fvrule"></div>
        <button
          class="facet" class:active={!crossProject}
          title="只搜尋目前開啟的專案"
          on:click={() => setCrossProject(false)}
        >Current only</button>
        <button
          class="facet" class:active={crossProject}
          title="跨專案聯邦搜尋 · 讀取 ~/.arkiv-projects.json 登記的所有專案（在設定新增）"
          on:click={() => setCrossProject(true)}
        >All projects{#if projectCount} · {projectCount}{/if}</button>
        <div class="fvrule"></div>
        {#if state === 'ok'}
          <Mono dim style="font-size:10.5px;">
            {total} matches · {elapsedMs}ms
            {#if crossProject}· {projectsQueried - projectsFailed}/{projectsQueried} 專案{:else}· semantic + lexical{/if}
          </Mono>
        {:else if state === 'loading'}
          <Mono dim style="font-size:10.5px;">searching…</Mono>
        {:else if state === 'error'}
          <Mono style="font-size:10.5px;color:var(--cyan);">ERROR: {err}</Mono>
        {:else}
          <Mono dim style="font-size:10.5px;">輸入查詢後按 Enter</Mono>
        {/if}
      </div>
    </div>

    <div class="results">
      {#if crossProject && state === 'ok' && projectsQueried === 0}
        <div class="emptyresult">
          尚未登記任何專案 — 到 <a href="#/settings">設定 → 跨庫專案</a> 新增要一起搜尋的素材庫
        </div>
      {/if}

      {#if crossProject && fedErrors.length}
        <div class="fedbanner">
          <Mono style="font-size:10px;letter-spacing:0.08em;color:var(--cyan);">⚠ {fedErrors.length} 專案查詢失敗</Mono>
          {#each fedErrors as e}
            <Mono dim style="font-size:10px;">· {e.project_name || e.project_path || '?'}: {e.error}</Mono>
          {/each}
        </div>
      {/if}

      {#if crossProject && picks.length}
        <div class="pickbar">
          <Mono style="font-size:11px;font-weight:500;">已選 {picks.length} 支 →</Mono>
          <select class="ak-input binsel" bind:value={targetBinId}>
            <option value="">選精選集…</option>
            {#each binList as b}<option value={b.id}>{b.name}（{b.item_count}）</option>{/each}
          </select>
          <Mono dim style="font-size:10px;">或</Mono>
          <input class="ak-input newbin" placeholder="新精選集名稱…" bind:value={newBinName}
                 on:keydown={(e) => e.key === 'Enter' && addPicksToBin()} />
          <button class="ak-btn ak-btn--primary" on:click={addPicksToBin}>加入精選集</button>
          <button class="ak-btn" on:click={clearPicks}>清除</button>
        </div>
      {/if}

      {#if state === 'ok' && shown === 0 && !(crossProject && projectsQueried === 0)}
        <div class="emptyresult">沒有符合「{query}」的素材</div>
      {:else if shown}
        {#each groups as g (g.name)}
          <section class="rgroup">
            <div class="ghead">
              <Mono dim style="font-size:10px;letter-spacing:0.1em;">PROJECT</Mono>
              <div class="ak-display gproj">{g.name}</div>
              <Mono dim style="font-size:10.5px;">{g.items.length}{#if g.live} of {g.count}{/if}</Mono>
              <div class="grow"></div>
              {#if g.live}
                <Mono dim style="font-size:9.5px;letter-spacing:0.08em;">● LIVE</Mono>
              {:else}
                <Mono dim style="font-size:9.5px;letter-spacing:0.08em;" title="結果在其他專案的資料庫 · 唯讀，無法在此開啟/播放">唯讀 · 跨庫</Mono>
              {/if}
            </div>

            <div class="rows">
              {#each g.items as r, i (g.name + ':' + r.id)}
                {@const p = hl(r.excerpt, query.trim())}
                <svelte:element
                  this={g.openable ? 'a' : 'div'}
                  class="rrow" class:first={i === 0} class:readonly={!g.openable}
                  href={g.openable ? `#/main-live?sel=${r.id}` : undefined}
                >
                  <div class="rthumb">
                    {#if r.thumb}
                      <img class="rthumbimg" src={r.thumb} alt={r.name} loading="lazy" />
                    {:else}
                      <Thumb seed={r.id} kind="video" {theme} />
                    {/if}
                    {#if !g.openable}
                      <!-- federated rows are read-only but pickable → 精選集 -->
                      <label class="pickbox" title="選取加入精選集">
                        <input type="checkbox" checked={pickKeys.has(g.name + ':' + r.id)}
                               on:change={() => togglePick(g.name, r.id, r.name)} />
                      </label>
                    {/if}
                    <Mono style="position:absolute;bottom:2px;right:3px;font-size:9px;color:#f3f2ee;background:rgba(10,10,12,.78);padding:1px 3px;">{r.dur}</Mono>
                  </div>
                  <div class="rcontent">
                    <div class="rtop">
                      <Mono style="font-size:11.5px;font-weight:500;color:var(--ink);">{r.name}</Mono>
                      <div class="rtags">
                        {#each r.tags as t}<span class="rtag">{t}</span>{/each}
                        {#if !g.openable && r.lang}<span class="rtag">{r.lang}</span>{/if}
                      </div>
                    </div>
                    {#if r.excerpt}<div class="snippet">{p.b}<span class="mark">{p.m}</span>{p.a}</div>{/if}
                  </div>
                  <div class="rscore">
                    {#if r.score != null}
                      <Mono style="font-size:13px;font-weight:600;color:var(--ink);">{r.score.toFixed(2)}</Mono>
                      <Mono dim style="font-size:9px;display:block;margin-top:1px;letter-spacing:0.08em;">SCORE</Mono>
                    {/if}
                  </div>
                  <div class="raction">
                    {#if g.openable}<span class="ak-btn openbtn">Open →</span>{/if}
                  </div>
                </svelte:element>
              {/each}
            </div>
          </section>
        {/each}
      {/if}
    </div>
  </div>
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .main { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .hero { padding: 24px 64px 18px; border-bottom: 1px solid var(--rule); }
  .queryrow { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
  .query { flex: 1; font-size: 20px; padding: 8px 4px; }
  .facets { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .facet { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; padding: 5px 10px; background: transparent; color: var(--ink-2); border: 1px solid var(--rule); cursor: pointer; line-height: 1; }
  .facet:hover:not(.active):not(:disabled) { border-color: var(--rule-hi); }
  .facet.active { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
  .facet:disabled { cursor: default; opacity: 0.5; }
  .fvrule { width: 1px; height: 14px; background: var(--rule); margin: 0 4px; }
  .results { flex: 1; overflow: auto; padding: 14px 64px 18px; }
  .fedbanner { display: flex; flex-direction: column; gap: 2px; margin-bottom: 12px; padding: 8px 10px; border: 1px solid var(--rule); background: var(--surface-2); }
  .pickbar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 10px; margin-bottom: 12px; padding: 8px 12px; border: 1px solid var(--rule-hi); background: var(--bg); }
  .binsel, .newbin { font-size: 12px; padding: 4px 6px; }
  .newbin { flex: 0 1 180px; }
  .pickbox { position: absolute; top: 4px; left: 4px; z-index: 2; display: flex; }
  .pickbox input { width: 15px; height: 15px; cursor: pointer; accent-color: var(--invert); }
  .rgroup { margin-bottom: 16px; }
  .ghead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
  .gproj { font-size: 18px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .emptyresult { padding: 24px 16px; font-family: var(--ak-mono); font-size: 11px; color: var(--quiet); text-align: center; letter-spacing: 0.05em; }
  .emptyresult a { color: var(--ink-2); }
  .rrow { display: grid; grid-template-columns: 100px 1fr 60px 78px; gap: 14px; align-items: center; padding: 5px 0; border-top: 1px solid var(--rule); cursor: pointer; text-decoration: none; color: inherit; }
  .rrow.first { border-top: none; }
  .rrow:hover { background: var(--surface-2); }
  .rrow.readonly { cursor: default; }
  .rrow.readonly:hover { background: transparent; }
  .rthumb { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }
  .rthumbimg { width: 100%; height: 100%; object-fit: cover; display: block; }
  .rcontent { min-width: 0; }
  .rtop { display: flex; align-items: baseline; gap: 8px; }
  .rtags { display: flex; gap: 4px; }
  .rtag { font-family: var(--ak-mono); font-size: 9px; padding: 1px 4px; border: 1px solid var(--rule); color: var(--quiet); line-height: 1.2; }
  .snippet { margin-top: 3px; font-size: 12.5px; color: var(--ink-2); line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mark { background: var(--invert); color: var(--invert-ink); padding: 0 2px; }
  .rscore { text-align: right; }
  .raction { display: flex; justify-content: flex-end; }
  .openbtn { padding: 5px 9px; }
</style>
