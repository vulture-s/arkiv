<!-- S4 — DIT Offload, ported into the SPA (was the standalone /dit island:
     dit-offload.html, which couldn't @fontsource-bundle, had inline-only tokens,
     and lived outside the router). This route uses app.css tokens + the shared
     primitives, so it inherits dual-theme + bundled faces for free.

     Built to the LOCKED interaction draft (plan §DIT, 2026-06-15):
       1. Human gate — Preview (read-only layout) must run first; Run is the
          explicit second click. No auto-run.
       2. offload ↔ ingest is two phases — offload (copy + xxh3 verify + ascMHL)
          completes on its own, then offers "接著 ingest →" handing the destination
          to /ingest-setup. Not one button to the end.
       3. Error red is the sanctioned exception — copy/checksum failures get a red
          signal (data-safety); every other state stays the brutalist B&W (+ cyan
          for in-progress). Reverses PR #59's pure-mono rule for failures only.

     Safety: offload.py copies + verifies, NEVER deletes the source. -->
<script>
  import { push } from 'svelte-spa-router'
  import { onDestroy } from 'svelte'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { resolvedTheme } from '../lib/prefs.js'

  let src = ''
  let organize = ''
  let dsts = [''] // one or more backup destinations (one-click multi-backup)
  let includeHeic = false

  let phase = 'idle' // idle | previewing | preview | running | done
  let preview = null // {src, count, organize, files:[{source, rel, size_mb}]}
  let err = ''

  // run progress
  let curDst = ''
  let pTotal = 0
  let pDone = 0
  let pFailed = 0
  let recent = [] // [{name, status}] — last handful of files, failed flagged
  let summary = null // {dst: {verified_files, failed_files, mhl_path, status}}
  let doneCode = null
  // R5-17 (#45): a running offload used to be uncancellable — Cancel just navigated
  // away and left the fetch (and the server-side copy) running orphaned. Hold the
  // AbortController so Stop / navigate-away can drop the connection; the server
  // already terminates the copy subprocess on disconnect (offload_run GeneratorExit).
  let abortCtl = null
  let stopped = false

  $: liveDsts = dsts.map((d) => d.trim()).filter(Boolean)
  // Run is armed after a Preview; also re-armed after a Stop so the user can
  // Resume directly — the backend keeps a per-source state file and picks up from
  // the last verified file (R5-17 #19), no re-Preview needed.
  $: canRun = (phase === 'preview' || (phase === 'done' && stopped)) && liveDsts.length > 0
  $: pct = pTotal ? Math.min(100, Math.round((pDone / pTotal) * 100)) : 0
  $: anyFailed = summary ? Object.values(summary).some((s) => s.failed_files > 0) || doneCode !== 0 : false
  const base = (p) => String(p).split(/[\\/]/).pop()

  function addDst() { dsts = [...dsts, ''] }
  function removeDst(i) { dsts = dsts.filter((_, j) => j !== i); if (!dsts.length) dsts = [''] }

  async function doPreview() {
    if (!src.trim()) { err = '請先填來源路徑'; return }
    err = ''; phase = 'previewing'; preview = null
    try {
      preview = await api.offloadPreview({
        src: src.trim(), organize: organize.trim() || null, include_heic: includeHeic,
      })
      phase = 'preview'
    } catch (e) {
      err = e.body?.detail || e.message
      phase = 'idle'
    }
  }

  async function doRun() {
    if (!canRun) return
    err = ''; phase = 'running'; stopped = false
    curDst = ''; pTotal = 0; pDone = 0; pFailed = 0; recent = []; summary = null; doneCode = null
    abortCtl = new AbortController()
    try {
      const res = await api.offloadRun({
        src: src.trim(), dst: liveDsts,
        organize: organize.trim() || null, include_heic: includeHeic,
      }, { signal: abortCtl.signal })
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let nl
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1)
          if (!line) continue
          let ev; try { ev = JSON.parse(line) } catch { continue }
          if (ev.type === 'dst_start') {
            curDst = ev.dst; pTotal = ev.total; pDone = 0; pFailed = 0
          } else if (ev.type === 'file') {
            pDone++
            if (ev.status === 'failed') pFailed++
            recent = [{ name: ev.name, status: ev.status }, ...recent].slice(0, 8)
          } else if (ev.type === 'done') {
            doneCode = ev.code
            summary = ev.summary || {}
            phase = 'done'
          }
        }
      }
      if (phase !== 'done') { phase = 'done'; doneCode = doneCode ?? 1 }
    } catch (e) {
      if (stopped || e.name === 'AbortError') {
        // User stopped it (or navigated away): the copy is resumable, so this is
        // not a failure — verified files are kept and re-Run continues from here.
        phase = 'done'; doneCode = doneCode ?? 130; err = ''
      } else {
        err = e.body?.detail || e.message
        phase = 'done'; doneCode = doneCode ?? 1
      }
    } finally {
      abortCtl = null
    }
  }

  // R5-17 (#45): stop a running offload. Aborting the fetch drops the HTTP
  // connection, which trips the server's terminate-on-disconnect; the per-source
  // state file is preserved so a later Run resumes from the last verified file.
  function stopRun() {
    if (phase !== 'running' || !abortCtl) return
    if (!confirm('轉存進行中，確定停止？已複製並校驗的檔案會保留，可稍後從中斷處續傳。')) return
    stopped = true
    abortCtl.abort()
  }

  // Navigating away mid-copy must also stop the server-side copy, not orphan it.
  onDestroy(() => { if (abortCtl) abortCtl.abort() })

  // 2-phase handoff — ingest the first destination we just offloaded.
  function ingestNext() {
    const target = (summary && Object.keys(summary)[0]) || liveDsts[0]
    if (target) push(`/ingest-setup?src=${encodeURIComponent(target)}`)
  }
</script>

<div class="artboard" data-theme={$resolvedTheme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">dit · offload</Mono>
  </div>

  <div class="dialog">
    <div class="dhead">
      <Eyebrow>DIT · card → backup</Eyebrow>
      <div class="ak-display title">Offload{preview ? ` · ${preview.count} files` : ''}</div>
      <button class="esc" on:click={() => push('/')}>ESC · CANCEL</button>
    </div>

    <div class="body">
      <!-- LEFT — config -->
      <div class="col">
        <div class="field">
          <Eyebrow>Source · card / folder</Eyebrow>
          <div class="srcrow">
            <input class="ak-input" placeholder="/Volumes/CARD/DCIM  或  ~/footage" bind:value={src} spellcheck="false" on:keydown={(e) => e.key === 'Enter' && doPreview()} />
            <button class="ak-btn" on:click={doPreview} disabled={phase === 'previewing' || phase === 'running'}>{phase === 'previewing' ? 'reading…' : 'Preview'}</button>
          </div>
        </div>

        <div class="field">
          <Eyebrow>Organize template · 留空=鏡射原結構</Eyebrow>
          <input class="ak-input" placeholder="{'{date}/{camera}/{reel}'}" bind:value={organize} spellcheck="false" disabled={phase === 'running'} />
          <Mono dim style="font-size:9.5px;">tokens · {'{date} {camera} {reel} {stem} {ext}'}</Mono>
        </div>

        <div class="field">
          <Eyebrow>Destinations · one-click multi-backup</Eyebrow>
          {#each dsts as d, i}
            <div class="dstrow">
              <input class="ak-input" placeholder={`/Volumes/Backup${i + 1}`} bind:value={dsts[i]} spellcheck="false" disabled={phase === 'running'} />
              {#if i === dsts.length - 1}
                <button class="seg" on:click={addDst} disabled={phase === 'running'} title="add destination">+</button>
              {:else}
                <button class="seg" on:click={() => removeDst(i)} disabled={phase === 'running'} title="remove">−</button>
              {/if}
            </div>
          {/each}
        </div>

        <div class="field">
          <Eyebrow>Options</Eyebrow>
          <div class="optrow">
            <button class="seg" class:on={includeHeic} on:click={() => includeHeic = !includeHeic} disabled={phase === 'running'}>{includeHeic ? 'ON' : 'OFF'}</button>
            <div class="optlabel"><span>Include .heic</span><Mono dim style="font-size:10px;">連同 .heic 靜照一起轉存</Mono></div>
          </div>
        </div>
      </div>

      <!-- RIGHT — preview / progress / result -->
      <div class="col out">
        {#if phase === 'idle' || phase === 'previewing'}
          <Eyebrow>Layout preview</Eyebrow>
          <div class="empty"><Mono dim>{phase === 'previewing' ? '讀取中…' : '填來源後按 Preview（只讀、不複製）'}</Mono></div>
        {:else if phase === 'preview'}
          <Eyebrow>Layout · {preview.organize ? `organize ${preview.organize}` : '鏡射原結構'}</Eyebrow>
          <Mono dim style="font-size:10px;">{base(preview.src)} · {preview.count} files{preview.count >= 200 ? ' · 顯示前 200' : ''}</Mono>
          <div class="rowsbox">
            {#each preview.files as f}
              <div class="prow">
                <Mono style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{base(f.source)}</Mono>
                <span class="arr">→</span>
                <Mono dim style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{f.rel}</Mono>
                <Mono dim style="font-size:10px;">{f.size_mb ?? '—'}</Mono>
              </div>
            {/each}
          </div>
        {:else}
          <!-- running / done -->
          <Eyebrow>{phase === 'running' ? 'Copying · verify + MHL' : (stopped ? 'Offload — stopped' : (anyFailed ? 'Offload — failed' : 'Offload — complete'))}</Eyebrow>
          {#if curDst}<Mono dim style="font-size:10px;">→ {curDst}</Mono>{/if}
          <div class="bar"><div class="barfill" class:fail={anyFailed} style="width:{phase === 'done' ? 100 : pct}%;"></div></div>
          <Mono dim style="font-size:10.5px;">{pDone}{pTotal ? `/${pTotal}` : ''} files{pFailed ? ` · ${pFailed} failed` : ''}</Mono>

          <div class="rowsbox">
            {#each recent as r}
              <div class="prow file" class:fail={r.status === 'failed'}>
                <Mono style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{r.name}</Mono>
                <span class="st {r.status}">{r.status === 'failed' ? 'FAIL' : r.status === 'skipped' ? 'SKIP' : 'OK'}</span>
              </div>
            {/each}
          </div>

          {#if phase === 'done' && summary}
            <div class="summary">
              {#each Object.entries(summary) as [dst, s]}
                <div class="srow" class:fail={s.failed_files > 0}>
                  <Mono style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{base(dst)}</Mono>
                  <Mono dim style="font-size:10px;">{s.verified_files} ok{s.failed_files ? ` · ${s.failed_files} fail` : ''}{s.mhl_path ? ' · MHL' : ''}</Mono>
                </div>
              {/each}
            </div>
          {/if}
        {/if}

        <div class="noticebox">
          <Mono dim style="font-size:10px;">◇ Copy + xxh3 verify + ascMHL. Never deletes the source card.</Mono>
        </div>
      </div>
    </div>

    <div class="footer">
      {#if err}<Mono style="font-size:11px;" class="errtext">{err}</Mono>
      {:else if phase === 'done' && stopped}<Mono style="font-size:11px;">■ 已停止 · 已校驗檔案保留，可續傳</Mono>
      {:else if phase === 'done'}<Mono style="font-size:11px;" class={anyFailed ? 'errtext' : ''}>{anyFailed ? `✗ 轉存有失敗 (exit ${doneCode})` : `✓ 轉存完成 (exit ${doneCode})`}</Mono>{/if}
      <div class="grow"></div>
      {#if phase === 'done' && !anyFailed}
        <button class="ak-btn" on:click={ingestNext}>接著 ingest →</button>
      {/if}
      {#if phase === 'running'}
        <button class="ak-btn stopbtn" on:click={stopRun}>停止</button>
      {:else}
        <button class="ak-btn" on:click={() => push('/')}>{phase === 'done' ? 'Done' : 'Cancel'}</button>
      {/if}
      <button class="ak-btn ak-btn--primary" on:click={doRun} disabled={!canRun}>{phase === 'running' ? 'copying…' : (stopped && phase === 'done' ? 'Resume →' : 'Run offload →')}</button>
    </div>
  </div>
</div>

<style>
  /* error red — the LOCKED exception to the B&W palette, failures only */
  .artboard { --danger: #e0563a; width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }

  .dialog { margin: 40px auto; width: 1080px; border: 1px solid var(--rule-hi); background: var(--surface); display: grid; grid-template-rows: auto 1fr auto; min-height: 0; max-height: calc(900px - 80px); }
  .dhead { display: flex; align-items: baseline; gap: 20px; padding: 22px 28px; border-bottom: 1px solid var(--rule); }
  .title { font-size: 28px; letter-spacing: -0.03em; line-height: 1; }
  .esc { margin-left: auto; font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--quiet); background: none; border: none; cursor: pointer; }
  .esc:hover { color: var(--ink); }

  .body { display: grid; grid-template-columns: 1fr 360px; min-height: 0; overflow: hidden; }
  .col { padding: 24px 28px; display: flex; flex-direction: column; gap: 22px; overflow: auto; }
  .out { border-left: 1px solid var(--rule); gap: 10px; }

  .field { display: flex; flex-direction: column; gap: 8px; }
  .srcrow { display: flex; gap: 10px; align-items: flex-end; }
  .dstrow { display: flex; gap: 8px; align-items: center; }

  .optrow { display: flex; align-items: center; gap: 14px; }
  .optlabel { display: flex; flex-direction: column; gap: 1px; font-size: 12px; }
  .seg { font-family: var(--ak-mono); font-size: 11px; letter-spacing: 0.08em; width: 46px; flex: 0 0 46px; padding: 6px 0; border: 1px solid var(--rule-hi); background: transparent; color: var(--quiet); cursor: pointer; text-align: center; }
  .seg.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); font-weight: 700; }
  .seg:disabled { opacity: 0.4; cursor: not-allowed; }

  .empty { padding: 24px 0; }
  .rowsbox { flex: 1; min-height: 0; overflow: auto; border-top: 1px solid var(--rule); margin-top: 2px; }
  .prow { display: grid; grid-template-columns: 1fr auto 1.1fr auto; gap: 8px; align-items: baseline; padding: 4px 0; border-bottom: 1px solid var(--surface-2); }
  .prow.file { grid-template-columns: 1fr auto; }
  .arr { color: var(--quiet); font-family: var(--ak-mono); font-size: 10px; }
  .st { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.06em; padding: 1px 6px; border: 1px solid var(--rule); color: var(--quiet); }
  .st.verified { color: var(--ink); border-color: var(--ink); }
  .st.skipped { color: var(--quiet); border-style: dashed; }
  .st.failed { color: var(--danger); border-color: var(--danger); font-weight: 700; }
  .prow.fail :global(*) { color: var(--danger); }

  .bar { height: 6px; background: var(--surface-2); position: relative; margin-top: 4px; }
  .barfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--cyan); transition: width 0.2s; }
  .barfill.fail { background: var(--danger); }

  .summary { border-top: 1px solid var(--rule); padding-top: 8px; display: flex; flex-direction: column; gap: 4px; }
  .srow { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
  .srow.fail :global(*) { color: var(--danger); }
  .noticebox { margin-top: auto; border: 1px dashed var(--rule-hi); padding: 10px 12px; line-height: 1.5; }

  .footer { display: flex; align-items: center; gap: 12px; padding: 16px 28px; border-top: 1px solid var(--rule); }
  .footer :global(.errtext) { color: var(--danger); }
  .stopbtn { border-color: var(--cyan); color: var(--cyan); }
</style>
