<!-- 精選集 (cross-library bins) — persistent named selections that reference clips
     scattered across MULTIPLE registered projects. Originals never move; a bin is
     pure reference (cross-library is read-only). Each item shows a reachability
     badge (✓ / ⚠ 庫離線 / ✗ 檔案不在 …) so a moved/offline source is never
     silently trusted. Rows are read-only — the clip lives in another project's DB,
     so this server can't stream/open it. Add items from the search screen's
     "加入精選集". Copy-into-project (the payoff) lands in the next PR. -->
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
  let bins = []
  let activeId = null
  let detail = null // { id, name, item_count, reachable, items:[{project_name, media_id, filename, status}] }
  let loading = false
  let newName = ''

  // status → { label, sev }. sev: ok | warn | bad. Drives the badge colour.
  const STATUS = {
    ok: { label: '✓', sev: 'ok' },
    project_unregistered: { label: '未登記', sev: 'warn' },
    db_missing: { label: '庫無索引', sev: 'warn' },
    chroma_missing: { label: '庫無向量', sev: 'warn' },
    nas_unmounted: { label: '庫離線', sev: 'warn' },
    path_not_found: { label: '庫不存在', sev: 'bad' },
    timeout: { label: '逾時', sev: 'warn' },
    row_missing: { label: '已從庫刪除', sev: 'bad' },
    file_missing: { label: '檔案不在', sev: 'bad' },
    error: { label: '錯誤', sev: 'warn' },
  }
  const badge = (s) => STATUS[s] || { label: s || '?', sev: 'warn' }

  // group a bin's items by project_name for display
  $: groups = (() => {
    if (!detail) return []
    const by = new Map()
    for (const it of detail.items || []) {
      if (!by.has(it.project_name)) by.set(it.project_name, { name: it.project_name, items: [] })
      by.get(it.project_name).items.push(it)
    }
    return [...by.values()]
  })()

  async function loadBins() {
    try {
      const r = await api.getBins()
      bins = r.bins || []
      if (!activeId && bins.length) select(bins[0].id)
      else if (activeId && !bins.find((b) => b.id === activeId)) { activeId = null; detail = null }
    } catch (e) { pushToast('讀取精選集失敗: ' + e.message, 'error') }
  }

  async function select(id) {
    activeId = id
    loading = true
    try {
      detail = await api.getBin(id)
    } catch (e) { pushToast('讀取精選集失敗: ' + e.message, 'error'); detail = null }
    loading = false
  }

  async function create() {
    const name = newName.trim()
    if (!name) return
    try {
      const b = await api.createBin(name)
      newName = ''
      await loadBins()
      select(b.id)
      pushToast('已建立精選集「' + name + '」')
    } catch (e) { pushToast('建立失敗: ' + e.message, 'error') }
  }

  async function removeItem(it) {
    try {
      detail = await api.removeBinItem(activeId, it.project_name, it.media_id)
      loadBins() // refresh counts
    } catch (e) { pushToast('移除失敗: ' + e.message, 'error') }
  }

  // ── copy-into-project dialog ──
  let copyOpen = false
  let copyMode = 'reference' // reference | copy
  let destKind = 'new' // new | existing
  let destPath = ''
  let destName = ''
  let existingProj = ''
  let projList = []
  let fastMode = false // skip vision + embedding (clips are already analysed in source)
  let copyRunning = false
  let copyLog = []
  let copySummary = null

  async function openCopy() {
    copyOpen = true
    copyLog = []; copySummary = null
    try { projList = (await api.getProjects()).projects || [] } catch (e) { projList = [] }
    if (!existingProj && projList.length) existingProj = projList[0].name
  }

  function _pushLog(line) { copyLog = [...copyLog.slice(-80), line] }

  function handleCopyEvent(ev) {
    if (ev.type === 'gate') _pushLog(`${ev.action === 'skipped' ? '⤫ 跳過' : '＋ 排入'} ${ev.project_name}#${ev.media_id}${ev.action === 'skipped' ? ' · ' + ev.status : ''}`)
    else if (ev.type === 'copy') _pushLog(ev.error ? `✗ 複製失敗 ${ev.file}: ${ev.error}` : `⇄ 複製 ${ev.file} (${ev.done}/${ev.total})`)
    else if (ev.type === 'index') _pushLog(ev.status === 'start' ? `▸ 索引 ${ev.files} 檔…` : `▸ 索引完成 (code ${ev.code})`)
    else if (ev.type === 'log' && ev.line) _pushLog(ev.line)
    else if (ev.type === 'registered') _pushLog(ev.error ? `註冊: ${ev.error}` : `✓ 已註冊新專案「${ev.name}」`)
    else if (ev.type === 'done') copySummary = ev.summary
  }

  async function runCopy() {
    const body = { mode: copyMode, create_new: destKind === 'new', skip_vision: fastMode, no_embed: fastMode }
    if (destKind === 'new') {
      if (!destPath.trim()) { pushToast('請填新專案路徑', 'error'); return }
      body.dest = destPath.trim()
      if (destName.trim()) body.dest_name = destName.trim()
    } else {
      if (!existingProj) { pushToast('請選目的專案', 'error'); return }
      body.dest = existingProj
    }
    copyRunning = true; copyLog = []; copySummary = null
    try {
      const res = await api.copyBin(activeId, body)
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let nl
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim()
          buf = buf.slice(nl + 1)
          if (line) { try { handleCopyEvent(JSON.parse(line)) } catch (e) { /* skip */ } }
        }
      }
      if (copySummary) {
        const s = copySummary
        pushToast(`複製完成 → ${s.dest}：${s.copied} 支${s.skipped.length ? '，略過 ' + s.skipped.length : ''}`)
      }
      loadBins(); if (activeId) select(activeId)
    } catch (e) { pushToast('複製失敗: ' + e.message, 'error') }
    copyRunning = false
  }

  async function del(id, name) {
    if (!confirm(`刪除精選集「${name}」？（只刪清單，不動任何原始素材）`)) return
    try {
      await api.deleteBin(id)
      if (activeId === id) { activeId = null; detail = null }
      await loadBins()
      pushToast('已刪除精選集')
    } catch (e) { pushToast('刪除失敗: ' + e.message, 'error') }
  }

  onMount(loadBins)
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">精選集</Mono>
    <div class="grow"></div>
    <a class="ak-btn" href="#/search-live">⌕ 搜尋加片</a>
    <a class="ak-btn" href="#/main-live">← back to grid</a>
  </div>

  <div class="body">
    <!-- left: bin list + create -->
    <aside class="side">
      <Eyebrow style="margin-bottom:10px;">精選集 · 跨庫引用</Eyebrow>
      <div class="createrow">
        <input class="ak-input newname" placeholder="新精選集名稱…" bind:value={newName}
               on:keydown={(e) => e.key === 'Enter' && create()} />
        <button class="ak-btn" on:click={create}>＋</button>
      </div>
      <div class="binlist">
        {#each bins as b (b.id)}
          <button class="binrow" class:on={activeId === b.id} on:click={() => select(b.id)}>
            <span class="bname">{b.name}</span>
            <Mono dim style="font-size:10px;">{b.item_count}</Mono>
          </button>
        {/each}
        {#if !bins.length}
          <Mono dim style="font-size:10.5px;display:block;padding:8px 2px;">尚無精選集 — 上方新建，或到搜尋頁把片加入</Mono>
        {/if}
      </div>
    </aside>

    <!-- right: active bin detail -->
    <main class="detail">
      {#if !detail}
        <div class="empty">選一個精選集，或先建立一個</div>
      {:else}
        <div class="dhead">
          <div class="ak-display dname">{detail.name}</div>
          <Mono dim style="font-size:10.5px;">{detail.reachable}/{detail.item_count} 可達</Mono>
          <div class="grow"></div>
          {#if detail.item_count}
            <button class="ak-btn ak-btn--primary" on:click={openCopy}>複製進新案 →</button>
          {/if}
          <button class="ak-btn danger" on:click={() => del(detail.id, detail.name)}>刪除精選集</button>
        </div>

        {#if copyOpen}
          <div class="copypanel">
            <div class="cprow">
              <Mono dim style="font-size:10px;letter-spacing:0.08em;">目的地</Mono>
              <label class="radio"><input type="radio" bind:group={destKind} value="new" /> 新專案</label>
              <label class="radio"><input type="radio" bind:group={destKind} value="existing" /> 現有專案</label>
            </div>
            {#if destKind === 'new'}
              <div class="cprow">
                <input class="ak-input" style="flex:1" placeholder="新專案資料夾路徑（例 /Volumes/NAS/新案）" bind:value={destPath} />
                <input class="ak-input" style="flex:0 1 160px" placeholder="專案名稱（選填）" bind:value={destName} />
              </div>
            {:else}
              <div class="cprow">
                <select class="ak-input" style="flex:1" bind:value={existingProj}>
                  {#each projList as p}<option value={p.name}>{p.name}</option>{/each}
                </select>
              </div>
            {/if}
            <div class="cprow">
              <Mono dim style="font-size:10px;letter-spacing:0.08em;">方式</Mono>
              <label class="radio" title="新案索引舊庫原始檔路徑，不搬 bytes（原檔不動、省空間；舊庫離線則讀不到）">
                <input type="radio" bind:group={copyMode} value="reference" /> 索引引用（原檔不動）
              </label>
              <label class="radio" title="驗證複製 bytes 進新案（自足可攜、防舊庫離線；佔額外空間）">
                <input type="radio" bind:group={copyMode} value="copy" /> 實體複製 bytes
              </label>
            </div>
            <div class="cprow">
              <label class="radio" title="略過 vision/embedding 重分析（來源已分析過，較快；新案暫無語意搜尋直到補跑）">
                <input type="checkbox" bind:checked={fastMode} /> 快速（略過重分析）
              </label>
              <div class="grow"></div>
              <button class="ak-btn" on:click={() => (copyOpen = false)} disabled={copyRunning}>取消</button>
              <button class="ak-btn ak-btn--primary" on:click={runCopy} disabled={copyRunning}>
                {copyRunning ? '複製中…' : '開始複製'}
              </button>
            </div>
            {#if copyLog.length || copySummary}
              <div class="cplog">
                {#each copyLog as l}<div class="cplogline">{l}</div>{/each}
              </div>
            {/if}
            {#if copySummary}
              <div class="cpsummary">
                <Mono style="font-size:11px;font-weight:600;">完成 → {copySummary.dest}：複製 {copySummary.copied} 支（{copySummary.mode === 'copy' ? '實體複製' : '索引引用'}）</Mono>
                {#if copySummary.skipped.length}
                  <Mono style="font-size:10px;color:var(--cyan);">略過 {copySummary.skipped.length}：{copySummary.skipped.map((s) => s.project_name + '#' + s.media_id + '(' + s.status + ')').join('、')}</Mono>
                {/if}
              </div>
            {/if}
          </div>
        {/if}

        {#if loading}
          <Mono dim style="font-size:10.5px;">載入中…</Mono>
        {:else if !detail.item_count}
          <div class="empty">這個精選集還沒有片 — 到 <a href="#/search-live">搜尋頁</a> 選片「加入精選集」</div>
        {:else}
          {#each groups as g (g.name)}
            <section class="rgroup">
              <div class="ghead">
                <Mono dim style="font-size:10px;letter-spacing:0.1em;">PROJECT</Mono>
                <div class="ak-display gproj">{g.name}</div>
                <Mono dim style="font-size:10.5px;">{g.items.length}</Mono>
                <div class="grow"></div>
                <Mono dim style="font-size:9.5px;letter-spacing:0.08em;" title="結果在其他專案的資料庫 · 唯讀，無法在此開啟/播放">唯讀 · 跨庫</Mono>
              </div>
              <div class="rows">
                {#each g.items as it (it.project_name + ':' + it.media_id)}
                  {@const bd = badge(it.status)}
                  <div class="rrow">
                    <div class="rthumb"><Thumb seed={it.media_id} kind="video" {theme} /></div>
                    <div class="rcontent">
                      <Mono style="font-size:11.5px;font-weight:500;color:var(--ink);">{it.filename || ('#' + it.media_id)}</Mono>
                    </div>
                    <div class="rbadge">
                      <span class="badge {bd.sev}" title={it.status}>{bd.label}</span>
                    </div>
                    <div class="raction">
                      <button class="ak-btn xbtn" title="從精選集移除（不動原檔）" on:click={() => removeItem(it)}>×</button>
                    </div>
                  </div>
                {/each}
              </div>
            </section>
          {/each}
        {/if}
      {/if}
    </main>
  </div>
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .body { display: grid; grid-template-columns: 260px 1fr; min-height: 0; overflow: hidden; }
  .side { border-right: 1px solid var(--rule); padding: 18px 16px; overflow: auto; }
  .createrow { display: flex; gap: 6px; margin-bottom: 12px; }
  .newname { flex: 1; font-size: 12px; }
  .binlist { display: flex; flex-direction: column; gap: 2px; }
  .binrow { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; text-align: left; padding: 7px 8px; background: transparent; border: 1px solid transparent; cursor: pointer; color: var(--ink-2); }
  .binrow:hover { background: var(--surface-2); }
  .binrow.on { background: var(--surface-2); border-color: var(--rule-hi); color: var(--ink); }
  .bname { font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .detail { padding: 18px 40px; overflow: auto; }
  .dhead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 14px; padding-bottom: 8px; border-bottom: 1px solid var(--rule); }
  .dname { font-size: 22px; letter-spacing: -0.03em; color: var(--ink); }
  .empty { padding: 40px 16px; font-family: var(--ak-mono); font-size: 11px; color: var(--quiet); text-align: center; letter-spacing: 0.05em; }
  .empty a, .detail a { color: var(--ink-2); }
  .rgroup { margin-bottom: 16px; }
  .ghead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
  .gproj { font-size: 18px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .rrow { display: grid; grid-template-columns: 100px 1fr 96px 40px; gap: 14px; align-items: center; padding: 5px 0; border-top: 1px solid var(--rule); }
  .rthumb { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }
  .rcontent { min-width: 0; }
  .rbadge { text-align: right; }
  .badge { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.05em; padding: 2px 6px; border: 1px solid var(--rule); white-space: nowrap; }
  .badge.ok { color: var(--ink-2); border-color: var(--rule-hi); }
  .badge.warn { color: var(--cyan); border-color: var(--cyan); }
  .badge.bad { color: #e0533d; border-color: #e0533d; }
  .raction { display: flex; justify-content: flex-end; }
  .xbtn { padding: 3px 8px; }
  .danger { color: #e0533d; border-color: #e0533d; }
  .danger:hover { background: #e0533d; color: #fff; }
  .copypanel { border: 1px solid var(--rule-hi); background: var(--surface-2); padding: 12px 14px; margin-bottom: 16px; display: flex; flex-direction: column; gap: 10px; }
  .cprow { display: flex; align-items: center; gap: 12px; }
  .radio { display: flex; align-items: center; gap: 5px; font-family: var(--ak-mono); font-size: 11px; color: var(--ink-2); cursor: pointer; }
  .radio input { cursor: pointer; accent-color: var(--invert); }
  .cplog { max-height: 160px; overflow: auto; background: var(--bg); border: 1px solid var(--rule); padding: 6px 8px; font-family: var(--ak-mono); font-size: 10px; line-height: 1.5; color: var(--ink-2); }
  .cplogline { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cpsummary { display: flex; flex-direction: column; gap: 2px; padding-top: 4px; }
</style>
