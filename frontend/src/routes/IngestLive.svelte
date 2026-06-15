<!-- B1+ / S1b — ingest progress wired to the live backend.
     Connects ws://…/ws/ingest, triggers POST /api/ingest/ws, renders the real
     broadcast stream. Since S1a brick 3 the protocol carries real per-stage
     events ({type:"stage", stage, index, counts}, stage ∈ probe/transcribe/
     thumbnail/frames/vision) on top of per-file start/done/skipped + start/
     complete — so the queue shows each file's live stage + a real aggregate
     stage breakdown. No faked sub-stages: every segment is a backend event. -->
<script>
  import { onMount, onDestroy } from 'svelte'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'

  const theme = 'dark'
  const BASE = import.meta.env?.VITE_API_URL ?? ''

  let ws = null
  let conn = 'connecting' // connecting | open | closed
  let path = ''
  let limit = 3
  let total = 0
  let files = {} // index → {filename, status, stage}
  let stageCounts = {} // aggregate per-stage tally from the backend (counts dict)
  let complete = null // {ok, skipped, failed}

  // Canonical pipeline order (matches ingest.py _stage calls). probe→frames run
  // in phase 1 (file ends "done"); vision streams in a second phase-2 pass, so a
  // file can receive a vision event after it already read "done" — handled below.
  const STAGES = ['probe', 'transcribe', 'thumbnail', 'frames', 'vision']
  const stageLabel = { probe: 'PROBE', transcribe: 'WHISPER', thumbnail: 'THUMB', frames: 'FRAMES', vision: 'VISION' }
  let log = [] // [{t, text}]
  let busy = false
  let rebuilding = false
  let err = ''

  const wsUrl = () => {
    // /ws/ingest requires ingest_write; a WebSocket can't send an Authorization
    // header, so carry the token as ?token= when one is set (direct remote
    // deployment). No-op on loopback / behind the dev proxy.
    const base = BASE
      ? BASE.replace(/^http/, 'ws') + '/ws/ingest'
      : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/ingest`
    return api.appendToken(base)
  }

  function pushLog(text) {
    const t = new Date().toLocaleTimeString()
    log = [{ t, text }, ...log].slice(0, 40)
  }

  function connect() {
    try {
      ws = new WebSocket(wsUrl())
    } catch (e) {
      conn = 'closed'
      err = String(e)
      return
    }
    ws.onopen = () => { conn = 'open' }
    ws.onclose = () => { conn = 'closed' }
    ws.onerror = () => { conn = 'closed' }
    ws.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.type === 'start') {
        total = msg.total || 0
        files = {}
        stageCounts = {}
        complete = null
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
        pushLog(`[${msg.index}/${msg.total}] ${msg.filename} >${msg.stage}`)
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

  onMount(connect)
  onDestroy(() => ws && ws.close())

  $: rows = Object.entries(files)
    .map(([i, f]) => ({ index: Number(i), ...f }))
    .sort((a, b) => a.index - b.index)
  $: doneCount = rows.filter((r) => r.status === 'done').length
  $: pct = total ? Math.round((doneCount / total) * 100) : 0
  // The aggregate stage breakdown strip — only stages the backend has reported.
  $: stageStrip = STAGES.filter((s) => stageCounts[s]).map((s) => ({ s, n: stageCounts[s] }))
  const statusText = { transcribing: 'RUN', done: 'OK', skipped: 'SKIP' }

  // Reached index in the canonical pipeline for a row: -1 (none) up to 4 (vision).
  // A "done" file (phase 1 complete) has cleared probe→frames even if its last
  // event was an earlier stage; vision only lights up once its own event lands.
  function reached(r) {
    let idx = r.stage ? STAGES.indexOf(r.stage) : -1
    if (r.status === 'done' && idx < 3) idx = 3 // probe..frames done in phase 1
    return idx
  }
  function badge(r) {
    if (r.status === 'done') return 'OK'
    if (r.status === 'skipped') return 'SKIP'
    if (r.stage) return stageLabel[r.stage] || r.stage.toUpperCase()
    return statusText[r.status] || r.status
  }
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">ws://…/ws/ingest · <span class:on={conn === 'open'} class:off={conn !== 'open'}>{conn.toUpperCase()}</span></Mono>
  </div>

  <div class="split">
    <div class="left">
      <div class="hero">
        <div class="herohead">
          <Eyebrow>Ingest · live websocket</Eyebrow>
          <Mono dim style="font-size:10.5px;">real /ws/ingest stream</Mono>
        </div>
        <div class="bignum-row">
          <div class="ak-display bignum">{doneCount}<span class="quiet">/{total || '—'}</span></div>
          <div class="trigger">
            <input class="ak-input pathinput" placeholder="ingest 資料夾路徑（例 ~/Desktop/Test）" bind:value={path} disabled={busy} />
            <div class="triggerrow">
              <input class="ak-input limitinput" type="number" min="0" bind:value={limit} disabled={busy} title="limit (0=all)" />
              <button class="ak-btn ak-btn--primary" on:click={trigger} disabled={busy || conn !== 'open'}>{busy ? 'running…' : 'Start ingest →'}</button>
              <button class="ak-btn" on:click={rebuildIndex} disabled={rebuilding || busy} title="重建 ChromaDB 向量索引（背景執行）— ingest 後或換 embedding 模型後使用">{rebuilding ? '排入中…' : '重建索引'}</button>
            </div>
          </div>
        </div>
        {#if err}<Mono style="font-size:11px;color:var(--cyan);">{err}</Mono>{/if}
        <div class="aggbar"><div class="aggfill" style="width:{pct}%;"></div></div>
        {#if stageStrip.length}
          <div class="stagestrip">
            {#each stageStrip as { s, n }}
              <span class="stagechip"><span class="sname">{stageLabel[s]}</span><span class="snum">{n}</span></span>
            {/each}
          </div>
        {/if}
      </div>

      <div class="queue">
        <div class="qhead">
          <Eyebrow>Queue {#if total}· {total} files{/if}</Eyebrow>
          {#if complete}<Mono dim style="font-size:10.5px;">done · ok={complete.ok} skip={complete.skipped} fail={complete.failed}</Mono>{/if}
        </div>
        {#if rows.length === 0}
          <div class="empty"><Mono dim>等待 ingest 觸發…（填路徑按 Start）</Mono></div>
        {:else}
          {#each rows as r (r.index)}
            <div class="qrow" class:done={r.status === 'done'}>
              <Mono dim style="font-size:10px;flex:0 0 36px;">{r.index}/{total}</Mono>
              <Mono style="font-size:11.5px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{r.filename}</Mono>
              {#if r.status !== 'skipped'}
                <div class="pipe" title={STAGES.join(' → ')}>
                  {#each STAGES as st, si}
                    <span class="seg" class:fill={si <= reached(r)} class:active={r.stage === st && r.status !== 'done'} title={stageLabel[st]}></span>
                  {/each}
                </div>
              {/if}
              <span class="stage {r.status}" class:running={r.status === 'transcribing'}>{badge(r)}</span>
            </div>
          {/each}
        {/if}
      </div>
    </div>

    <div class="right">
      <div class="loghead">
        <Eyebrow>Live log · ws</Eyebrow>
        <span class="livedot" class:on={conn === 'open'}>● {conn === 'open' ? 'LIVE' : conn.toUpperCase()}</span>
      </div>
      <div class="logbody">
        {#each log as ln}
          <div class="logline"><span class="logt">{ln.t}</span><span class="logmsg">{ln.text}</span></div>
        {/each}
      </div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .quiet { color: var(--quiet); }
  .on { color: var(--cyan); }
  .off { color: var(--quiet); }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .split { display: grid; grid-template-columns: 1fr 380px; min-height: 0; overflow: hidden; }
  .left { display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--rule); }
  .hero { padding: 32px 40px 24px; border-bottom: 1px solid var(--rule); }
  .herohead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 16px; }
  .bignum-row { display: flex; align-items: flex-start; gap: 24px; }
  .bignum { font-size: 72px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  .trigger { flex: 1; display: flex; flex-direction: column; gap: 8px; padding-top: 8px; }
  .pathinput { font-size: 12px; }
  .triggerrow { display: flex; gap: 8px; align-items: center; }
  .limitinput { width: 70px; font-size: 12px; }
  .aggbar { height: 4px; background: var(--surface-3); position: relative; margin-top: 22px; }
  .aggfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--cyan); transition: width 0.2s; }
  .stagestrip { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .stagechip { display: inline-flex; align-items: center; gap: 6px; font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.08em; padding: 2px 7px; border: 1px solid var(--rule); color: var(--quiet); }
  .stagechip .sname { color: var(--ink-2); }
  .stagechip .snum { color: var(--cyan); font-weight: 700; }
  .queue { flex: 1; padding: 14px 40px 20px; overflow: auto; }
  .qhead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
  .empty { padding: 30px 0; }
  .qrow { display: flex; align-items: center; gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--rule); }
  .qrow.done { opacity: 0.6; }
  .pipe { display: flex; gap: 3px; flex: 0 0 auto; }
  .seg { width: 14px; height: 4px; background: var(--surface-3); transition: background 0.2s; }
  .seg.fill { background: var(--ink-2); }
  .seg.active { background: var(--cyan); animation: segpulse 1s ease-in-out infinite; }
  @keyframes segpulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
  .stage { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; padding: 2px 6px; border: 1px solid var(--rule); color: var(--quiet); flex: 0 0 auto; min-width: 52px; text-align: center; }
  .stage.running { color: var(--cyan); border-color: var(--cyan); font-weight: 700; }
  .stage.done { color: var(--ink); border-color: var(--ink); font-weight: 600; }
  .stage.skipped { color: var(--quiet); border-style: dashed; }
  .right { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .loghead { padding: 16px 20px 12px; border-bottom: 1px solid var(--rule); display: flex; justify-content: space-between; align-items: baseline; }
  .livedot { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; color: var(--quiet); }
  .livedot.on { color: var(--cyan); }
  .logbody { flex: 1; overflow: auto; padding: 14px 20px; display: flex; flex-direction: column; gap: 7px; }
  .logline { display: flex; gap: 8px; font-family: var(--ak-mono); font-size: 10.5px; line-height: 1.35; }
  .logt { color: var(--quiet); flex: 0 0 64px; }
  .logmsg { color: var(--ink-2); flex: 1; word-break: break-word; }
</style>
