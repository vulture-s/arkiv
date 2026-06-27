<!-- Phase 9.7 G6 — structured query builder, wired LIVE to POST /api/search/query.
     The Flows.svelte B2 artboard is the design baseline; this is its functional
     twin. Typed field conditions combined by AND/OR, with an optional semantic
     (vector) leg. Empty / no-result states are honest, not faked. -->
<script>
  import { onMount } from 'svelte'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Thumb from '../lib/Thumb.svelte'
  import { resolvedTheme } from '../lib/prefs.js'

  $: theme = $resolvedTheme

  // Field catalogue — mirrors query_builder._FIELDS on the backend. `kind` drives
  // which input renders; `ops` are the operators the backend accepts per field.
  const FIELDS = [
    { key: 'semantic', label: 'Semantic（語意）', ops: ['contains'], kind: 'text' },
    { key: 'transcript', label: 'Transcript（逐字稿）', ops: ['contains'], kind: 'text' },
    { key: 'tag', label: 'Tag（標籤）', ops: ['contains', 'eq'], kind: 'text' },
    { key: 'filename', label: 'Filename（檔名）', ops: ['contains', 'eq'], kind: 'text' },
    { key: 'camera', label: 'Camera（機型）', ops: ['contains', 'eq'], kind: 'text' },
    { key: 'content_type', label: 'Content type', ops: ['contains', 'eq'], kind: 'text' },
    { key: 'lang', label: 'Language', ops: ['eq'], kind: 'text' },
    { key: 'rating', label: 'Rating（評分）', ops: ['eq'], kind: 'enum', options: ['good', 'ng', 'review', 'unrated'] },
    { key: 'media_type', label: 'Media type', ops: ['eq'], kind: 'enum', options: ['video', 'audio'] },
    { key: 'duration', label: 'Duration（秒）', ops: ['range'], kind: 'range' },
    { key: 'iso', label: 'ISO', ops: ['range'], kind: 'range' },
    { key: 'date', label: 'Processed date', ops: ['range'], kind: 'daterange' },
  ]
  const fieldMeta = (k) => FIELDS.find((f) => f.key === k) || FIELDS[0]

  const OP_LABEL = { contains: 'contains', eq: 'equals', range: 'between' }

  let match = 'all' // all | any
  // each condition: {field, op, value, vmin, vmax}
  let conditions = [{ field: 'semantic', op: 'contains', value: '', vmin: '', vmax: '' }]

  let state = 'idle' // idle | loading | ok | error
  let err = ''
  let results = []
  let total = 0
  let elapsedMs = 0
  let degraded = ''

  function addCondition() {
    conditions = [...conditions, { field: 'transcript', op: 'contains', value: '', vmin: '', vmax: '' }]
  }
  function removeCondition(i) {
    conditions = conditions.filter((_, idx) => idx !== i)
  }
  function onFieldChange(i) {
    // reset op to the field's first valid op when the field changes
    const c = conditions[i]
    c.op = fieldMeta(c.field).ops[0]
    c.value = ''; c.vmin = ''; c.vmax = ''
    conditions = conditions
  }

  const fmtDur = (s) => {
    s = Math.round(s || 0)
    const m = Math.floor(s / 60), ss = s % 60
    const p = (n) => String(n).padStart(2, '0')
    return `${p(m)}:${p(ss)}`
  }

  // Build the API spec from the UI rows, dropping empty conditions.
  function buildSpec() {
    const out = []
    for (const c of conditions) {
      const meta = fieldMeta(c.field)
      if (meta.kind === 'range' || meta.kind === 'daterange') {
        const lo = c.vmin === '' ? null : (meta.kind === 'range' ? Number(c.vmin) : c.vmin)
        const hi = c.vmax === '' ? null : (meta.kind === 'range' ? Number(c.vmax) : c.vmax)
        if (lo === null && hi === null) continue
        out.push({ field: c.field, op: 'range', value: [lo, hi] })
      } else {
        if (!String(c.value).trim()) continue
        out.push({ field: c.field, op: c.op, value: String(c.value).trim() })
      }
    }
    return out
  }

  async function run() {
    const spec = buildSpec()
    if (!spec.length) { state = 'idle'; results = []; total = 0; err = '至少填一個條件'; return }
    err = ''; degraded = ''; state = 'loading'
    const t0 = performance.now()
    try {
      const r = await api.structuredQuery({ match, conditions: spec, limit: 60 })
      elapsedMs = Math.round(performance.now() - t0)
      total = r.total ?? (r.items || []).length
      if (r.search_degraded) degraded = r.warning || 'semantic search degraded'
      results = (r.items || []).map((it) => ({
        id: it.id,
        name: it.filename || `#${it.id}`,
        dur: fmtDur(it.duration_s),
        thumb: api.thumbUrlFromPath(it.thumbnail_path),
        rating: it.rating || null,
        tags: (it.tags || []).map((t) => t.name).slice(0, 4),
      }))
      state = 'ok'
    } catch (e) {
      state = 'error'
      err = (e?.body?.detail) || e.message
    }
  }
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">live · query builder</Mono>
    <div class="grow"></div>
    <a class="ak-btn" href="#/search-live">⌕ simple search</a>
    <a class="ak-btn" href="#/main-live">← back to grid</a>
  </div>

  <div class="main">
    <div class="builder">
      <div class="bhead">
        <Eyebrow style="margin-bottom:6px;">Query builder · compound（結構化查詢）</Eyebrow>
        <div class="matchrow">
          <Mono dim style="font-size:11px;">Find media matching</Mono>
          <div class="seg">
            <button class="segbtn" class:on={match === 'all'} on:click={() => (match = 'all')}>All（AND）</button>
            <div class="segsep"></div>
            <button class="segbtn" class:on={match === 'any'} on:click={() => (match = 'any')}>Any（OR）</button>
          </div>
          <Mono dim style="font-size:11px;">of the conditions:</Mono>
        </div>
      </div>

      <div class="clauses">
        {#each conditions as c, i}
          {@const meta = fieldMeta(c.field)}
          <div class="clause">
            <select class="ak-input sel cfield" bind:value={c.field} on:change={() => onFieldChange(i)}>
              {#each FIELDS as f}<option value={f.key}>{f.label}</option>{/each}
            </select>

            {#if meta.ops.length > 1}
              <select class="ak-input sel cop" bind:value={c.op}>
                {#each meta.ops as op}<option value={op}>{OP_LABEL[op]}</option>{/each}
              </select>
            {:else}
              <span class="cop static">{OP_LABEL[meta.ops[0]]}</span>
            {/if}

            {#if meta.kind === 'range'}
              <span class="rangewrap">
                <input class="ak-input num" type="number" placeholder="min" bind:value={c.vmin} />
                <Mono dim style="font-size:11px;">—</Mono>
                <input class="ak-input num" type="number" placeholder="max" bind:value={c.vmax} />
              </span>
            {:else if meta.kind === 'daterange'}
              <span class="rangewrap">
                <input class="ak-input" type="date" bind:value={c.vmin} />
                <Mono dim style="font-size:11px;">→</Mono>
                <input class="ak-input" type="date" bind:value={c.vmax} />
              </span>
            {:else if meta.kind === 'enum'}
              <select class="ak-input sel cvalue" bind:value={c.value}>
                <option value="" disabled selected>選擇…</option>
                {#each meta.options as o}<option value={o}>{o}</option>{/each}
              </select>
            {:else}
              <input class="ak-input cvalue" placeholder="值…" bind:value={c.value}
                     on:keydown={(e) => e.key === 'Enter' && run()} spellcheck="false" />
            {/if}

            <button class="cx" title="移除" on:click={() => removeCondition(i)} disabled={conditions.length === 1}>✕</button>
          </div>
        {/each}
        <button class="addclause" on:click={addCondition}>+ Add condition</button>
      </div>

      <div class="brun">
        <button class="ak-btn ak-btn--primary" on:click={run} disabled={state === 'loading'}>
          {state === 'loading' ? 'Running…' : 'Run query →'}
        </button>
        {#if state === 'ok'}
          <Mono dim style="font-size:10.5px;">{total} matches · {elapsedMs}ms</Mono>
        {:else if state === 'error'}
          <Mono style="font-size:10.5px;color:var(--cyan);">ERROR: {err}</Mono>
        {:else if err}
          <Mono dim style="font-size:10.5px;">{err}</Mono>
        {/if}
        {#if degraded}<Mono style="font-size:10px;color:var(--cyan);">⚠ {degraded}</Mono>{/if}
      </div>
    </div>

    <div class="results">
      {#if state === 'ok' && results.length === 0}
        <div class="emptyresult">沒有符合條件的素材</div>
      {:else if results.length}
        <section class="rgroup">
          <div class="ghead">
            <Mono dim style="font-size:10px;letter-spacing:0.1em;">RESULTS</Mono>
            <Mono dim style="font-size:10.5px;">{results.length} of {total}</Mono>
            <div class="grow"></div>
            <Mono dim style="font-size:9.5px;letter-spacing:0.08em;">● LIVE</Mono>
          </div>
          <div class="rows">
            {#each results as r, i (r.id)}
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
                </div>
                {#if r.rating}<div class="rrating"><Mono dim style="font-size:9px;letter-spacing:0.08em;text-transform:uppercase;">{r.rating}</Mono></div>{/if}
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
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .topbar a { text-decoration: none; }
  .main { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .builder { padding: 22px 64px 16px; border-bottom: 1px solid var(--rule); }
  .matchrow { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .seg { display: flex; border: 1px solid var(--rule); width: fit-content; }
  .segsep { width: 1px; background: var(--rule); }
  .segbtn { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 5px 12px; background: transparent; color: var(--ink-2); border: none; cursor: pointer; line-height: 1; }
  .segbtn.on { background: var(--invert); color: var(--invert-ink); font-weight: 700; }
  .clauses { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; }
  .clause { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .cfield { min-width: 190px; }
  .cop { min-width: 110px; }
  .cop.static { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.05em; color: var(--quiet); padding: 0 8px; min-width: 110px; }
  .cvalue { min-width: 240px; }
  .rangewrap { display: flex; align-items: center; gap: 8px; }
  .rangewrap .num { width: 110px; }
  .cx { background: transparent; border: 1px solid var(--rule); color: var(--quiet); cursor: pointer; width: 26px; height: 26px; line-height: 1; }
  .cx:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
  .cx:disabled { opacity: 0.4; cursor: default; }
  .addclause { margin-top: 4px; align-self: flex-start; font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; background: transparent; border: 1px dashed var(--rule-hi); color: var(--ink-2); padding: 6px 12px; cursor: pointer; }
  .addclause:hover { border-style: solid; }
  .brun { display: flex; align-items: center; gap: 14px; margin-top: 16px; }
  .results { flex: 1; overflow: auto; padding: 14px 64px 18px; }
  .rgroup { margin-bottom: 16px; }
  .ghead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
  .emptyresult { padding: 24px 16px; font-family: var(--ak-mono); font-size: 11px; color: var(--quiet); text-align: center; letter-spacing: 0.05em; }
  .rrow { display: grid; grid-template-columns: 100px 1fr 70px 78px; gap: 14px; align-items: center; padding: 5px 0; border-top: 1px solid var(--rule); cursor: pointer; text-decoration: none; color: inherit; }
  .rrow.first { border-top: none; }
  .rrow:hover { background: var(--surface-2); }
  .rthumb { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }
  .rthumbimg { width: 100%; height: 100%; object-fit: cover; display: block; }
  .rcontent { min-width: 0; }
  .rtop { display: flex; align-items: baseline; gap: 8px; }
  .rtags { display: flex; gap: 4px; }
  .rtag { font-family: var(--ak-mono); font-size: 9px; padding: 1px 4px; border: 1px solid var(--rule); color: var(--quiet); line-height: 1.2; }
  .rrating { text-align: right; }
  .raction { display: flex; justify-content: flex-end; }
  .openbtn { padding: 5px 9px; }
</style>
