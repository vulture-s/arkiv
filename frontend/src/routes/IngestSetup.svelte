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
  import { onMount } from 'svelte'
  import { VERSION } from '../lib/version.js'
  import { push } from 'svelte-spa-router'
  import * as api from '../lib/api.js'
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { resolvedTheme } from '../lib/prefs.js'
  import { pickFolder, canPickFolder } from '../lib/pickFolder.js'
  import { pushToast } from '../lib/toast.js'
  import { t } from '../lib/i18n.js'

  let path = ''
  let limit = 0
  // engine flags surfaced by S1a brick 1
  let opts = { skip_vision: false, refresh: false, recursive: false, skip_failed: false, no_embed: false }
  let maxFailures = 0
  // brick 4 — real transcription pickers, options from /api/ingest/engines.
  // '' = use the backend default (no flag sent), so unchanged callers stay default.
  let engines = null
  // Source shortcuts: [{label, path}]. The browser build has no folder picker
  // (canPickFolder() needs window.__TAURI__), so without these the only way to
  // reach a long NAS path on the web UI is to retype it every single time.
  let presets = []
  let presetNote = ''
  let whisperGuard = '' // '' = default preset; else mode int as string
  let language = ''     // '' = auto-detect; else whisper code
  // brick 4b — vision model picker. Unlike the per-run whisper preset above,
  // vision.model is a persisted library default (ingest reads settings.vision_model()
  // fresh each run), so selecting here writes the setting, matching SettingsLive.
  let visionModel = ''
  let visionNote = ''

  let manifest = null      // {video,audio,unsupported,total_size_mb}
  let total = 0, fresh = 0
  // From /api/ingest/scan: {seconds, budget_s, stall_s, probed, of}.
  // `seconds` is advisory — nothing is killed on it. What actually bounds
  // the run is budget_s plus a stall watchdog the UI cannot see.
  let estimate = null
  let scanning = false, starting = false, err = '', notice = ''

  $: gb = manifest ? (manifest.total_size_mb / 1024).toFixed(1) : null
  $: hhmm = (s) => {
    if (!s || s < 60) return $t('{n} 秒', { n: Math.max(1, Math.round(s || 0)) })
    const m = Math.round(s / 60)
    return m < 60 ? $t('{m} 分', { m }) : $t('{h} 小時 {m} 分', { h: Math.floor(m / 60), m: m % 60 })
  }

  // The header button has read "ESC · CANCEL" since this screen shipped, but the
  // key was never wired. Same fix as Offload's, minus its running-copy guard:
  // nothing here runs long enough to be worth interrupting with a confirm — Scan
  // is a single request, and the actual ingest happens on /ingest-live.
  //
  // No skip-when-focused-in-an-input guard on purpose: that belongs to printable
  // shortcuts (Inspector's onKey), not to Escape, which should close the dialog
  // from inside the path field too.
  function onKey(e) {
    if (e.key !== 'Escape') return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    e.preventDefault()
    push('/')
  }

  // Native folder chooser, same contract as Offload's: desktop shell only, and a
  // cancel leaves the field alone rather than clearing a path someone typed.
  const canBrowse = canPickFolder()
  async function browsePath() {
    try {
      const p = await pickFolder(path)
      if (p) path = p
    } catch (e) {
      pushToast($t('資料夾選擇器打不開：{name} {error}', { name: e?.name || '', error: e?.message || e }), 'error')
    }
  }

  async function scan() {
    // Same rule as Offload's: a missing required path shouts, because pressing
    // the action with a blank field is the likeliest way to hit it and a line of
    // inline text is easy to miss. `err` stays for operation failures below, so
    // validation and runtime errors do not compete for the same slot.
    if (!path.trim()) {
      pushToast($t('請先填來源資料夾路徑（Source · folder）'), 'error')
      return
    }
    err = ''; notice = ''; scanning = true; manifest = null; estimate = null
    try {
      const d = await api.scanMedia(path.trim())
      manifest = d.manifest; total = d.total; fresh = d.new; estimate = d.estimate || null
      if (total === 0) notice = $t('這個資料夾沒有可匯入的媒體檔。')
    } catch (e) { err = e.message } finally { scanning = false }
  }

  async function start() {
    // Same shape as Offload's doRun: collect every unmet prerequisite and name
    // them in one message. "Scan first" and "that folder had nothing in it" are
    // different problems with the same symptom (total === 0), so they get
    // different sentences — manifest is null only before a scan has run.
    const missing = []
    if (!path.trim()) missing.push($t('來源資料夾路徑（Source · folder）'))
    if (manifest === null) missing.push($t('先按 Scan 掃描來源資料夾'))
    else if (total === 0) missing.push($t('可匯入的媒體檔（這個資料夾掃到 0 個）'))
    if (missing.length) {
      pushToast($t('無法開始匯入 — 缺少：{list}', { list: missing.join($t('、')) }), 'error')
      return
    }
    err = ''; starting = true
    try {
      const body = { ...opts }
      if (maxFailures > 0) body.max_failures = Number(maxFailures)
      if (whisperGuard !== '') body.whisper_guard = Number(whisperGuard)
      if (language !== '') body.language = language
      await api.ingestWs(path.trim(), Number(limit) || 0, body)
      // hand off to the live progress route (the WS is already broadcasting)
      push('/ingest-live')
    } catch (e) { err = e.message; starting = false }
  }

  // Persist the chosen vision model as the library default (vision.model), the
  // same setting SettingsLive writes. The next ingest picks it up via
  // settings.vision_model(). No per-run flag exists for vision (by design).
  // Adding the shortcut from here rather than only in Settings: the moment you
  // know a path is worth keeping is right after you typed it, not later in
  // another screen.
  // `ingest.source_presets` is one flat string: entries split on ';', label and
  // path split on the first '|'. Neither character is escaped, and both are legal
  // in a macOS folder name — so a path containing one does not merely lose its
  // own shortcut. This writer re-serialises the WHOLE list from `presets`, and
  // `presets` came back through that same lossy parser, so adding any shortcut
  // rewrites every previously-mangled entry into its mangled form permanently.
  // The settings docstring's promise that "losing one shortcut is recoverable"
  // stops being true at exactly that point. Refuse the unrepresentable path
  // instead, and say which character is the problem.
  const PRESET_UNSAFE = /[;|]/
  async function savePreset() {
    const val = path.trim()
    if (!val) { presetNote = $t('先填路徑'); return }
    if (presets.some((p) => p.path === val)) { presetNote = $t('這個路徑已在捷徑裡'); return }
    const bad = val.match(PRESET_UNSAFE)
    if (bad) { presetNote = $t('路徑含有「{ch}」，捷徑格式存不了這個字元', { ch: bad[0] }); return }
    if (presets.some((p) => PRESET_UNSAFE.test(p.path) || PRESET_UNSAFE.test(p.label))) {
      // Writing now would persist the damage to the existing entries too.
      presetNote = $t('既有捷徑裡有「;」或「|」，請先到 Settings 修好再新增')
      return
    }
    const label = (val.replace(/\/+$/, '').split('/').pop() || val).slice(0, 24)
    const next = [...presets, { label, path: val }]
    presetNote = $t('儲存中…')
    try {
      await api.putSettings({
        'ingest.source_presets': next.map((p) => `${p.label}|${p.path}`).join('; '),
      })
      presets = next
      presetNote = $t('已加入捷徑「{label}」', { label })
    } catch (e) { presetNote = $t('儲存失敗') }
  }

  async function saveVisionModel() {
    visionNote = $t('儲存中…')
    try { await api.putSettings({ 'vision.model': visionModel }); visionNote = $t('已設為全庫預設 ✓') }
    catch (e) { visionNote = $t('儲存失敗') }
  }

  // 2-phase DIT handoff: /offload sends the completed destination here as
  // #/ingest-setup?src=<path> so the user can ingest what they just offloaded.
  onMount(async () => {
    try {
      engines = await api.getIngestEngines()
      // G5③ — pre-fill the dialog from the persisted library defaults so the
      // operator's Settings choices are honored as the starting point here.
      if (engines && engines.default_language) language = engines.default_language
      if (engines && typeof engines.default_recursive === 'boolean') opts.recursive = engines.default_recursive
      if (engines && engines.vision_model) visionModel = engines.vision_model
      presets = engines?.source_presets ?? []
    } catch (e) { /* picker falls back to default-only */ }
    const h = window.location.hash
    const qi = h.indexOf('?')
    if (qi === -1) return
    const src = new URLSearchParams(h.slice(qi + 1)).get('src')
    if (src) { path = src; scan() }
  })

  $: TOGGLES = [
    ['skip_vision', 'Skip vision', $t('跳過 AI 視覺標註（只轉錄）')],
    ['refresh', 'Refresh', $t('重抽已索引檔（含 360 reproject 重套）')],
    ['recursive', 'Recursive', $t('遞迴掃子資料夾')],
    ['skip_failed', 'Tolerate vision fails', $t('零星幀失敗不中斷（過夜跑）')],
    ['no_embed', 'Skip index rebuild', $t('匯入後不自動重建向量索引')],
  ]
</script>

<svelte:window on:keydown={onKey} />

<div class="artboard" data-theme={$resolvedTheme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">{VERSION}</Mono>
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
            <input class="ak-input" placeholder={$t('/Volumes/CARD/DCIM  或  ~/footage')} bind:value={path} spellcheck="false" on:keydown={(e)=>e.key==='Enter'&&scan()} />
            {#if canBrowse}
              <button class="seg" on:click={browsePath} disabled={scanning} title={$t('選擇資料夾')}>⋯</button>
            {/if}
            <button class="ak-btn" on:click={scan} disabled={scanning}>{scanning ? 'scanning…' : 'Scan'}</button>
          </div>
          <div class="presetrow">
            {#each presets as p (p.path)}
              <button class="seg preset" class:on={path.trim() === p.path}
                      title={p.path} on:click={() => { path = p.path; scan() }}>{p.label}</button>
            {/each}
            <button class="seg preset add" on:click={savePreset}
                    title={$t('把目前路徑存成捷徑（存在 Settings 的 ingest.source_presets）')}>＋</button>
            {#if presetNote}<Mono dim style="font-size:9.5px;">{presetNote}</Mono>
            {:else if !presets.length}<Mono dim style="font-size:9.5px;">{$t('按 ＋ 把常用路徑存成捷徑')}</Mono>{/if}
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
            <div class="optlabel"><span>Max vision failures</span><Mono dim style="font-size:10px;">{$t('累計幾個幀失敗才停（0 = 第一個就停）')}</Mono></div>
          </div>
          <div class="optrow">
            <input class="ak-input num" type="number" min="0" bind:value={limit} title="limit (0 = all)" />
            <div class="optlabel"><span>Limit</span><Mono dim style="font-size:10px;">{$t('只處理前 N 個（0 = 全部）')}</Mono></div>
          </div>
        </div>

        <!-- brick 4 — real transcription pickers (options from /api/ingest/engines) -->
        <div class="field">
          <Eyebrow>Transcribe · whisper</Eyebrow>
          <div class="optrow">
            <select class="ak-input sel" bind:value={whisperGuard} title="transcription quality preset">
              <option value="">Default{engines ? ` (${(engines.whisper_modes.find((m) => m.mode === engines.default_mode) || {}).name})` : ''}</option>
              {#each (engines?.whisper_modes ?? []) as m}
                <option value={String(m.mode)}>{m.name}</option>
              {/each}
            </select>
            <div class="optlabel"><span>Model · quality</span><Mono dim style="font-size:10px;">{$t('whisper guard preset（0 快 → 4 最準，預設 4）')}</Mono></div>
          </div>
          <div class="optrow">
            <select class="ak-input sel" bind:value={language} title="force transcription language">
              <option value="">{$t('系統預設（中文 zh）')}</option>
              {#each (engines?.languages ?? []) as l}
                <option value={l.code}>{l.label} · {l.code}</option>
              {/each}
            </select>
            <div class="optlabel"><span>Language</span><Mono dim style="font-size:10px;">{$t('強制語言（留白＝系統預設，目前為中文 zh；非中文素材請選對應語言，無自動偵測）')}</Mono></div>
          </div>
        </div>

        <!-- brick 4b — vision model picker (options = installed vision-capable
             Ollama models from /api/ingest/engines). tag-pool / frames pickers
             still have no backend → shown as pending, never faked. -->
        <div class="field">
          <Eyebrow>Vision tagging · ollama</Eyebrow>
          <div class="optrow">
            <select class="ak-input sel" bind:value={visionModel} on:change={saveVisionModel}
                    title="vision model (library default)"
                    disabled={!engines || !(engines.vision_models?.length)}>
              {#each (engines?.vision_models ?? []) as m}
                <option value={m}>{m}</option>
              {/each}
            </select>
            <div class="optlabel"><span>Model{visionNote ? ` · ${visionNote}` : ''}</span><Mono dim style="font-size:10px;">{$t('全庫預設視覺模型（持久，同 Settings）')}{engines && !(engines.vision_models?.length) ? $t(' · ollama 無法連線') : ''}</Mono></div>
          </div>
          <div class="pendrow"><Mono dim>tag pool · frames</Mono><span class="pend">picker pending · brick 4b</span></div>
        </div>
      </div>

      <!-- RIGHT — manifest -->
      <div class="col manifest">
        <Eyebrow>Manifest</Eyebrow>
        {#if !manifest}
          <div class="empty"><Mono dim>{scanning ? $t('掃描中…') : $t('填路徑後按 Scan 看清單')}</Mono></div>
        {:else}
          <div class="mtotal"><Mono>{total} files · {gb} GB</Mono></div>
          <div class="mrow"><span>Video</span><Mono dim>{manifest.video.count}</Mono><Mono dim>{manifest.video.size_mb} MB</Mono></div>
          <div class="mrow"><span>Audio</span><Mono dim>{manifest.audio.count}</Mono><Mono dim>{manifest.audio.size_mb} MB</Mono></div>
          {#if manifest.unsupported.count}
            <div class="mrow skip"><span>Unsupp.</span><Mono dim>{manifest.unsupported.count}</Mono><Mono dim>skipped</Mono></div>
          {/if}
          <div class="estimated">
            <Eyebrow>Estimated</Eyebrow>
            {#if estimate}
              <div class="estrow"><span>{$t('約需')}</span><Mono>{hhmm(estimate.seconds)}</Mono></div>
              <div class="estrow dim"><span>{$t('上限')}</span><Mono dim>{hhmm(estimate.budget_s)}</Mono></div>
              <Mono dim style="font-size:9.5px;line-height:1.5;display:block;margin-top:4px;">
                {$t('依 {probed}/{of} 支的實際時長推算（成本由視覺分析的 幀數決定，不是由檔案數）。超過上限或連續 {stall}沒有進度會停止並告知原因； 已完成的素材都會留在庫裡，重跑接續。', { probed: estimate.probed, of: estimate.of, stall: hhmm(estimate.stall_s) })}
              </Mono>
            {:else}
              <span class="pend">{$t('掃描後顯示')}</span>
            {/if}
          </div>
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
      <!-- Disabled only while a start is actually in flight; every other refusal
           is a toast naming what is missing, same as Offload's Run. -->
      <button class="ak-btn ak-btn--primary" on:click={start} disabled={starting}
              title={total > 0 ? $t('開始匯入 {n} 個檔案', { n: total }) : $t('開始匯入')}>{starting ? 'starting…' : 'Start ingest →'}</button>
    </div>
  </div>
</div>

<style>
  .artboard { width: 100%; max-width: 1920px; height: 100vh; height: 100dvh; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
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
  /* No `background` here on purpose: a class beats the bare `select` rule in
     app.css, so re-declaring `transparent` would put the grey-white default
     popup back. Colour comes from that global rule. */
  .sel { flex: 0 0 196px; width: 196px; font-size: 11.5px; font-family: var(--ak-mono); color: var(--ink); border: 1px solid var(--rule-hi); border-radius: 0; padding: 6px 8px; cursor: pointer; }
  .sel:focus { outline: none; border-color: var(--ink); }

  .optrow { display: flex; align-items: center; gap: 14px; }
  .presetrow { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 5px; }
  .preset { font-size: 10px; padding: 2px 8px; }
  .preset.add { font-weight: 600; }
  .optlabel { display: flex; flex-direction: column; gap: 1px; font-size: 12px; }
  .seg { font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.08em; width: 46px; flex: 0 0 46px; padding: 5px 0; border: 1px solid var(--rule-hi); background: transparent; color: var(--quiet); cursor: pointer; }
  .seg.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); font-weight: 700; }

  .pendrow, .estimated { display: flex; align-items: baseline; justify-content: space-between; }
  .pend { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--quiet-2); border: 1px dashed var(--rule-hi); padding: 1px 6px; }

  .empty { padding: 24px 0; }
  .mtotal { padding-bottom: 8px; border-bottom: 1px solid var(--rule); margin-bottom: 4px; }
  .mrow { display: grid; grid-template-columns: 1fr auto auto; gap: 14px; padding: 5px 0; font-size: 12px; align-items: baseline; }
  .mrow.skip { color: var(--quiet); }
  .estrow { display: flex; justify-content: space-between; align-items: baseline;
            gap: 8px; font-size: 11px; margin-top: 2px; }
  .estrow.dim { opacity: .65; }
  .estimated { margin-top: 10px; }
  .noticebox { margin-top: auto; border: 1px dashed var(--rule-hi); padding: 12px; line-height: 1.5; }

  .footer { display: flex; align-items: center; gap: 12px; padding: 16px 28px; border-top: 1px solid var(--rule); }
</style>
