<!-- S1b — Ingest setup dialog, built to the redesign SSOT
     (docs/design/redesign-2026 op-01 · "B1 · Ingest setup dialog").
     Wired to the S1a backend: /api/ingest/scan → MANIFEST panel, and
     /api/ingest/ws with the engine options surfaced in S1a brick 1.

     Honest scope (per the design↔build reconcile): the offload half of op-01
     (COPY/MOVE/LINK · destination · on-conflict) and the model/language/tag-pool
     pickers + time estimates have NO backend yet (plan S1a brick 4) — they are
     shown as disabled "picker pending" rows, never faked. Everything enabled here
     is fully wired to a real endpoint. -->
<script>
  import { push } from 'svelte-spa-router'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'

  let path = ''
  let limit = 0
  // engine flags surfaced by S1a brick 1
  let opts = { skip_vision: false, refresh: false, recursive: false, skip_failed: false, no_embed: false }
  let maxFailures = 0

  let manifest = null      // {video,audio,unsupported,total_size_mb}
  let total = 0, fresh = 0
  let scanning = false, starting = false, err = '', notice = ''

  $: gb = manifest ? (manifest.total_size_mb / 1024).toFixed(1) : null

  async function scan() {
    if (!path.trim()) { err = '請先填來源資料夾路徑'; return }
    err = ''; notice = ''; scanning = true; manifest = null
    try {
      const d = await api.scanMedia(path.trim())
      manifest = d.manifest; total = d.total; fresh = d.new
      if (total === 0) notice = '這個資料夾沒有可匯入的媒體檔。'
    } catch (e) { err = e.message } finally { scanning = false }
  }

  async function start() {
    if (!path.trim()) { err = '需要來源資料夾'; return }
    err = ''; starting = true
    try {
      const body = { ...opts }
      if (maxFailures > 0) body.max_failures = Number(maxFailures)
      await api.ingestWs(path.trim(), Number(limit) || 0, body)
      // hand off to the live progress route (the WS is already broadcasting)
      push('/ingest-live')
    } catch (e) { err = e.message; starting = false }
  }

  const TOGGLES = [
    ['skip_vision', 'Skip vision', '跳過 AI 視覺標註（只轉錄）'],
    ['refresh', 'Refresh', '重抽已索引檔（含 360 reproject 重套）'],
    ['recursive', 'Recursive', '遞迴掃子資料夾'],
    ['skip_failed', 'Tolerate vision fails', '零星幀失敗不中斷（過夜跑）'],
    ['no_embed', 'Skip index rebuild', '匯入後不自動重建向量索引'],
  ]
</script>

<div class="artboard" data-theme="dark">
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">ingest · setup</Mono>
  </div>

  <div class="dialog">
    <div class="dhead">
      <Eyebrow>Step 1 / 1 · configure</Eyebrow>
      <div class="ak-display title">{total ? `Ingest ${fresh} files` : 'Ingest media'}{gb ? ` · ${gb} GB` : ''}</div>
      <button class="esc" on:click={() => push('/')}>ESC · CANCEL</button>
    </div>

    <div class="body">
      <!-- LEFT — config -->
      <div class="col">
        <div class="field">
          <Eyebrow>Source · folder</Eyebrow>
          <div class="srcrow">
            <input class="ak-input" placeholder="/Volumes/CARD/DCIM  或  ~/footage" bind:value={path} spellcheck="false" on:keydown={(e)=>e.key==='Enter'&&scan()} />
            <button class="ak-btn" on:click={scan} disabled={scanning}>{scanning ? 'scanning…' : 'Scan'}</button>
          </div>
        </div>

        <div class="field">
          <Eyebrow>Ingest options</Eyebrow>
          {#each TOGGLES as [key, label, hint]}
            <div class="optrow">
              <button class="seg" class:on={opts[key]} on:click={() => opts[key] = !opts[key]}>{opts[key] ? 'ON' : 'OFF'}</button>
              <div class="optlabel"><span>{label}</span><Mono dim style="font-size:10px;">{hint}</Mono></div>
            </div>
          {/each}
          <div class="optrow">
            <input class="ak-input num" type="number" min="0" bind:value={maxFailures} title="max-failures (0 = halt on first)" />
            <div class="optlabel"><span>Max vision failures</span><Mono dim style="font-size:10px;">累計幾個幀失敗才停（0 = 第一個就停）</Mono></div>
          </div>
          <div class="optrow">
            <input class="ak-input num" type="number" min="0" bind:value={limit} title="limit (0 = all)" />
            <div class="optlabel"><span>Limit</span><Mono dim style="font-size:10px;">只處理前 N 個（0 = 全部）</Mono></div>
          </div>
        </div>

        <!-- design sections with no backend picker yet — shown, not faked -->
        <div class="field deferred">
          <Eyebrow>Transcribe · whisper</Eyebrow>
          <div class="pendrow"><Mono dim>model · languages · skip-if</Mono><span class="pend">picker pending · brick 4</span></div>
          <Eyebrow>Vision tagging · ollama</Eyebrow>
          <div class="pendrow"><Mono dim>model · tag pool · frames</Mono><span class="pend">picker pending · brick 4</span></div>
        </div>
      </div>

      <!-- RIGHT — manifest -->
      <div class="col manifest">
        <Eyebrow>Manifest</Eyebrow>
        {#if !manifest}
          <div class="empty"><Mono dim>{scanning ? '掃描中…' : '填路徑後按 Scan 看清單'}</Mono></div>
        {:else}
          <div class="mtotal"><Mono>{total} files · {gb} GB</Mono></div>
          <div class="mrow"><span>Video</span><Mono dim>{manifest.video.count}</Mono><Mono dim>{manifest.video.size_mb} MB</Mono></div>
          <div class="mrow"><span>Audio</span><Mono dim>{manifest.audio.count}</Mono><Mono dim>{manifest.audio.size_mb} MB</Mono></div>
          {#if manifest.unsupported.count}
            <div class="mrow skip"><span>Unsupp.</span><Mono dim>{manifest.unsupported.count}</Mono><Mono dim>skipped</Mono></div>
          {/if}
          <div class="estimated"><Eyebrow>Estimated</Eyebrow><span class="pend">timing pending · brick 4</span></div>
        {/if}
        <div class="noticebox">
          <Mono dim style="font-size:10px;">◇ Notice<br/>Files are processed locally. Nothing leaves this machine. Offload never deletes the source.</Mono>
        </div>
      </div>
    </div>

    <div class="footer">
      {#if err}<Mono style="font-size:11px;color:var(--cyan);">{err}</Mono>{:else if notice}<Mono dim style="font-size:11px;">{notice}</Mono>{/if}
      <div class="grow"></div>
      <button class="ak-btn" on:click={() => push('/')}>Cancel</button>
      <button class="ak-btn ak-btn--primary" on:click={start} disabled={starting || total === 0}>{starting ? 'starting…' : 'Start ingest →'}</button>
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }

  .dialog { margin: 40px auto; width: 1080px; border: 1px solid var(--rule-hi); background: var(--surface); display: grid; grid-template-rows: auto 1fr auto; min-height: 0; }
  .dhead { display: flex; align-items: baseline; gap: 20px; padding: 22px 28px; border-bottom: 1px solid var(--rule); }
  .title { font-size: 28px; letter-spacing: -0.03em; line-height: 1; }
  .esc { margin-left: auto; font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--quiet); background: none; border: none; cursor: pointer; }
  .esc:hover { color: var(--ink); }

  .body { display: grid; grid-template-columns: 1fr 320px; min-height: 0; overflow: auto; }
  .col { padding: 24px 28px; display: flex; flex-direction: column; gap: 22px; }
  .manifest { border-left: 1px solid var(--rule); gap: 12px; }

  .field { display: flex; flex-direction: column; gap: 10px; }
  .srcrow { display: flex; gap: 10px; align-items: flex-end; }
  .num { width: 80px; flex: 0 0 80px; }

  .optrow { display: flex; align-items: center; gap: 14px; }
  .optlabel { display: flex; flex-direction: column; gap: 1px; font-size: 12px; }
  .seg { font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.08em; width: 46px; flex: 0 0 46px; padding: 5px 0; border: 1px solid var(--rule-hi); background: transparent; color: var(--quiet); cursor: pointer; }
  .seg.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); font-weight: 700; }

  .deferred { opacity: 0.6; }
  .pendrow, .estimated { display: flex; align-items: baseline; justify-content: space-between; }
  .pend { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--quiet-2); border: 1px dashed var(--rule-hi); padding: 1px 6px; }

  .empty { padding: 24px 0; }
  .mtotal { padding-bottom: 8px; border-bottom: 1px solid var(--rule); margin-bottom: 4px; }
  .mrow { display: grid; grid-template-columns: 1fr auto auto; gap: 14px; padding: 5px 0; font-size: 12px; align-items: baseline; }
  .mrow.skip { color: var(--quiet); }
  .estimated { margin-top: 10px; }
  .noticebox { margin-top: auto; border: 1px dashed var(--rule-hi); padding: 12px; line-height: 1.5; }

  .footer { display: flex; align-items: center; gap: 12px; padding: 16px 28px; border-top: 1px solid var(--rule); }
</style>
