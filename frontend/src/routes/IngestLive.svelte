<!-- S1b — IngestLive, layout aligned to the Ingest mock (docs/design/redesign-2026
     op-01 progress screen + frontend/src/routes/Ingest.svelte), wired to the real
     /ws/ingest stream. Since S1a brick 3 the protocol carries per-stage events
     ({type:"stage", stage, index, counts}, stage ∈ probe/transcribe/thumbnail/
     frames/vision) on top of per-file start/done/skipped + start/complete.

     Honest deviations from the mock (no backend source → not faked, per evidence
     discipline + plan S1b deviation):
       · GPU tile — PC-only (mini/Mac pipeline has none).
       · THROUGHPUT / per-file ETA — backend emits no timing yet (brick 3 deferred).
       · per-file SIZE — not in the WS events.
       · log warn/error levels — WS carries no structured log level.
     Queue rows only exist once a file starts (the backend names files as it
     reaches them; not-yet-started files are an honest "N queued" count, not faked
     rows). The mock's 5 internal stages map to its 3 user-facing columns:
     PROBE=probe · TRANSCRIBE=transcribe · TAG=vision (thumbnail/frames roll into
     the progress bar). Triggering lives in /ingest-setup (op-01); a compact
     trigger stays here as the empty state for standalone use. -->
<script>
  import { onMount, onDestroy } from 'svelte'
  import { push } from 'svelte-spa-router'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { resolvedTheme } from '../lib/prefs.js'

  $: theme = $resolvedTheme
  const BASE = import.meta.env?.VITE_API_URL ?? ''
  // FILE / PROBE / TRANSCRIBE / TAG / PROGRESS — SIZE+ETA dropped (no backend source).
  const cols = '1fr 76px 92px 76px 1.3fr'

  let ws = null
  let conn = 'connecting' // connecting | open | closed
  let path = ''
  let limit = 3
  let total = 0
  let files = {} // index → {filename, status, stage}
  let stageCounts = {} // aggregate per-stage tally from the backend (counts dict)
  let complete = null // {ok, skipped, failed}
  let log = [] // [{t, stage, text}]
  let busy = false
  let rebuilding = false
  let err = ''
  let startedAt = 0 // ms — set when the run's 'start' event arrives (for elapsed)
  let now = Date.now() // kept fresh by the 1s tick; seeded so countdowns never read 0
  let tick = null
  // edge-state C1 (redesign essay 08) — auto-reconnect with backoff when the ws
  // drops. The ingest runs as a detached server task and writes to the DB per
  // file, so a dropped socket pauses only the live VIEW, not the work; reconnecting
  // rejoins the broadcast. retry>0 means we've seen at least one close.
  let retry = 0
  const MAX_RETRY = 8
  let reconnectAt = 0 // ms timestamp of the next auto attempt (0 = none scheduled)
  let closedByUs = false // set on unmount so we don't reconnect a teardown
  $: reconnectIn = reconnectAt && now ? Math.max(0, Math.ceil((reconnectAt - now) / 1000)) : 0
  $: disconnected = conn === 'closed' && retry > 0 && !closedByUs

  function scheduleReconnect() {
    if (closedByUs || retry >= MAX_RETRY) { reconnectAt = 0; return }
    const delaySec = Math.min(8, 2 ** Math.min(retry, 3)) // 2 → 4 → 8 → 8…
    reconnectAt = Date.now() + delaySec * 1000
  }

  // Canonical pipeline order (matches ingest.py _stage calls). probe→frames run
  // in phase 1 (file ends "done"); vision streams in a phase-2 pass, so a file can
  // receive a vision event after it already read "done" — handled below.
  const STAGES = ['probe', 'transcribe', 'thumbnail', 'frames', 'vision']
  // Mock's 3 user-facing columns → canonical stage index.
  const COLS = [
    { head: 'PROBE', si: 0 },
    { head: 'TRANSCRIBE', si: 1 },
    { head: 'TAG', si: 4 },
  ]
  const stageText = { done: 'OK', running: 'RUN', queued: '·' }

  const wsUrl = () => {
    // /ws/ingest requires ingest_write; a WebSocket can't send an Authorization
    // header, so carry the token as ?token= when one is set (direct remote
    // deployment). No-op on loopback / behind the dev proxy.
    const base = BASE
      ? BASE.replace(/^http/, 'ws') + '/ws/ingest'
      : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/ingest`
    return api.appendToken(base)
  }

  function pushLog(text, stage = '') {
    const t = new Date().toLocaleTimeString()
    log = [{ t, stage, text }, ...log].slice(0, 60)
  }

  function connect() {
    try {
      ws = new WebSocket(wsUrl())
    } catch (e) {
      conn = 'closed'
      err = String(e)
      return
    }
    ws.onopen = () => { conn = 'open'; retry = 0; reconnectAt = 0 }
    ws.onclose = () => { conn = 'closed'; retry += 1; scheduleReconnect() }
    ws.onerror = () => { conn = 'closed' }
    ws.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.type === 'start') {
        total = msg.total || 0
        files = {}
        stageCounts = {}
        complete = null
        startedAt = Date.now()
        pushLog(`start · ${total} files`)
      } else if (msg.type === 'file') {
        // Preserve any stage already recorded for this file (e.g. a vision event
        // can land after the "done" file event in phase 2 — don't clobber it).
        const prev = files[msg.index] || {}
        files[msg.index] = { ...prev, filename: msg.filename, status: msg.status }
        files = files // trigger reactivity
        pushLog(`[${msg.index}/${msg.total}] ${msg.filename} · ${msg.status}`)
      } else if (msg.type === 'stage') {
        // Per-stage event (S1a brick 3): attribute to the in-flight file by index
        // and refresh the aggregate tally the backend keeps for us.
        const prev = files[msg.index] || { filename: msg.filename, status: 'transcribing' }
        files[msg.index] = { ...prev, filename: prev.filename || msg.filename, stage: msg.stage }
        files = files // trigger reactivity
        if (msg.counts) stageCounts = msg.counts
        pushLog(`${msg.filename}`, msg.stage)
      } else if (msg.type === 'complete') {
        complete = { ok: msg.ok, skipped: msg.skipped, failed: msg.failed }
        busy = false
        pushLog(`complete · ok=${msg.ok} skipped=${msg.skipped} failed=${msg.failed}`)
      }
    }
  }

  async function trigger() {
    if (!path.trim()) { err = '請填要 ingest 的資料夾路徑'; return }
    err = ''
    busy = true
    files = {}
    stageCounts = {}
    complete = null
    try {
      // Through the authenticated API layer (adds the Bearer token when set) —
      // /api/ingest/ws requires ingest_write; a raw fetch would 401 on a tokened
      // remote backend.
      await api.ingestWs(path.trim(), Number(limit) || 0)
      pushLog(`triggered ingest: ${path} (limit ${limit})`)
    } catch (e) {
      err = e.message
      busy = false
    }
  }

  // Rebuild the semantic index — independent of the ingest WS (plain POST,
  // background task on the server). Kept on its own flag so it never collides
  // with the ws ingest's busy/complete lifecycle.
  async function rebuildIndex() {
    err = ''
    rebuilding = true
    try {
      const res = await api.rebuildEmbedIndex()
      pushLog(res.message || '重建向量索引：已排入背景')
    } catch (e) {
      err = e.message
    } finally {
      rebuilding = false
    }
  }

  // Manual "Reconnect now" — also used by the auto-retry tick.
  function reconnectNow() {
    reconnectAt = 0
    if (conn === 'open') return
    connect()
  }

  onMount(() => {
    connect()
    tick = setInterval(() => {
      now = Date.now()
      if (reconnectAt && now >= reconnectAt && conn === 'closed' && !closedByUs) reconnectNow()
    }, 1000)
  })
  onDestroy(() => {
    closedByUs = true
    ws && ws.close()
    tick && clearInterval(tick)
  })

  $: rows = Object.entries(files)
    .map(([i, f]) => ({ index: Number(i), ...f }))
    .sort((a, b) => a.index - b.index)
  $: doneCount = rows.filter((r) => r.status === 'done').length
  $: pct = total ? Math.round((doneCount / total) * 100) : 0
  $: remaining = Math.max(0, total - rows.length)
  $: active = busy || rows.length > 0 || complete
  // Real aggregate metrics from the stage tally (mock's PROBED/TRANSCRIBED/TAGGED).
  $: metrics = [
    ['PROBED', stageCounts.probe || 0],
    ['TRANSCRIBED', stageCounts.transcribe || 0],
    ['TAGGED', stageCounts.vision || 0],
  ]
  $: elapsed = startedAt && now ? fmtDur(now - startedAt) : ''

  function fmtDur(ms) {
    const s = Math.floor(ms / 1000)
    const m = Math.floor(s / 60)
    return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  }

  // Reached index in the canonical pipeline for a row: -1 (none) up to 4 (vision).
  // A "done" file (phase 1 complete) has cleared probe→frames even if its last
  // event was an earlier stage; vision only lights up once its own event lands.
  function reached(r) {
    let idx = r.stage ? STAGES.indexOf(r.stage) : -1
    if (r.status === 'done' && idx < 3) idx = 3 // probe..frames done in phase 1
    return idx
  }
  // Per-column state for the mock's 3 stage columns (PROBE/TRANSCRIBE/TAG).
  function colState(si, r) {
    if (r.status === 'skipped') return 'queued'
    if (complete && r.status === 'done') return 'done' // run finished → all cleared
    const R = reached(r)
    if (R > si) return 'done'
    if (R === si) return (r.status === 'done' && si < 4) ? 'done' : 'running'
    return 'queued'
  }
  // Per-row progress from real stage progression (probe 20% … vision 100%).
  function rowPct(r) {
    if (r.status === 'skipped') return 100
    if (complete && r.status === 'done') return 100
    const R = reached(r)
    return R < 0 ? 0 : Math.round(((R + 1) / STAGES.length) * 100)
  }
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">ws://…/ws/ingest · <span class:on={conn === 'open'} class:off={conn !== 'open'}>{conn.toUpperCase()}</span></Mono>
    <button class="ak-btn" on:click={() => push('/ingest-setup')}>New ingest →</button>
  </div>

  {#if disconnected}
    <div class="dbanner">
      <div class="dbtext">
        <div class="dbhead">
          <Eyebrow style="color:var(--ink);">◇ STREAM DISCONNECTED</Eyebrow>
          {#if retry < MAX_RETRY}
            <Mono dim style="font-size:10px;">retry {retry} of {MAX_RETRY}{reconnectIn ? ` · next in 00:${String(reconnectIn).padStart(2, '0')}` : ' · reconnecting…'}</Mono>
          {:else}
            <Mono dim style="font-size:10px;">auto-retry gave up · reconnect manually</Mono>
          {/if}
        </div>
        <Mono dim style="font-size:11px;line-height:1.5;display:block;margin-top:4px;">
          The ingest keeps running on the server and writes each file to the library as it finishes — the dropped socket only pauses this live view. Reconnecting rejoins the stream.
        </Mono>
      </div>
      <button class="ak-btn ak-btn--primary" on:click={reconnectNow}>Reconnect now</button>
    </div>
  {/if}

  <div class="split">
    <!-- LEFT -->
    <div class="left">
      <div class="hero">
        <div class="herohead">
          <Eyebrow>Ingest · live websocket</Eyebrow>
          <Mono dim style="font-size:10.5px;">{#if startedAt}{elapsed} elapsed · real /ws/ingest{:else}real /ws/ingest stream{/if}</Mono>
        </div>

        {#if active}
          <div class="herobig">
            <div class="ak-display bignum">{doneCount}<span class="quiet">/{total || '—'}</span></div>
            <div class="herometa">
              <Mono dim style="font-size:11px;letter-spacing:0.05em;">FILES PROCESSED</Mono>
              <Mono style="font-size:13px;font-weight:500;display:block;margin-top:4px;">{path || '— folder import in progress —'}</Mono>
              <Mono dim style="font-size:10.5px;display:block;margin-top:2px;">
                {#if complete}done · ok={complete.ok} skip={complete.skipped} fail={complete.failed}
                {:else}{remaining} queued · {rows.length} seen{/if}
              </Mono>
            </div>
          </div>
        {:else}
          <!-- empty state: trigger here for standalone use (op-01 is the main path) -->
          <div class="herobig">
            <div class="ak-display bignum quiet">—</div>
            <div class="trigger">
              <input class="ak-input pathinput" placeholder="ingest 資料夾路徑（例 ~/Desktop/Test）" bind:value={path} disabled={busy} />
              <div class="triggerrow">
                <input class="ak-input limitinput" type="number" min="0" bind:value={limit} disabled={busy} title="limit (0=all)" />
                <button class="ak-btn ak-btn--primary" on:click={trigger} disabled={busy || conn !== 'open'}>{busy ? 'running…' : 'Start ingest →'}</button>
                <button class="ak-btn" on:click={rebuildIndex} disabled={rebuilding || busy} title="重建 ChromaDB 向量索引（背景執行）">{rebuilding ? '排入中…' : '重建索引'}</button>
              </div>
            </div>
          </div>
        {/if}
        {#if err}<Mono style="font-size:11px;color:var(--cyan);margin-top:8px;display:block;">{err}</Mono>{/if}

        <div class="aggwrap">
          <div class="aggrow"><Mono dim style="font-size:10px;">AGGREGATE</Mono><Mono style="font-size:11px;font-weight:600;color:var(--cyan);">{pct}%</Mono></div>
          <div class="aggbar"><div class="aggfill" style="width:{pct}%;"></div><div class="aggmark" style="left:{pct}%;"></div></div>
          <div class="metrics">
            {#each metrics as [label, value]}
              <div class="metric">
                <Mono dim style="font-size:9.5px;letter-spacing:0.1em;display:block;margin-bottom:3px;">{label}</Mono>
                <span class="metricval">{value}</span><Mono dim style="font-size:10px;margin-left:4px;">/ {total || '—'}</Mono>
              </div>
            {/each}
          </div>
        </div>
      </div>

      <div class="queue">
        <div class="qhead">
          <Eyebrow>Queue {#if total}· {remaining} queued{/if}</Eyebrow>
          {#if startedAt}<Mono dim style="font-size:10.5px;">{rows.length} of {total} started</Mono>{/if}
        </div>
        {#if rows.length === 0}
          <div class="empty"><Mono dim>等待 ingest 觸發…（填路徑按 Start，或用 New ingest）</Mono></div>
        {:else}
          <div class="qrow qheadrow" style="grid-template-columns:{cols};">
            {#each ['FILE', 'PROBE', 'TRANSCRIBE', 'TAG', 'PROGRESS'] as h}<Mono dim style="font-size:9.5px;letter-spacing:0.1em;">{h}</Mono>{/each}
          </div>
          <div class="qrows">
            {#each rows as r (r.index)}
              <div class="qrow" class:done={r.status === 'done'} style="grid-template-columns:{cols};">
                <Mono style="font-size:11.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{r.filename}</Mono>
                {#each COLS as c}
                  {@const st = r.status === 'skipped' ? 'skipped' : colState(c.si, r)}
                  <span class="stage {st}">{r.status === 'skipped' ? 'SKIP' : stageText[st]}</span>
                {/each}
                <div class="pbar"><div class="pfill" class:full={rowPct(r) === 100} style="width:{rowPct(r)}%;"></div></div>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>

    <!-- RIGHT: log -->
    <div class="right">
      <div class="loghead">
        <div class="logheadrow"><Eyebrow>Live log · ws stream</Eyebrow><span class="livedot" class:on={conn === 'open'}>● {conn === 'open' ? 'LIVE' : conn.toUpperCase()}</span></div>
        <Mono dim style="font-size:10px;margin-top:4px;">{log.length} events</Mono>
      </div>
      <div class="logbody">
        {#each log as ln}
          <div class="logline">
            <span class="logt">{ln.t}</span>
            <span class="logstage">{ln.stage}</span>
            <span class="logmsg">{ln.text}</span>
          </div>
        {/each}
      </div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); display: flex; flex-direction: column; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .quiet { color: var(--quiet); }
  .on { color: var(--cyan); }
  .off { color: var(--quiet); }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; height: 52px; flex: 0 0 52px; }
  /* edge-state C1 — disconnect banner (B&W + dashed; not the red reserved for
     data-safety failures). ◇ glyph + ink top rule = the redesign's alert motif. */
  .dbanner { flex: 0 0 auto; display: flex; align-items: center; gap: 28px; padding: 14px 40px; border-top: 1px solid var(--ink); border-bottom: 1px dashed var(--rule-hi); background: var(--surface); }
  .dbtext { flex: 1; min-width: 0; }
  .dbhead { display: flex; align-items: baseline; gap: 12px; }
  .split { flex: 1; display: grid; grid-template-columns: 1fr 380px; min-height: 0; overflow: hidden; }
  .left { display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--rule); }
  .hero { padding: 32px 40px 28px; border-bottom: 1px solid var(--rule); }
  .herohead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  .herobig { display: flex; align-items: baseline; gap: 18px; }
  .bignum { font-size: 80px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  .herometa { flex: 1; }
  .trigger { flex: 1; display: flex; flex-direction: column; gap: 8px; align-self: center; }
  .pathinput { font-size: 12px; }
  .triggerrow { display: flex; gap: 8px; align-items: center; }
  .limitinput { width: 70px; font-size: 12px; }
  .aggwrap { margin-top: 22px; }
  .aggrow { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
  .aggbar { height: 4px; background: var(--surface-3); position: relative; }
  .aggfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--cyan); transition: width 0.2s; }
  .aggmark { position: absolute; top: -2px; width: 1px; height: 8px; background: var(--cyan); }
  .metrics { display: flex; gap: 32px; margin-top: 14px; }
  .metricval { font-family: var(--ak-mono); font-size: 18px; font-weight: 600; color: var(--ink); }
  .queue { flex: 1; padding: 14px 40px 20px; overflow: hidden; display: flex; flex-direction: column; }
  .qhead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
  .empty { padding: 30px 0; }
  .qrow { display: grid; gap: 14px; align-items: center; padding: 7px 0; border-bottom: 1px solid var(--rule); }
  .qrow.qheadrow { align-items: baseline; padding: 6px 0 8px; }
  .qrow.done { opacity: 0.55; }
  .qrows { flex: 1; overflow: auto; }
  .stage { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; padding: 2px 6px; width: fit-content; text-align: center; line-height: 1.1; color: var(--quiet); font-weight: 400; border: 1px solid var(--rule); }
  .stage.done { color: var(--ink); font-weight: 600; border-color: var(--ink); }
  .stage.running { color: var(--cyan); font-weight: 700; border-color: var(--cyan); }
  .stage.skipped { color: var(--quiet); border-style: dashed; }
  .pbar { position: relative; height: 3px; background: var(--surface-3); }
  .pfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--cyan); transition: width 0.2s; }
  .pfill.full { background: var(--quiet); }
  .right { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .loghead { padding: 16px 20px 12px; border-bottom: 1px solid var(--rule); }
  .logheadrow { display: flex; justify-content: space-between; align-items: baseline; }
  .livedot { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; color: var(--quiet); }
  .livedot.on { color: var(--cyan); }
  .logbody { flex: 1; overflow: auto; padding: 14px 20px; display: flex; flex-direction: column; gap: 7px; }
  .logline { display: flex; gap: 8px; font-family: var(--ak-mono); font-size: 10.5px; line-height: 1.35; }
  .logt { color: var(--quiet); flex: 0 0 64px; }
  .logstage { color: var(--quiet); flex: 0 0 64px; text-transform: uppercase; letter-spacing: 0.05em; }
  .logmsg { color: var(--ink-2); flex: 1; word-break: break-word; }
</style>
