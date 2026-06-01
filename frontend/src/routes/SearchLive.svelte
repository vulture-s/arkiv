<!-- Search screen wired to LIVE semantic search (/api/media?q=). The mock
     Search.svelte stays a design baseline; this is its functional twin.
     Same-DB semantic + lexical ranking (score + excerpt come from the backend);
     cross-project federation (the mock's multi-group view) needs a populated
     ~/.arkiv-projects.json registry and is out of scope here — we show the one
     active project. -->
<script>
  import { onMount } from 'svelte'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Thumb from '../lib/Thumb.svelte'

  const theme = 'dark'
  let query = ''
  let state = 'idle' // idle | loading | ok | error
  let err = ''
  let results = []
  let total = 0
  let elapsedMs = 0
  // Derived from the project registry when available; neutral label otherwise,
  // so results outside the demo project aren't mislabeled (Codex review P3).
  let projectName = '目前專案'

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

  async function runSearch() {
    const q = query.trim()
    if (!q) { state = 'idle'; results = []; total = 0; return }
    state = 'loading'
    const t0 = performance.now()
    try {
      const r = await api.getMedia({ q, limit: 40 })
      elapsedMs = Math.round(performance.now() - t0)
      total = r.total ?? (r.items || []).length
      results = (r.items || []).map((it) => ({
        id: it.id,
        name: it.filename || `#${it.id}`,
        dur: fmtDur(it.duration_s),
        score: typeof it.score === 'number' ? it.score : null,
        excerpt: it.excerpt || it.transcript || '',
        thumb: api.thumbUrlFromPath(it.thumbnail_path),
        // first few tag names (raw per-result tags); keep it light
        tags: (it.tags || []).map((t) => t.name).slice(0, 4),
      }))
      state = 'ok'
    } catch (e) {
      state = 'error'
      err = e.message + (e.body ? ' · ' + JSON.stringify(e.body) : '')
    }
  }

  onMount(async () => {
    // label the result group from the active project registry, if any
    try {
      const r = await api.getProjects()
      const name = r?.projects?.[0]?.name
      if (name) projectName = name
    } catch (e) { /* no registry → keep neutral label */ }
    // seed from ?q= in the hash, e.g. #/search-live?q=餐廳
    const h = window.location.hash
    const qi = h.indexOf('?')
    if (qi !== -1) {
      const p = new URLSearchParams(h.slice(qi + 1))
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
    <a class="ak-btn" href="#/main-live">← back to grid</a>
  </div>

  <div class="main">
    <div class="hero">
      <Eyebrow style="margin-bottom:10px;">Search · 語意 + 關鍵字（current project）</Eyebrow>
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
        {#if state === 'ok'}
          <Mono dim style="font-size:10.5px;">{total} matches · {elapsedMs}ms · semantic + lexical</Mono>
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
      {#if state === 'ok' && results.length === 0}
        <div class="emptyresult">沒有符合「{query}」的素材</div>
      {:else if results.length}
        <section class="rgroup">
          <div class="ghead">
            <Mono dim style="font-size:10px;letter-spacing:0.1em;">PROJECT</Mono>
            <div class="ak-display gproj">{projectName}</div>
            <Mono dim style="font-size:10.5px;">{results.length} of {total}</Mono>
            <div class="grow"></div>
            <Mono dim style="font-size:9.5px;letter-spacing:0.08em;">● LIVE</Mono>
          </div>

          <div class="rows">
            {#each results as r, i (r.id)}
              {@const p = hl(r.excerpt, query.trim())}
              <a class="rrow" class:first={i === 0} href={`#/main-live?sel=${r.id}`}>
                <div class="rthumb">
                  {#if r.thumb}
                    <img class="rthumbimg" src={r.thumb} alt={r.name} loading="lazy" />
                  {:else}
                    <Thumb seed={r.id} kind="video" {theme} />
                  {/if}
                  <Mono style="position:absolute;bottom:2px;right:3px;font-size:9px;color:#f3f2ee;background:rgba(10,10,12,.78);padding:1px 3px;">{r.dur}</Mono>
                </div>
                <div class="rcontent">
                  <div class="rtop">
                    <Mono style="font-size:11.5px;font-weight:500;color:var(--ink);">{r.name}</Mono>
                    <div class="rtags">{#each r.tags as t}<span class="rtag">{t}</span>{/each}</div>
                  </div>
                  {#if r.excerpt}<div class="snippet">{p.b}<span class="mark">{p.m}</span>{p.a}</div>{/if}
                </div>
                <div class="rscore">
                  {#if r.score != null}
                    <Mono style="font-size:13px;font-weight:600;color:var(--ink);">{r.score.toFixed(2)}</Mono>
                    <Mono dim style="font-size:9px;display:block;margin-top:1px;letter-spacing:0.08em;">SCORE</Mono>
                  {/if}
                </div>
                <div class="raction"><span class="ak-btn openbtn">Open →</span></div>
              </a>
            {/each}
          </div>
        </section>
      {/if}
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .topbar a { text-decoration: none; }
  .main { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .hero { padding: 24px 64px 18px; border-bottom: 1px solid var(--rule); }
  .queryrow { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
  .query { flex: 1; font-size: 20px; padding: 8px 4px; }
  .facets { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .results { flex: 1; overflow: auto; padding: 14px 64px 18px; }
  .rgroup { margin-bottom: 16px; }
  .ghead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
  .gproj { font-size: 18px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .emptyresult { padding: 24px 16px; font-family: var(--ak-mono); font-size: 11px; color: var(--quiet); text-align: center; letter-spacing: 0.05em; }
  .rrow { display: grid; grid-template-columns: 100px 1fr 60px 78px; gap: 14px; align-items: center; padding: 5px 0; border-top: 1px solid var(--rule); cursor: pointer; text-decoration: none; color: inherit; }
  .rrow.first { border-top: none; }
  .rrow:hover { background: var(--surface-2); }
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
