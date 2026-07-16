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
  import { onMount, onDestroy } from 'svelte'
  import { push } from 'svelte-spa-router'
  import * as api from '../lib/api.js'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { themePref, resolvedTheme, uiScale, SCALE_MIN, SCALE_MAX } from '../lib/prefs.js'

  import { VERSION } from '../lib/version.js'
  // G5 step ①: nav expanded to the design's tab set. Real-backed tabs are
  // interactive; tabs without backend (vision/export) show honest pending rows.
  let section = 'general'
  const nav = [
    ['general', 'General'],
    ['transcription', 'Transcription'],
    ['vision', 'Vision tagging'],
    ['export', 'Export defaults'],
    ['storage', 'Storage · proxy'],
    ['projects', 'Projects'],
    ['advanced', 'Advanced'],
    ['system', 'System · about'],
  ]
  const themeOpts = [['dark', 'Dark'], ['light', 'Light'], ['system', 'System']]

  // Transcription engines (brick 4, PR #77) — real whisper quality presets +
  // forced-language set. We display the available options + current default; the
  // per-ingest picker lives in IngestSetup. Persisting a库-wide default = step ②.
  let engines = null // {whisper_modes:[{mode,name}], default_mode, languages:[{code,label}]}
  async function loadEngines() {
    try { engines = await api.getIngestEngines() } catch { engines = null }
  }

  // G5②③ — persisted settings (default ← global ← project). These are REAL:
  // vision.model / vision.num_ctx are read by the ingest run (vision.py /
  // ingest.py warm-up); export.default_dir is the fallback dest for the
  // server-write CSV export. We only expose controls that are genuinely consumed.
  let settingsList = []     // describe() output
  let settingsBusy = false
  let settingsMsg = ''
  let visModel = '', visNumCtx = 16384, expDir = ''  // local edit buffers
  function settingMeta(key) { return settingsList.find((s) => s.key === key) || null }
  async function loadSettings() {
    try { settingsList = (await api.getSettings()).settings || [] } catch { settingsList = [] }
    const vm = settingMeta('vision.model'); if (vm) visModel = vm.value
    const vc = settingMeta('vision.num_ctx'); if (vc) visNumCtx = vc.value
    const ed = settingMeta('export.default_dir'); if (ed) expDir = ed.value
  }
  async function saveSetting(values) {
    settingsBusy = true; settingsMsg = ''
    try { await api.putSettings(values); await loadSettings(); settingsMsg = '已儲存 ✓' }
    catch (e) { settingsMsg = '儲存失敗：' + (e?.body?.detail || e.message) }
    finally { settingsBusy = false }
  }
  async function resetSettingKey(key) {
    settingsBusy = true; settingsMsg = ''
    try { await api.resetSetting(key); await loadSettings(); settingsMsg = '已重設為預設 ✓' }
    catch (e) { settingsMsg = '重設失敗：' + (e?.body?.detail || e.message) }
    finally { settingsBusy = false }
  }

  // Project registry — full CRUD (projects_read/write, token-free on loopback).
  let projects = [] // [{name, path, added_at, last_indexed_at, tags, source}]
  let projHealth = {} // name -> status string ("ok" | …)
  let projMsg = ''
  let projBusy = false
  let newName = '', newPath = '', newTags = ''
  async function loadProjects() {
    try { projects = (await api.getProjects()).projects || [] } catch { projects = [] }
    try {
      const h = await api.getProjectsHealth()
      projHealth = Object.fromEntries((h.projects || []).map((p) => [p.name, p.status]))
    } catch { projHealth = {} }
  }
  async function addProj() {
    if (projBusy) return
    const name = newName.trim(), path = newPath.trim()
    if (!name || !path) { projMsg = '名稱與路徑都要填'; return }
    projBusy = true; projMsg = ''
    try {
      await api.addProject({ name, path, tags: newTags.split(',').map((t) => t.trim()).filter(Boolean) })
      newName = ''; newPath = ''; newTags = ''
      await loadProjects()
      projMsg = `已加入 ${name}`
    } catch (e) { projMsg = e.status === 409 ? `已存在同名專案：${name}` : `失敗: ${e.message}` }
    finally { projBusy = false }
  }
  async function delProj(name) {
    if (projBusy) return
    projBusy = true; projMsg = ''
    try { await api.deleteProject(name); await loadProjects(); projMsg = `已移除 ${name}` }
    catch (e) { projMsg = `失敗: ${e.message}` } finally { projBusy = false }
  }
  async function syncProj() {
    if (projBusy) return
    projBusy = true; projMsg = ''
    try { await api.syncProjects(); await loadProjects(); projMsg = '已同步索引時間' }
    catch (e) { projMsg = `失敗: ${e.message}` } finally { projBusy = false }
  }

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

  // Batch retranscribe (2a, 9.6d) — heavy upgrade path: re-runs Whisper across
  // the whole project so new hotwords take effect. Poll status while running.
  let retr = null // {total, done, failed, current, running, backup}
  let retrTimer = null
  async function pollRetr() {
    try {
      retr = await api.retranscribeAllStatus()
      if (retr && retr.running) retrTimer = setTimeout(pollRetr, 1500)
      else { backups = (await api.getRecorrectBackups()).backups || [] }
    } catch { /* ignore poll error */ }
  }
  async function runRetranscribeAll() {
    if (retr && retr.running) return
    vocabMsg = ''
    try {
      const r = await api.retranscribeAll(true)
      if (!r.queued) { vocabMsg = r.message || '沒有可重轉錄的素材'; return }
      retr = { total: r.queued, done: 0, failed: 0, current: null, running: true, backup: null }
      pollRetr()
    } catch (e) { vocabMsg = e.status === 409 ? '批次重轉錄已在進行中' : `重轉錄失敗: ${e.message}` }
  }
  onDestroy(() => { if (retrTimer) clearTimeout(retrTimer) })

  onMount(() => { loadSystem(); loadProxy(); loadAnalytics(); loadCache(); loadVocab(); loadEngines(); loadProjects(); loadSettings() })

  const shortDate = (s) => (s ? String(s).slice(0, 10) : '—')

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
        {#if section === 'general'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">THEME · INTERFACE</Eyebrow>
              <div class="ak-display fstitle">General</div>
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
              <div class="frow">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">UI scale</Mono>
                <div class="scalectl">
                  <input class="scalerange" type="range" min={SCALE_MIN} max={SCALE_MAX} step="0.05" bind:value={$uiScale} />
                  <Mono style="font-size:11px;color:var(--ink);min-width:38px;">{Math.round($uiScale * 100)}%</Mono>
                  <button class="ak-btn" on:click={() => uiScale.set(1)} disabled={$uiScale === 1}>重設</button>
                </div>
              </div>
              <div class="frow disabled">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Type density</Mono>
                <span class="pend">px-layout · spacing not reflowable yet</span>
              </div>
            </div>
          </section>
        {:else if section === 'advanced'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">EFFECTIVE SETTINGS · 生效設定</Eyebrow>
              <div class="ak-display fstitle">Advanced</div>
              <div class="fsdesc">所有持久化設定的<b>生效值</b>與來源（<code>default</code> = config 預設／<code>global</code> = 全庫覆寫）。可從這裡一鍵重設任一覆寫回預設。</div>
            </div>
            {#if settingsList.length}
              <div class="settbl">
                <div class="settbl-h"><span>KEY</span><span>VALUE</span><span>SOURCE</span><span></span></div>
                {#each settingsList as s}
                  <div class="settbl-r">
                    <span class="setk" title={s.label}>{s.key}</span>
                    <span class="setv">{s.value === '' ? '—' : s.value}</span>
                    <span class="srctag" class:srcset={s.source !== 'default'}>{s.source}</span>
                    <span>{#if s.source !== 'default'}<button class="vx" title="重設為預設" on:click={() => resetSettingKey(s.key)} disabled={settingsBusy}>↺</button>{/if}</span>
                  </div>
                {/each}
              </div>
            {/if}
            <div class="fshead" style="margin-top:24px;">
              <Eyebrow style="margin-bottom:4px;">CORRECTION DICTIONARY · 校正字典</Eyebrow>
              <div class="ak-display fstitle" style="font-size:15px;">Correction dictionary</div>
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

            <div class="vdiv"></div>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">BATCH RETRANSCRIBE · 批次重轉錄（2a · 升級）</Eyebrow>
              <div class="fsdesc">重跑整庫 Whisper，讓新加的 hotword 生效——<b>慢、耗資源</b>，只在某詞被聽成完全不同的東西、批次校正救不回時才用。重轉前自動備份，可還原。</div>
            </div>
            <div class="vctl">
              <button class="ak-btn" on:click={runRetranscribeAll} disabled={retr && retr.running}>{retr && retr.running ? '重轉錄中…' : '批次重轉錄'}</button>
              {#if retr}
                <Mono dim style="font-size:11px;">{retr.done}/{retr.total} 完成{retr.failed ? ` · ${retr.failed} 失敗` : ''}{retr.running ? '…' : ' · 完成'}</Mono>
              {/if}
            </div>
          </section>
        {:else if section === 'transcription'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">WHISPER · GUARD PRESETS</Eyebrow>
              <div class="ak-display fstitle">Transcription</div>
              <div class="fsdesc">轉錄品質預設（whisper-guard 0–4）與強制語言，每次匯入於 setup 對話框選。此處顯示可用選項 + 目前預設；設成全庫預設＝下一步（settings 表）。</div>
            </div>
            {#if engines}
              <div class="frows">
                <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Quality presets</Mono>
                  <div class="chips">
                    {#each engines.whisper_modes as m}
                      <span class="chip" class:chipon={m.mode === engines.default_mode}>{m.mode} · {m.name}{m.mode === engines.default_mode ? ' ●' : ''}</span>
                    {/each}
                  </div>
                </div>
                <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Languages</Mono>
                  <div class="chips">
                    {#each engines.languages as l}<span class="chip">{l.label} · {l.code}</span>{/each}
                    <span class="chip">auto-detect</span>
                  </div>
                </div>
                <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Current default</Mono><Mono style="font-size:12px;color:var(--ink);">preset {engines.default_mode} · 語言自動偵測</Mono></div>
              </div>
            {:else}
              <span class="pend">engines endpoint unreachable</span>
            {/if}
          </section>
        {:else if section === 'vision'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">VLM · LIBRARY DEFAULT</Eyebrow>
              <div class="ak-display fstitle">Vision tagging</div>
              <div class="fsdesc">視覺模型與 context window 的全庫預設。<strong>真實生效</strong>：每次 ingest 的 vision 標註與 warm-up 都讀這裡（未設＝沿用 config 預設）。Tag pool 約束仍待後端（brick 4b），不造假。</div>
            </div>
            <div class="frows">
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Vision model</Mono>
                <div class="setctl">
                  <input class="ak-input" bind:value={visModel} placeholder="qwen2.5vl:7b" spellcheck="false" />
                  {#if settingMeta('vision.model')}<span class="srctag">{settingMeta('vision.model').source}</span>{/if}
                </div>
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Context window (num_ctx)</Mono>
                <div class="setctl">
                  <input class="ak-input num" type="number" min="512" max="131072" bind:value={visNumCtx} />
                  {#if settingMeta('vision.num_ctx')}<span class="srctag">{settingMeta('vision.num_ctx').source}</span>{/if}
                </div>
              </div>
              <div class="frow"><span></span>
                <div class="setctl">
                  <button class="ak-btn" on:click={() => saveSetting({ 'vision.model': visModel, 'vision.num_ctx': Number(visNumCtx) })} disabled={settingsBusy}>儲存全庫預設</button>
                  <button class="ak-btn" on:click={() => { resetSettingKey('vision.model'); resetSettingKey('vision.num_ctx') }} disabled={settingsBusy}>重設</button>
                  {#if settingsMsg}<span class="setmsg">{settingsMsg}</span>{/if}
                </div>
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Tag pool · confidence</Mono><span class="pend">no config endpoint yet · brick 4b</span></div>
            </div>
          </section>
        {:else if section === 'export'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">EXPORT · DEFAULT DEST</Eyebrow>
              <div class="ak-display fstitle">Export defaults</div>
              <div class="fsdesc">預設匯出資料夾。<strong>真實生效</strong>：server-write CSV 匯出（Tauri 存檔路徑）未指定時落這個目錄。EDL fps / proxy 解析度 / drop-frame 仍每次呼叫帶入、無持久預設（待後端）。</div>
            </div>
            <div class="frows">
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Default export dir</Mono>
                <div class="setctl">
                  <input class="ak-input" bind:value={expDir} placeholder="~/Desktop/arkiv-exports（空＝瀏覽器下載）" spellcheck="false" />
                  {#if settingMeta('export.default_dir')}<span class="srctag">{settingMeta('export.default_dir').source}</span>{/if}
                </div>
              </div>
              <div class="frow"><span></span>
                <div class="setctl">
                  <button class="ak-btn" on:click={() => saveSetting({ 'export.default_dir': expDir })} disabled={settingsBusy}>儲存</button>
                  <button class="ak-btn" on:click={() => resetSettingKey('export.default_dir')} disabled={settingsBusy}>重設</button>
                  {#if settingsMsg}<span class="setmsg">{settingsMsg}</span>{/if}
                </div>
              </div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">EDL frame rate · proxy · drop-frame</Mono><span class="pend">default pending · per-call only</span></div>
            </div>
          </section>
        {:else if section === 'storage'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">PROXY · CACHE · BREAKDOWN</Eyebrow>
              <div class="ak-display fstitle">Storage · proxy</div>
              <div class="fsdesc">編輯用 proxy 狀態與生成、快取清理，以及庫的語言時長 / 格式容量分布。</div>
            </div>
            <div class="frows">
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
            </div>
          </section>
        {:else if section === 'projects'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">PROJECT REGISTRY · 專案註冊表</Eyebrow>
              <div class="ak-display fstitle">Projects</div>
              <div class="fsdesc">跨庫專案登記——加入 / 移除 / 同步索引時間。寫入 <code>~/.arkiv-projects.json</code>。健康狀態來自 <code>/api/projects/health</code>。</div>
            </div>

            <div class="ptable">
              <div class="phead"><span>NAME</span><span>PATH</span><span>INDEXED</span><span>HEALTH</span><span></span></div>
              {#each projects as p}
                <div class="prow">
                  <span class="pname">{p.name}</span>
                  <span class="ppath" title={p.path}>{p.path}</span>
                  <Mono dim style="font-size:10.5px;">{shortDate(p.last_indexed_at)}</Mono>
                  <span class="phealth" class:ok={projHealth[p.name] === 'ok'}>{projHealth[p.name] || '—'}</span>
                  <button class="vx" on:click={() => delProj(p.name)} disabled={projBusy} title="移除">✕</button>
                </div>
              {/each}
              {#if !projects.length}<div class="vempty">尚無註冊專案 — 下方加入第一個。</div>{/if}
            </div>

            <div class="vdiv"></div>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">ADD PROJECT · 加入</Eyebrow>
            </div>
            <div class="paddrow">
              <input class="vin" bind:value={newName} placeholder="專案名稱" />
              <input class="vin" bind:value={newPath} placeholder="/Volumes/… 路徑" />
              <input class="vin" bind:value={newTags} placeholder="tags（逗號分隔，可空）" />
            </div>
            <div class="vctl">
              <button class="ak-btn" on:click={addProj} disabled={projBusy}>+ 加入專案</button>
              <button class="ak-btn" on:click={syncProj} disabled={projBusy}>同步索引時間</button>
              {#if projMsg}<Mono dim style="font-size:11px;">{projMsg}</Mono>{/if}
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
  /* G5②③ settings controls */
  .setctl { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .setctl .ak-input { min-width: 220px; }
  .setctl .ak-input.num { min-width: 110px; }
  .setmsg { font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.04em; color: var(--cyan); }
  .srctag { font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--quiet-2); border: 1px solid var(--rule); padding: 1px 6px; }
  .srctag.srcset { color: var(--cyan); border-color: var(--cyan); }
  .settbl { display: flex; flex-direction: column; border: 1px solid var(--rule); }
  .settbl-h, .settbl-r { display: grid; grid-template-columns: 1.6fr 1.4fr 0.7fr 32px; align-items: center; gap: 10px; padding: 7px 10px; }
  .settbl-h { font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.08em; color: var(--quiet-2); border-bottom: 1px solid var(--rule); }
  .settbl-r { border-bottom: 1px solid var(--rule-lo, var(--rule)); }
  .settbl-r:last-child { border-bottom: none; }
  .setk { font-family: var(--ak-mono); font-size: 11px; color: var(--ink-2); }
  .setv { font-family: var(--ak-mono); font-size: 11px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .proxyctl { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .scalectl { display: flex; align-items: center; gap: 12px; }
  .scalerange { width: 180px; accent-color: var(--invert); cursor: pointer; }

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

  /* G5 transcription engines (read-only chips) */
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.04em; color: var(--ink-2); border: 1px solid var(--rule); padding: 3px 8px; line-height: 1.2; }
  .chip.chipon { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }

  /* G5 project registry table */
  .ptable { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
  .phead, .prow { display: grid; grid-template-columns: 1.2fr 2fr 78px 64px 28px; align-items: center; gap: 10px; }
  .phead { font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.08em; color: var(--quiet-2); padding: 0 2px; }
  .prow { padding: 5px 2px; border-bottom: 1px solid var(--rule); }
  .pname { font-size: 13px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ppath { font-family: var(--ak-mono); font-size: 10.5px; color: var(--quiet); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .phealth { font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--quiet-2); }
  .phealth.ok { color: var(--cyan); }
  .paddrow { display: grid; grid-template-columns: 1.2fr 2fr 1.4fr; gap: 8px; margin-bottom: 12px; }
</style>
