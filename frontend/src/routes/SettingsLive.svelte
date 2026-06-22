<!-- Settings, ported to a live route (was mock-only Settings.svelte → /_design/settings).
     Honest scope — only what's genuinely backed is interactive:
       · Appearance · Theme — REAL: writes the prefs store (localStorage), re-themes
         the whole product live (app.css ships dark + light token sets).
       · UI scale / Type density — shown DISABLED: the app is px-based on a fixed
         1400×900 artboard, so a root font-size / density class has no effect.
         Faking a working control would violate the evidence discipline.
       · Engine (transcription / vision / export) — read-only current behaviour;
         model/format pickers have no API yet (plan brick 4), marked pending.
       · System — REAL: version + backend reachability + library totals + disk,
         from /api/stats. -->
<script>
  import { onMount } from 'svelte'
  import { push } from 'svelte-spa-router'
  import * as api from '../lib/api.js'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { themePref, resolvedTheme } from '../lib/prefs.js'

  const VERSION = 'v0.9.2'
  let section = 'appearance' // appearance | vocab | engine | system
  const nav = [
    ['appearance', 'Appearance'],
    ['vocab', 'Vocabulary'],
    ['engine', 'Engine'],
    ['system', 'System · about'],
  ]
  const themeOpts = [['dark', 'Dark'], ['light', 'Light'], ['system', 'System']]

  // System panel — real backend state.
  let sys = 'loading' // loading | ok | error
  let stats = null
  async function loadSystem() {
    sys = 'loading'
    try { stats = await api.getStats(); sys = 'ok' } catch { sys = 'error' }
  }

  // Editing proxies — status + background build (no completion signal, so we
  // re-poll the status a couple of times after kicking a build off).
  let proxy = null // {total, proxied, size_mb}
  let proxyMsg = ''
  let proxyBusy = false
  async function loadProxy() {
    try { proxy = await api.getProxyStatus() } catch { proxy = null }
  }
  async function runProxyBuild() {
    if (proxyBusy) return
    proxyBusy = true
    proxyMsg = ''
    try {
      const r = await api.buildProxies()
      proxyMsg = r.message || `已排入 ${r.queued} 個`
      setTimeout(loadProxy, 2000)
      setTimeout(loadProxy, 8000)
    } catch (e) {
      proxyMsg = `失敗: ${e.message}`
    } finally {
      proxyBusy = false
    }
  }

  // Analytics breakdowns (real /api/duration-by-lang + /api/size-by-ext)
  let durLang = null // [{lang, total_s, count}]
  let sizeExt = null // [{ext, total_mb, count}]
  async function loadAnalytics() {
    try { durLang = await api.durationByLang() } catch { durLang = null }
    try { sizeExt = await api.sizeByExt() } catch { sizeExt = null }
  }
  // Cache management (info + targeted clear)
  let cache = null // {caches:{name:{size_mb,count?}}, total_mb}
  let cacheMsg = ''
  let cacheBusy = false
  async function loadCache() {
    try { cache = await api.cacheInfo() } catch { cache = null }
  }
  async function runClearCache(target) {
    if (cacheBusy) return
    cacheBusy = true; cacheMsg = ''
    try {
      const r = await api.clearCache(target)
      cacheMsg = r.message || `已清除 ${target}`
      setTimeout(loadCache, 800)
    } catch (e) { cacheMsg = `失敗: ${e.message}` } finally { cacheBusy = false }
  }

  // Correction dictionary (Phase 9.6) — one per-project dictionary, two paths:
  // pre-rules feed the Whisper hotword list; post-rules batch-rewrite stored
  // transcripts (recorrect). Editor here writes .arkiv/corrections.json.
  let rules = [] // [{from,to,scope,pre,post}]
  let vocabMsg = ''
  let vocabBusy = false
  let preview = null // dry-run result {media_affected,total_hits,rules:[{from,to,scope,hits}]}
  let applyResult = null // {media_updated,total_hits,backup,embed_rebuild_started}
  let backups = []
  let doRebuild = false
  const normRule = (r) => ({ from: r.from || '', to: r.to || '', scope: r.scope || 'global', pre: !!r.pre, post: r.post !== false })
  async function loadVocab() {
    try { rules = ((await api.getCorrections()).rules || []).map(normRule) } catch { rules = [] }
    try { backups = (await api.getRecorrectBackups()).backups || [] } catch { backups = [] }
  }
  function addRule() { rules = [...rules, { from: '', to: '', scope: 'global', pre: false, post: true }]; preview = null }
  function removeRule(i) { rules = rules.filter((_, j) => j !== i); preview = null }
  async function saveVocab() {
    if (vocabBusy) return
    vocabBusy = true; vocabMsg = ''
    try {
      const res = await api.putCorrections(rules.filter((r) => (r.from || '').trim()))
      rules = (res.rules || []).map(normRule)
      vocabMsg = `已存 ${res.count} 條規則`
      preview = null
    } catch (e) { vocabMsg = `失敗: ${e.message}` } finally { vocabBusy = false }
  }
  async function runPreview() {
    if (vocabBusy) return
    vocabBusy = true; vocabMsg = ''; applyResult = null
    try { preview = await api.recorrectPreview() }
    catch (e) { vocabMsg = `預覽失敗: ${e.message}` } finally { vocabBusy = false }
  }
  async function runApply() {
    if (vocabBusy) return
    vocabBusy = true; vocabMsg = ''
    try {
      applyResult = await api.recorrectApply(doRebuild)
      preview = null
      backups = (await api.getRecorrectBackups()).backups || []
      vocabMsg = `已套用：更新 ${applyResult.media_updated} 筆、${applyResult.total_hits} 處替換`
    } catch (e) { vocabMsg = `套用失敗: ${e.message}` } finally { vocabBusy = false }
  }
  async function runRevert() {
    if (vocabBusy) return
    vocabBusy = true; vocabMsg = ''
    try {
      const r = await api.recorrectRevert()
      applyResult = null
      vocabMsg = r.error ? `還原失敗: ${r.error}` : `已還原 ${r.restored} 筆（${r.backup}）`
      backups = (await api.getRecorrectBackups()).backups || []
    } catch (e) { vocabMsg = `還原失敗: ${e.message}` } finally { vocabBusy = false }
  }

  onMount(() => { loadSystem(); loadProxy(); loadAnalytics(); loadCache(); loadVocab() })

  const gb = (n) => (n == null ? '—' : n >= 1000 ? `${(n / 1000).toFixed(1)} TB` : `${Math.round(n)} GB`)
  const mb = (n) => (n == null ? '—' : n >= 1000 ? `${(n / 1000).toFixed(1)} GB` : `${Math.round(n)} MB`)
  const hms = (s) => {
    s = Math.round(s || 0)
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
    return h ? `${h}h ${m}m` : `${m}m`
  }
  $: disk = stats?.disk ?? null
  $: cacheRows = cache?.caches ? Object.entries(cache.caches) : []
</script>

<div class="artboard" data-theme={$resolvedTheme}>
  <div class="scrim"></div>
  <div class="modal">
    <div class="mhead">
      <div class="mtitle">
        <Eyebrow style="color:var(--ink-2);">Settings</Eyebrow>
        <div class="ak-display mtitlebig">Preferences</div>
      </div>
      <button class="mclose" on:click={() => push('/')}>ESC · CLOSE</button>
    </div>

    <div class="mbody">
      <nav class="mnav">
        {#each nav as [id, label]}
          <button class="navbtn" class:active={section === id} on:click={() => (section = id)}>{label}</button>
        {/each}
      </nav>

      <div class="form">
        {#if section === 'appearance'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">THEME · INTERFACE</Eyebrow>
              <div class="ak-display fstitle">Appearance</div>
              <div class="fsdesc">vulture.s editorial. Theme applies across the whole app and persists. System follows your OS.</div>
            </div>
            <div class="frows">
              <div class="frow">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Theme</Mono>
                <div class="seg">
                  {#each themeOpts as [id, label], i}
                    {#if i > 0}<div class="segsep"></div>{/if}
                    <button class="segbtn" class:on={$themePref === id} on:click={() => themePref.set(id)}>{label}</button>
                  {/each}
                </div>
              </div>
              <div class="frow disabled">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">UI scale</Mono>
                <span class="pend">px-layout · not adjustable yet</span>
              </div>
              <div class="frow disabled">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Type density</Mono>
                <span class="pend">px-layout · not adjustable yet</span>
              </div>
            </div>
          </section>
        {:else if section === 'vocab'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">CORRECTION DICTIONARY · 校正字典</Eyebrow>
              <div class="ak-display fstitle">Vocabulary</div>
              <div class="fsdesc">一本 per-project 字典，兩條路徑：<b>pre</b> 把 <code>to</code> 詞餵 Whisper hotword（轉錄前防聽錯）；<b>post</b> 把 <code>from→to</code> 套到已存逐字稿（批次校正，秒級修整庫搜尋召回、不碰音訊）。寫入 <code>.arkiv/corrections.json</code>。</div>
            </div>

            <div class="vrules">
              <div class="vhead"><span>FROM</span><span>TO</span><span>SCOPE</span><span>PRE</span><span>POST</span><span></span></div>
              {#each rules as r, i}
                <div class="crow">
                  <input class="vin" bind:value={r.from} placeholder="富田" />
                  <input class="vin" bind:value={r.to} placeholder="Furutech" />
                  <select class="vsel" bind:value={r.scope}>
                    <option value="global">global</option>
                    <option value="word">word</option>
                    <option value="line">line</option>
                  </select>
                  <input class="vcb" type="checkbox" bind:checked={r.pre} title="餵 hotword（轉錄前）" />
                  <input class="vcb" type="checkbox" bind:checked={r.post} title="套已存逐字稿（轉錄後）" />
                  <button class="vx" on:click={() => removeRule(i)} title="移除">✕</button>
                </div>
              {/each}
              {#if !rules.length}<div class="vempty">尚無規則 — 點下方「新增規則」。</div>{/if}
            </div>

            <div class="vctl">
              <button class="ak-btn" on:click={addRule}>+ 新增規則</button>
              <button class="ak-btn" on:click={saveVocab} disabled={vocabBusy}>儲存字典</button>
            </div>

            <div class="vdiv"></div>

            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">BATCH RECORRECT · 批次套用</Eyebrow>
              <div class="fsdesc">把 <b>post</b> 規則套到整個專案已存的逐字稿（transcript + 時間軸 segments 同步改）。<b>預覽不寫入</b>；套用前自動備份，可還原。</div>
            </div>
            <div class="vctl">
              <button class="ak-btn" on:click={runPreview} disabled={vocabBusy}>預覽命中</button>
              <label class="vchk"><input class="vcb" type="checkbox" bind:checked={doRebuild} /> 套用後重建向量索引</label>
              <button class="ak-btn" on:click={runApply} disabled={vocabBusy || !preview || !preview.total_hits}>套用校正</button>
              {#if backups.length}<button class="ak-btn" on:click={runRevert} disabled={vocabBusy}>還原最近一次</button>{/if}
            </div>

            {#if preview}
              <div class="vresult">
                <Mono style="font-size:12px;color:var(--ink);">預覽：{preview.media_affected} 筆素材 · {preview.total_hits} 處替換</Mono>
                {#each preview.rules.filter((x) => x.hits) as x}
                  <Mono dim style="font-size:11px;">· {x.from} → {x.to || '(刪除)'} [{x.scope}] ×{x.hits}</Mono>
                {/each}
                {#if !preview.total_hits}<Mono dim style="font-size:11px;">沒有命中（post 關閉、或無 transcript 含這些詞）。先「儲存字典」再預覽。</Mono>{/if}
              </div>
            {/if}
            {#if applyResult}
              <div class="vresult">
                <Mono style="font-size:12px;color:var(--ink);">✓ 已套用：更新 {applyResult.media_updated} 筆 · {applyResult.total_hits} 處{applyResult.embed_rebuild_started ? ' · 向量索引重建中' : ''}</Mono>
                {#if applyResult.backup}<Mono dim style="font-size:11px;">備份 {applyResult.backup} — 可還原</Mono>{/if}
              </div>
            {/if}
            {#if vocabMsg}<div class="vmsg"><Mono dim style="font-size:11px;">{vocabMsg}</Mono></div>{/if}
          </section>
        {:else if section === 'engine'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">WHISPER · OLLAMA · RESOLVE</Eyebrow>
              <div class="ak-display fstitle">Engine</div>
              <div class="fsdesc">Transcription / vision models and export defaults are chosen per ingest. In-app pickers have no API yet (brick 4).</div>
            </div>
            <div class="frows">
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Transcription</Mono><span class="pend">model picker pending · brick 4</span></div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Vision tagging</Mono><span class="pend">model + tag pool pending · brick 4</span></div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Export defaults</Mono><span class="pend">EDL fps / proxy pending · brick 4</span></div>
            </div>
          </section>
        {:else}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">RUNTIME</Eyebrow>
              <div class="ak-display fstitle">System</div>
            </div>
            <div class="frows">
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Version</Mono><Mono style="font-size:12px;color:var(--ink);">arkiv {VERSION}</Mono></div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Backend</Mono>
                {#if sys === 'loading'}<Mono dim style="font-size:12px;">checking…</Mono>
                {:else if sys === 'ok'}<Mono style="font-size:12px;color:var(--ink);"><span class="livedot">●</span> online</Mono>
                {:else}<Mono style="font-size:12px;color:var(--cyan);">unreachable</Mono>{/if}
              </div>
              {#if sys === 'ok' && stats}
                <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Library</Mono><Mono style="font-size:12px;color:var(--ink);">{stats.total} media · {Math.round((stats.total_size_mb || 0) / 1024)} GB indexed</Mono></div>
                <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Disk</Mono>
                  {#if disk}<Mono style="font-size:12px;color:var(--ink);">{gb(disk.used_gb)} / {gb(disk.total_gb)} · {disk.pct}%</Mono>{:else}<Mono dim style="font-size:12px;">—</Mono>{/if}
                </div>
              {/if}
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Proxies</Mono>
                {#if proxy}<Mono style="font-size:12px;color:var(--ink);">{proxy.proxied}/{proxy.total} built · {proxy.size_mb} MB</Mono>{:else}<Mono dim style="font-size:12px;">—</Mono>{/if}
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Build proxies</Mono>
                <div class="proxyctl">
                  <button class="ak-btn" on:click={runProxyBuild} disabled={proxyBusy}>{proxyBusy ? '排入中…' : '生成缺漏 proxy'}</button>
                  {#if proxyMsg}<Mono dim style="font-size:10.5px;">{proxyMsg}</Mono>{/if}
                </div>
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">語言時長</Mono>
                {#if durLang && durLang.length}<Mono style="font-size:12px;color:var(--ink);">{durLang.slice(0, 4).map((d) => `${d.lang} ${hms(d.total_s)}`).join(' · ')}</Mono>{:else}<Mono dim style="font-size:12px;">—</Mono>{/if}
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">格式容量</Mono>
                {#if sizeExt && sizeExt.length}<Mono style="font-size:12px;color:var(--ink);">{sizeExt.slice(0, 4).map((s) => `${s.ext || '?'} ${mb(s.total_mb)}`).join(' · ')}</Mono>{:else}<Mono dim style="font-size:12px;">—</Mono>{/if}
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">快取</Mono>
                <div class="proxyctl">
                  <Mono style="font-size:12px;color:var(--ink);">{mb(cache?.total_mb)}</Mono>
                  <button class="ak-btn" on:click={() => runClearCache('app')} disabled={cacheBusy}>清 app 快取</button>
                  <button class="ak-btn" on:click={() => runClearCache('all')} disabled={cacheBusy}>清全部</button>
                  {#if cacheMsg}<Mono dim style="font-size:10.5px;">{cacheMsg}</Mono>{/if}
                </div>
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Privacy</Mono><Mono dim style="font-size:11.5px;">Everything runs locally. Nothing leaves this machine.</Mono></div>
            </div>
          </section>
        {/if}
      </div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); position: relative; overflow: hidden; margin: 0 auto; }
  .scrim { position: absolute; inset: 0; background: rgba(10, 10, 12, 0.55); }

  .modal { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 960px; height: 640px; background: var(--bg); box-shadow: inset 0 0 0 1px var(--invert); display: grid; grid-template-rows: 56px 1fr; }
  .mhead { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--invert); padding: 0 24px; }
  .mtitle { display: flex; align-items: baseline; gap: 16px; }
  .mtitlebig { font-size: 22px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .mclose { font-family: var(--ak-mono); font-size: 11px; letter-spacing: 0.08em; background: transparent; border: none; color: var(--ink-2); cursor: pointer; padding: 0; }
  .mclose:hover { color: var(--ink); }
  .mbody { display: grid; grid-template-columns: 200px 1fr; min-height: 0; overflow: hidden; }
  .mnav { border-right: 1px solid var(--rule); padding: 20px 0; display: flex; flex-direction: column; }
  .navbtn { text-align: left; padding: 7px 24px; background: transparent; border: none; border-left: 2px solid transparent; color: var(--ink-2); font-size: 13px; font-weight: 400; cursor: pointer; font-family: inherit; }
  .navbtn.active { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  .form { overflow: auto; padding: 24px 32px; }
  .fshead { margin-bottom: 14px; }
  .fstitle { font-size: 18px; letter-spacing: -0.02em; line-height: 1.1; color: var(--ink); }
  .fsdesc { font-size: 11.5px; color: var(--quiet); margin-top: 3px; max-width: 460px; line-height: 1.5; }
  .frows { display: flex; flex-direction: column; gap: 12px; }
  .frow { display: grid; grid-template-columns: 140px 1fr; align-items: center; gap: 16px; }
  .frow.disabled { opacity: 0.55; }
  .seg { display: flex; border: 1px solid var(--rule); width: fit-content; }
  .segsep { width: 1px; background: var(--rule); }
  .segbtn { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 5px 12px; background: transparent; color: var(--ink-2); border: none; cursor: pointer; line-height: 1; font-weight: 400; }
  .segbtn.on { background: var(--invert); color: var(--invert-ink); font-weight: 700; }
  .pend { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--quiet-2); border: 1px dashed var(--rule-hi); padding: 2px 7px; width: fit-content; }
  .livedot { color: var(--cyan); font-size: 9px; }
  .proxyctl { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

  /* correction dictionary (Phase 9.6c) */
  .vrules { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
  .vhead, .crow { display: grid; grid-template-columns: 1fr 1fr 88px 36px 36px 28px; align-items: center; gap: 8px; }
  .vhead { font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.08em; color: var(--quiet-2); padding: 0 2px; }
  .vhead span { text-align: center; }
  .vhead span:nth-child(1), .vhead span:nth-child(2) { text-align: left; }
  .vin { font-family: inherit; font-size: 13px; color: var(--ink); background: transparent; border: 1px solid var(--rule); padding: 6px 9px; outline: none; }
  .vin:focus { border-color: var(--invert); }
  .vsel { font-family: var(--ak-mono); font-size: 10.5px; color: var(--ink); background: transparent; border: 1px solid var(--rule); padding: 5px 4px; outline: none; }
  .vcb { justify-self: center; accent-color: var(--invert); cursor: pointer; }
  .vx { background: transparent; border: none; color: var(--quiet-2); cursor: pointer; font-size: 12px; padding: 4px; }
  .vx:hover { color: var(--cyan); }
  .vempty { font-size: 12px; color: var(--quiet); padding: 8px 2px; }
  .vctl { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
  .vchk { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--ink-2); cursor: pointer; }
  .vdiv { height: 1px; background: var(--rule); margin: 20px 0 16px; }
  .vresult { display: flex; flex-direction: column; gap: 3px; margin-top: 12px; padding: 10px 12px; box-shadow: inset 0 0 0 1px var(--rule); }
  .vmsg { margin-top: 8px; }
</style>
