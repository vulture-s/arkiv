<!-- Right inspector panel: header · preview · metadata · waveform · transcript · rate. -->
<script>
  import Eyebrow from './Eyebrow.svelte'
  import Mono from './Mono.svelte'
  import Thumb from './Thumb.svelte'
  import Waveform from './Waveform.svelte'
  export let media
  export let theme = 'dark'
  // 360 footage (.insv / .360): the preview thumbnail is an equirectangular frame,
  // so show a drag-to-look sphere viewer instead of the flat still. The viewer
  // (three.js, ~600KB) is lazy-loaded only when a 360 clip is first opened, so the
  // main bundle isn't bloated for the common non-360 case.
  $: is360 = /\.(insv|360)$/i.test((media && media.name) || '')
  let Pano360 = null
  $: if (is360 && !Pano360) import('./Pano360.svelte').then((m) => (Pano360 = m.default))
  // Live overrides — all default to mock behaviour so mock screens are unchanged.
  export let thumbUrl = null // real <img> when set, else abstract Thumb
  // Real stream URL (api.streamUrl) → the preview becomes a playable <video>/<audio>
  // element instead of a static poster. This is the actual playback the
  // mock-derived inspector was missing (the old .controls overlay was a fake
  // scrubber on a still image). null → poster/placeholder behaviour unchanged.
  export let videoSrc = null
  $: useVideo = !!videoSrc && !is360 && !!media && media.kind !== 'audio'
  $: useAudio = !!videoSrc && !!media && media.kind === 'audio'
  // The player element (video or audio) — bound so clicking a transcript line
  // seeks playback to that timecode.
  let playerEl = null
  function seekTo(t) {
    if (playerEl && typeof t === 'number' && !Number.isNaN(t)) {
      playerEl.currentTime = t
      playerEl.play && playerEl.play().catch(() => {})
    }
  }
  export let pathLabel = null // file path string; null → mock /vol/... path
  export let transcriptLines = null // [[tc,text,hl],...]; null → mock lines
  export let frameDescriptions = null // string[]; when set, render a Vision block
  // Richer per-frame vision metadata for the scene timeline. When set, supersedes
  // frameDescriptions. [{tc?, description, content_type, atmosphere, energy,
  // edit_position, edit_reason, focus_score}]. null → mock screens unchanged.
  export let frameScenes = null
  export let onRate = null // (uiRating) => void; when set, rate buttons are live
  // (fmt) => void; triggers an authenticated download for this clip. When set,
  // the export buttons become live; null → mock screens keep the inert buttons.
  export let onExport = null
  // Live tag editing. tags = [{id,name,source}] → renders the editable Tags block;
  // null → no block (mock screens unchanged). onAddTag/onRemoveTag wire the writes.
  export let tags = null
  export let onAddTag = null // (name) => void
  export let onRemoveTag = null // (name) => void
  // LLM-canonicalized tag list (string[]) — semantic-deduped (生肉/生魚→肉類).
  // When present, a toggle switches the Tags block raw ⇄ canonical; default
  // canonical (the cleaner view) unless the user flipped it (persisted).
  export let canonicalTags = null
  let showCanonical =
    typeof localStorage === 'undefined' || localStorage.getItem('arkiv.tagview') !== 'raw'
  function toggleTagView() {
    showCanonical = !showCanonical
    try { localStorage.setItem('arkiv.tagview', showCanonical ? 'canonical' : 'raw') } catch (e) { /* private mode */ }
  }
  $: hasCanon = !!(canonicalTags && canonicalTags.length)
  $: useCanon = hasCanon && showCanonical
  // Live re-processing. onReprocess = async (action, opts) => {ok, message};
  // action ∈ 'retranscribe' | 'retry-vision' | 'reingest'. null → no block (mock).
  export let onReprocess = null
  export let languages = null // [{code,label}] for the retranscribe picker
  export let mediaLang = null // current clip language → default selection
  let imgFailed = false
  let tagInput = ''
  function submitTag() {
    const v = tagInput.trim()
    if (!v || !onAddTag) return
    onAddTag(v)
    tagInput = ''
  }
  // reprocess local state, reset when the selected clip changes
  let reBusy = null // action currently running, or null
  let reMsg = ''
  let reLang = 'zh'
  let lastMediaId = null
  $: if (media && media.id !== lastMediaId) {
    lastMediaId = media.id
    reBusy = null
    reMsg = ''
    reLang = mediaLang || 'zh'
  }
  async function doReprocess(action) {
    if (!onReprocess || reBusy) return
    reBusy = action
    reMsg = ''
    try {
      const opts = action === 'retranscribe' ? { language: reLang || 'zh' } : {}
      const r = await onReprocess(action, opts)
      reMsg = (r && r.message) || '完成'
    } catch (e) {
      reMsg = `失敗: ${(e && e.message) || e}`
    } finally {
      reBusy = null
    }
  }
  const RE_LANGS = [{ code: 'zh', label: '中文' }, { code: 'en', label: 'English' }]
  $: langOpts = languages && languages.length ? languages : RE_LANGS
  const EXPORT_FMTS = ['edl', 'fcpxml', 'srt']

  const MOCK_TRANSCRIPT = [
    ['00:05', '我們從上海一路騎到拉薩，第十七天。', false],
    ['00:12', '中間最難的是格爾木到沱沱河那段。', true],
    ['00:24', '氧氣比想像中還少，海拔 4800。', false],
    ['00:36', '車架被打到變形，但人沒事。', false],
  ]
  $: lines = transcriptLines ?? MOCK_TRANSCRIPT
  $: pathStr = pathLabel ?? `/vol/nas01/bicycle-diaries/raw/${media.name}`
  const rateBtns = [['good', 'Good'], ['rev', 'Review'], ['ng', 'N·G'], ['none', '—']]
</script>

<aside class="inspector">
  <div class="header">
    <Eyebrow style="margin-bottom:8px;">
      Inspector · {media.kind === 'audio' ? 'AUDIO' : `${media.fps}p · ${media.res}`}
    </Eyebrow>
    <div class="fname">{media.name}</div>
    <Mono dim style="font-size:10.5px;margin-top:4px;letter-spacing:0.04em;">
      {pathStr}
    </Mono>
  </div>

  <div class="preview" class:hasplayer={useVideo || useAudio}>
    {#if is360 && thumbUrl && !imgFailed}
      {#if Pano360}<svelte:component this={Pano360} src={thumbUrl} />{:else}<div class="panoload"><Mono dim style="font-size:11px;">360 · loading…</Mono></div>{/if}
    {:else if useVideo}
      <!-- svelte-ignore a11y-media-has-caption -->
      <video bind:this={playerEl} class="previmg" controls playsinline preload="metadata" poster={thumbUrl || undefined} src={videoSrc}></video>
    {:else if useAudio}
      {#if thumbUrl && !imgFailed}
        <img class="previmg" src={thumbUrl} alt={media.name} on:error={() => (imgFailed = true)} />
      {/if}
      <audio bind:this={playerEl} class="prevaudio" controls preload="metadata" src={videoSrc}></audio>
    {:else if thumbUrl && !imgFailed}
      <img class="previmg" src={thumbUrl} alt={media.name} on:error={() => (imgFailed = true)} />
    {:else}
      <Thumb seed={media.id} kind={media.kind} {theme} />
    {/if}
    {#if !useVideo && !useAudio}
      <!-- mock/poster fallback only: fake scrubber overlay (no real player wired) -->
      <div class="scrim"></div>
      <div class="controls">
        <Mono style="font-size:11px;color:#f3f2ee;">00:00:42</Mono>
        <div class="track">
          <div class="trackfill"></div>
          <div class="trackhead"></div>
        </div>
        <Mono style="font-size:11px;color:#f3f2ee;">{media.dur}</Mono>
      </div>
    {/if}
  </div>

  <div class="block">
    <Eyebrow style="margin-bottom:8px;">Metadata</Eyebrow>
    <div class="metagrid">
      <Mono dim>CAMERA</Mono><Mono>{media.cam}</Mono>
      <Mono dim>LENS</Mono><Mono>{media.lens}</Mono>
      <Mono dim>ISO</Mono><Mono>{media.iso} · {media.ap} · {media.fl}</Mono>
      <Mono dim>SIZE</Mono><Mono>{media.size} · {media.dur}</Mono>
    </div>
  </div>

  {#if tags || hasCanon}
    <div class="block">
      <div class="blockhead">
        <Eyebrow>Tags</Eyebrow>
        {#if hasCanon}
          <button class="tagtoggle" on:click={toggleTagView} title="原始 vision tags ⇄ LLM 精簡（語意去重）">
            {useCanon ? '精簡' : '原始'} ⇄
          </button>
        {/if}
      </div>
      {#if useCanon}
        <div class="tagrow">
          {#each canonicalTags as t}<span class="tagchip canon">{t}</span>{/each}
        </div>
      {:else if tags}
        <div class="tagrow">
          {#each tags as t (t.name)}
            <span class="tagchip" class:auto={t.source === 'auto'}>
              <span class="tagname">{t.name}</span>
              {#if onRemoveTag}
                <button class="tagx" title="移除標籤" on:click={() => onRemoveTag(t.name)}>×</button>
              {/if}
            </span>
          {/each}
          {#if tags.length === 0}
            <Mono dim style="font-size:10.5px;">（無標籤）</Mono>
          {/if}
        </div>
        {#if onAddTag}
          <form class="tagadd" on:submit|preventDefault={submitTag}>
            <input class="ak-input taginput" placeholder="加標籤…" bind:value={tagInput} />
            <button class="ak-btn tagaddbtn" type="submit" disabled={!tagInput.trim()}>＋</button>
          </form>
        {/if}
      {/if}
    </div>
  {/if}

  <div class="block">
    <div class="blockhead">
      <Eyebrow>Waveform</Eyebrow>
      <Mono dim style="font-size:9.5px;">IN 00:05 · OUT 00:42</Mono>
    </div>
    <Waveform />
  </div>

  <div class="block transcript">
    <div class="blockhead">
      <Eyebrow>Transcript · zh-Hant</Eyebrow>
      <Mono dim style="font-size:9.5px;">whisper-large · 98.2%</Mono>
    </div>
    <div class="lines">
      {#if lines.length === 0}
        <Mono dim style="font-size:11px;">（無語音 · no speech detected）</Mono>
      {:else}
        {#each lines as [tc, text, hl, t]}
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div class="line" class:seekable={typeof t === 'number' && (useVideo || useAudio)}
            on:click={() => (typeof t === 'number') && seekTo(t)}>
            <Mono dim style="font-size:10.5px;flex:0 0 36px;">{tc}</Mono>
            <span class="ttext" class:hl>{text}</span>
          </div>
        {/each}
      {/if}
    </div>
  </div>

  {#if frameScenes && frameScenes.length}
    <div class="block">
      <div class="blockhead">
        <Eyebrow>場景時間軸 · qwen3-vl</Eyebrow>
        <Mono dim style="font-size:9.5px;">{frameScenes.length} 場景</Mono>
      </div>
      <div class="scenes">
        {#each frameScenes as sc, i}
          <div class="scene">
            <div class="scenetime">
              <Mono dim style="font-size:10px;">{sc.tc || `f${i + 1}`}</Mono>
              <span class="dot" class:hi={(sc.focus_score ?? 0) >= 4} class:lo={(sc.focus_score ?? 3) <= 2} title="focus {sc.focus_score ?? '—'}"></span>
            </div>
            <div class="scenebody">
              <div class="chips">
                {#if sc.content_type && sc.content_type !== 'Undefined'}<span class="chip">{sc.content_type}</span>{/if}
                {#if sc.edit_position}<span class="chip pos">{sc.edit_position}</span>{/if}
                {#if sc.atmosphere}<span class="chip dim">{sc.atmosphere}</span>{/if}
                {#if sc.energy}<span class="chip dim">能量 {sc.energy}</span>{/if}
              </div>
              {#if sc.description}<div class="scenedesc">{sc.description}</div>{/if}
              {#if sc.edit_reason}<div class="scenereason">▸ {sc.edit_reason}</div>{/if}
            </div>
          </div>
        {/each}
      </div>
    </div>
  {:else if frameDescriptions}
    <div class="block">
      <div class="blockhead">
        <Eyebrow>Vision · qwen3-vl</Eyebrow>
        <Mono dim style="font-size:9.5px;">{frameDescriptions.length} frame(s)</Mono>
      </div>
      <div class="lines">
        {#each frameDescriptions as desc, i}
          <div class="line">
            <Mono dim style="font-size:10.5px;flex:0 0 36px;">f{i + 1}</Mono>
            <span class="ttext">{desc}</span>
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#if onReprocess}
    <div class="block reproc">
      <Eyebrow style="margin-bottom:8px;">Reprocess</Eyebrow>
      <div class="reprow">
        <select class="ak-input relang" bind:value={reLang} disabled={!!reBusy} title="重新轉錄語言">
          {#each langOpts as l}
            <option value={l.code}>{l.label || l.code}</option>
          {/each}
        </select>
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('retranscribe')}>
          {reBusy === 'retranscribe' ? '轉錄中…' : '重轉錄'}
        </button>
      </div>
      <div class="reprow">
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('retry-vision')}>
          {reBusy === 'retry-vision' ? '分析中…' : '重試視覺'}
        </button>
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('reingest')}>
          {reBusy === 'reingest' ? '重建中…' : '完整重建'}
        </button>
      </div>
      <div class="reprow">
        <button class="ak-btn rebtn" disabled={!!reBusy} on:click={() => doReprocess('proxy')}>
          {reBusy === 'proxy' ? '排入中…' : '建立 proxy'}
        </button>
      </div>
      {#if reMsg}<Mono dim style="font-size:10px;line-height:1.45;display:block;">{reMsg}</Mono>{/if}
    </div>
  {/if}

  <div class="rate">
    <Eyebrow>Rate</Eyebrow>
    <div class="ratebtns">
      {#each rateBtns as [r, label]}
        <button
          class="ratebtn"
          class:active={media.rating === r}
          class:rev={r === 'rev'}
          on:click={() => onRate && onRate(r)}
        >{label}</button>
      {/each}
    </div>
    <div class="exports">
      {#if onExport}
        {#each EXPORT_FMTS as fmt}
          <button class="ak-btn exp" on:click={() => onExport(fmt)}>{fmt.toUpperCase()}</button>
        {/each}
      {:else}
        <button class="ak-btn exp">EDL</button>
        <button class="ak-btn exp">FCPXML</button>
        <button class="ak-btn exp">SRT</button>
      {/if}
    </div>
  </div>
</aside>

<style>
  .inspector {
    border-left: 1px solid var(--rule); display: flex; flex-direction: column;
    min-height: 0; overflow: hidden;
  }
  .header { padding: 18px 18px 16px; border-bottom: 1px solid var(--rule); }
  .fname {
    font-family: var(--ak-mono); font-size: 13px; font-weight: 500;
    letter-spacing: 0.005em; line-height: 1.3; color: var(--ink); word-break: break-all;
  }
  .preview {
    position: relative; aspect-ratio: 16 / 9; background: var(--surface-2);
    border-bottom: 1px solid var(--rule);
  }
  .previmg { width: 100%; height: 100%; object-fit: cover; display: block; }
  /* the real player: contain (don't crop footage) on black; audio sits at the bottom */
  video.previmg { object-fit: contain; background: #000; }
  .prevaudio { position: absolute; left: 12px; right: 12px; bottom: 12px; width: auto; }
  .panoload { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: var(--surface-2); }
  .scrim {
    position: absolute; left: 0; right: 0; bottom: 0; height: 40%;
    background-image: linear-gradient(to top, rgba(0, 0, 0, 0.55), transparent); pointer-events: none;
  }
  .controls { position: absolute; left: 12px; bottom: 12px; right: 12px; display: flex; align-items: center; gap: 10px; }
  .track { flex: 1; height: 2px; background: rgba(243, 242, 238, 0.25); position: relative; }
  .trackfill { position: absolute; left: 0; top: 0; bottom: 0; width: 25%; background: #f3f2ee; }
  .trackhead { position: absolute; left: 25%; top: -3px; width: 1px; height: 8px; background: #f3f2ee; }
  .block { padding: 14px 18px; border-bottom: 1px solid var(--rule); }
  .metagrid { display: grid; grid-template-columns: 64px 1fr; row-gap: 4px; column-gap: 12px; font-size: 11.5px; }
  .blockhead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
  /* flex column + min-height:0 lets .lines scroll inside the inspector instead of
     clipping a long transcript (was overflow:hidden → couldn't scroll the subs). */
  .transcript { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .lines { display: flex; flex-direction: column; gap: 8px; font-size: 12px; line-height: 1.5; overflow-y: auto; min-height: 0; padding-right: 4px; }
  .line { display: flex; gap: 10px; }
  .line.seekable { cursor: pointer; }
  .line.seekable:hover .ttext { color: var(--invert); }
  .ttext { color: var(--ink); }
  .ttext.hl { border-bottom: 1px solid var(--invert); padding-bottom: 1px; }
  /* tags */
  .tagrow { display: flex; flex-wrap: wrap; gap: 5px; }
  .tagchip {
    display: inline-flex; align-items: center; gap: 4px;
    font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.03em;
    padding: 2px 4px 2px 7px; border: 1px solid var(--rule-hi); color: var(--ink);
    background: var(--surface-2);
  }
  .tagchip.auto { border-style: dashed; border-color: var(--rule); color: var(--ink-2); }
  .tagchip.canon { border-color: var(--invert); }
  .tagtoggle {
    appearance: none; background: transparent; border: 1px solid var(--rule);
    font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.06em;
    color: var(--ink-2); padding: 2px 6px; cursor: pointer;
  }
  .tagtoggle:hover { color: var(--ink); border-color: var(--ink); }
  .tagchip .tagname { white-space: nowrap; }
  .tagx {
    appearance: none; background: transparent; border: none; cursor: pointer;
    color: var(--ink-2); font-size: 13px; line-height: 1; padding: 0 2px;
  }
  .tagx:hover { color: var(--cyan); }
  .tagadd { display: flex; gap: 6px; margin-top: 8px; }
  .taginput { flex: 1; font-size: 11px; padding: 5px 8px; }
  .tagaddbtn { flex: 0 0 auto; padding: 5px 10px; }
  /* reprocess */
  .reproc { display: flex; flex-direction: column; gap: 6px; }
  .reprow { display: flex; gap: 6px; }
  .relang { flex: 0 0 auto; font-size: 11px; padding: 5px 6px; }
  .rebtn { flex: 1; font-size: 10px; }
  .rebtn:disabled { opacity: 0.5; cursor: default; }
  .rate { padding: 14px 18px; display: flex; flex-direction: column; gap: 10px; }
  .ratebtns { display: flex; gap: 4px; }
  .ratebtn {
    flex: 1; font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 7px 0; background: transparent; color: var(--ink-2);
    border: 1px solid var(--rule); cursor: pointer; font-weight: 400;
  }
  .ratebtn.rev { border: 1px dashed var(--rule-hi); }
  .ratebtn.active { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); font-weight: 700; }
  .exports { display: flex; gap: 6px; margin-top: 6px; }
  .exp { flex: 1; }
  /* scene timeline */
  .scenes { display: flex; flex-direction: column; }
  .scene { display: flex; gap: 10px; padding: 8px 0; border-top: 1px solid var(--rule); }
  .scene:first-child { border-top: none; }
  .scenetime { flex: 0 0 40px; display: flex; align-items: center; gap: 5px; padding-top: 1px; }
  .dot { width: 5px; height: 5px; border-radius: 50%; background: var(--ink-2); flex: 0 0 auto; }
  .dot.hi { background: var(--invert); }
  .dot.lo { background: var(--quiet); }
  .scenebody { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .chip {
    font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.04em;
    padding: 2px 5px; border: 1px solid var(--rule); color: var(--ink-2); white-space: nowrap;
  }
  .chip.pos { border-color: var(--rule-hi); color: var(--ink); }
  .chip.dim { color: var(--quiet); }
  .scenedesc { font-size: 11.5px; line-height: 1.45; color: var(--ink); }
  .scenereason { font-size: 10.5px; line-height: 1.4; color: var(--quiet); font-style: italic; }
</style>
