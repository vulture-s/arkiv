<!-- Right inspector panel: header · preview · metadata · waveform · transcript · rate. -->
<script>
  import Eyebrow from './Eyebrow.svelte'
  import Mono from './Mono.svelte'
  import Thumb from './Thumb.svelte'
  import Waveform from './Waveform.svelte'
  export let media
  export let theme = 'dark'
  // Live overrides — all default to mock behaviour so mock screens are unchanged.
  export let thumbUrl = null // real <img> when set, else abstract Thumb
  export let pathLabel = null // file path string; null → mock /vol/... path
  export let transcriptLines = null // [[tc,text,hl],...]; null → mock lines
  export let frameDescriptions = null // string[]; when set, render a Vision block
  export let onRate = null // (uiRating) => void; when set, rate buttons are live
  let imgFailed = false

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

  <div class="preview">
    {#if thumbUrl && !imgFailed}
      <img class="previmg" src={thumbUrl} alt={media.name} on:error={() => (imgFailed = true)} />
    {:else}
      <Thumb seed={media.id} kind={media.kind} {theme} />
    {/if}
    <div class="scrim"></div>
    <div class="controls">
      <Mono style="font-size:11px;color:#f3f2ee;">00:00:42</Mono>
      <div class="track">
        <div class="trackfill"></div>
        <div class="trackhead"></div>
      </div>
      <Mono style="font-size:11px;color:#f3f2ee;">{media.dur}</Mono>
    </div>
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
        {#each lines as [tc, text, hl]}
          <div class="line">
            <Mono dim style="font-size:10.5px;flex:0 0 36px;">{tc}</Mono>
            <span class="ttext" class:hl>{text}</span>
          </div>
        {/each}
      {/if}
    </div>
  </div>

  {#if frameDescriptions}
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
      <button class="ak-btn exp">EDL</button>
      <button class="ak-btn exp">FCPXML</button>
      <button class="ak-btn exp">SRT</button>
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
  .transcript { flex: 1; overflow: hidden; }
  .lines { display: flex; flex-direction: column; gap: 8px; font-size: 12px; line-height: 1.5; }
  .line { display: flex; gap: 10px; }
  .ttext { color: var(--ink); }
  .ttext.hl { border-bottom: 1px solid var(--invert); padding-bottom: 1px; }
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
</style>
