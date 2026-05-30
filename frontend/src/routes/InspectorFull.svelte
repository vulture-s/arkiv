<!-- Seg 4 — Screen 3: Inspector full state. Single file open full-screen.
     Own simplified top bar + breadcrumb + 2-col split (preview/scenes/wf/tags
     | metadata/transcript/rate/export). Helpers (scenes/waveform/markers) inline. -->
<script>
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Thumb from '../lib/Thumb.svelte'
  import { MEDIA } from '../lib/mockData.js'

  const theme = 'dark'
  const m = MEDIA[0]

  const scenes = [
    { w: 12, sel: false }, { w: 8, sel: false }, { w: 18, sel: true }, { w: 14, sel: false },
    { w: 6, sel: false }, { w: 22, sel: false }, { w: 10, sel: false }, { w: 10, sel: false },
  ]
  const scenesTotal = scenes.reduce((a, s) => a + s.w, 0)

  const N = 160
  const bigBars = Array.from({ length: N }, (_, i) => {
    const v = Math.sin(i * 0.42) * 0.4 + Math.sin(i * 0.13 + 1.2) * 0.32 + Math.sin(i * 0.7 + 0.4) * 0.28
    return Math.min(1, Math.abs(v) * (0.5 + Math.abs(Math.sin(i * 0.09)) * 0.5))
  }).map((v, i) => ({ h: Math.max(2, v * 70), inWin: i / N > 0.05 && i / N < 0.78 }))

  const tags = [
    ['cycling', 'auto'], ['road', 'auto'], ['interview', 'auto'], ['tibet', 'auto'],
    ['documentary', 'auto'], ['day-17', 'manual'], ['hero-shot', 'manual'],
  ]
  const meta = [
    ['CODEC', 'H.265 / HEVC · 10-bit 4:2:2'], ['RESOLUTION', '3840 × 2160 · 24.000 fps'],
    ['BITRATE', '240 Mbps · CBR'], ['COLOR', 'S-Log3 · S-Gamut3.Cine'],
    ['DURATION', '00:02:47.083 · 4010 frames'], ['SIZE', '1.44 GB'],
    ['CAMERA', 'Sony α7S III · ILCE-7SM3'], ['LENS', 'FE 24-70mm f/2.8 GM'],
    ['EXPOSURE', 'ISO 800 · f/2.8 · 1/50s · 35mm'], ['CREATED', '2026-05-15 14:32:08 +0800'],
    ['INGESTED', '2026-05-16 02:14:33 +0800'],
  ]
  const langTabs = [['zh-Hant', true], ['en', false], ['ja', false]]
  const lines = [
    ['00:00.214', '今天是第十七天，我們從上海一路騎到拉薩。', false],
    ['00:05.612', '中間最難的是格爾木到沱沱河這段。', true],
    ['00:12.408', '海拔從 2800 一路爬到 4800，氧氣比想像中還少。', false],
    ['00:24.811', '車架被打到變形，但人沒事。', true],
    ['00:32.215', '想起出發前認識的那群人，現在不知道怎麼樣了。', false],
    ['00:39.602', '明天還要再翻一座山。', false],
  ]
  const rateBtns = [['good', 'Good'], ['rev', 'Review'], ['ng', 'N·G'], ['none', '—']]
  const exports = ['EDL', 'FCPXML', 'SRT', 'VTT', 'CSV']
</script>

<div class="artboard" data-theme={theme}>
  <!-- top bar -->
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <button class="ak-btn">⌕ Search</button>
    <button class="ak-btn ak-btn--primary">+ Ingest</button>
    <button class="ak-btn">···</button>
  </div>

  <!-- breadcrumb -->
  <div class="crumb">
    <Mono dim style="font-size:11px;">← BACK TO GRID</Mono>
    <div class="vrule"></div>
    <Mono dim style="font-size:11px;letter-spacing:0.04em;">Bicycle Diaries  /  Day 17 · Geermu → Tuotuohe  /  <span class="ink">{m.name}</span></Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">3 of 247</Mono>
    <div class="navbtns">
      <button class="ak-btn navbtn">← Prev</button>
      <button class="ak-btn navbtn">Next →</button>
    </div>
  </div>

  <!-- split -->
  <div class="split">
    <!-- LEFT -->
    <div class="left">
      <div class="preview">
        <Thumb seed={m.id} kind={m.kind} {theme} />
        <div class="playbtn"><div class="tri"></div></div>
        <div class="pscrim"></div>
        <div class="pchrome">
          <Mono style="font-size:12px;color:#f3f2ee;">00:00:42</Mono>
          <div class="track"><div class="trackfill"></div><div class="trackhead"></div></div>
          <Mono style="font-size:12px;color:#f3f2ee;">{m.dur}</Mono>
          <div class="pvrule"></div>
          <Mono style="font-size:11px;color:#f3f2ee;">1× ▸ ⤓</Mono>
        </div>
      </div>

      <div class="block">
        <div class="bhead"><Eyebrow>Scenes · cut detection</Eyebrow><Mono dim style="font-size:10px;">8 scenes · pyscenedetect 0.6.4</Mono></div>
        <div class="scenes">
          {#each scenes as s, i}
            <div class="scene" class:sel={s.sel} style="width:{(s.w / scenesTotal) * 100}%;">
              <Thumb seed={i * 3 + 7} kind="video" {theme} />
              <div class="scenenum">{String(i + 1).padStart(2, '0')}</div>
            </div>
          {/each}
        </div>
      </div>

      <div class="block">
        <div class="bhead">
          <Eyebrow>Audio waveform</Eyebrow>
          <div class="wfmeta"><Mono dim style="font-size:10px;">IN  00:05.214</Mono><Mono dim style="font-size:10px;">OUT 00:42.108</Mono><Mono dim style="font-size:10px;">SEL 00:36.894</Mono></div>
        </div>
        <div class="bigwf">
          <svg viewBox="0 0 160 78" preserveAspectRatio="none" class="bigwfsvg">
            {#each bigBars as b, i}
              <rect x={i + 0.1} y={(78 - b.h) / 2} width="0.8" height={b.h} fill={b.inWin ? 'var(--ink)' : 'var(--quiet-2)'} />
            {/each}
          </svg>
          <div class="bmark" style="left:5%;"><div class="bmarklabel">IN</div></div>
          <div class="bmark" style="left:78%;"><div class="bmarklabel">OUT</div></div>
          <div class="bplayhead"><div class="bplaydot"></div></div>
        </div>
      </div>

      <div class="block tagsblock">
        <Eyebrow style="margin-bottom:10px;">Tags · 5 auto · 2 manual</Eyebrow>
        <div class="tags">
          {#each tags as [t, src]}
            <span class="tag" class:manual={src === 'manual'}>{t}{#if src === 'manual'}<Mono dim style="font-size:9px;letter-spacing:0.06em;">✕</Mono>{/if}</span>
          {/each}
          <span class="addtag">+ add tag</span>
        </div>
      </div>
    </div>

    <!-- RIGHT -->
    <div class="right">
      <div class="rblock">
        <Eyebrow style="margin-bottom:6px;">Inspector · full</Eyebrow>
        <div class="fname">{m.name}</div>
        <Mono dim style="font-size:10.5px;margin-top:4px;letter-spacing:0.03em;">/vol/nas01/bicycle-diaries/raw/{m.name}</Mono>
        <Mono dim style="font-size:10px;margin-top:6px;display:block;">SHA-256 · 7f3e8a92c1b4d5f6e2a8c9d1b3e5f7a9c2b4d6e8f1a3c5d7e9b1f3a5c7d9e1f3</Mono>
      </div>

      <div class="rblock">
        <Eyebrow style="margin-bottom:10px;">Technical metadata</Eyebrow>
        <div class="metagrid">
          {#each meta as [k, v]}<Mono dim>{k}</Mono><Mono>{v}</Mono>{/each}
        </div>
      </div>

      <div class="rblock transcript">
        <div class="bhead"><Eyebrow>Transcript</Eyebrow><Mono dim style="font-size:9.5px;">whisper-large-v3 · conf 98.2%</Mono></div>
        <div class="langtabs">
          {#each langTabs as [l, on]}<button class="langtab" class:on>{l}</button>{/each}
        </div>
        <div class="lines">
          {#each lines as [tc, text, hl]}
            <div class="line"><Mono dim style="font-size:10.5px;flex:0 0 64px;padding-top:2px;">{tc}</Mono><span class="ttext" class:hl>{text}</span></div>
          {/each}
        </div>
      </div>

      <div class="rblock rate">
        <div class="bhead"><Eyebrow>Rate</Eyebrow><Mono dim style="font-size:9.5px;">last edit: 2026-05-24 23:08</Mono></div>
        <div class="ratebtns">
          {#each rateBtns as [r, label]}<button class="ratebtn" class:active={m.rating === r} class:rev={r === 'rev'}>{label}</button>{/each}
        </div>
        <Eyebrow style="margin-top:4px;">Export</Eyebrow>
        <div class="exports">
          {#each exports as f}<button class="ak-btn expbtn">{f}</button>{/each}
        </div>
      </div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 56px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .ink { color: var(--ink); }
  .vrule { width: 1px; height: 14px; background: var(--rule); }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .crumb { display: flex; align-items: center; gap: 14px; padding: 0 24px; border-bottom: 1px solid var(--rule); }
  .navbtns { display: flex; gap: 1px; }
  .navbtn { padding: 6px 12px; }

  .split { display: grid; grid-template-columns: 1fr 460px; min-height: 0; overflow: hidden; }
  .left { border-right: 1px solid var(--rule); display: flex; flex-direction: column; min-height: 0; }
  .preview { position: relative; flex: 0 0 auto; height: 420px; background: var(--surface-2); overflow: hidden; }
  .playbtn { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 56px; height: 56px; box-shadow: inset 0 0 0 1px #f3f2ee; display: flex; align-items: center; justify-content: center; cursor: pointer; }
  .tri { width: 0; height: 0; border-left: 13px solid #f3f2ee; border-top: 8px solid transparent; border-bottom: 8px solid transparent; margin-left: 4px; }
  .pscrim { position: absolute; left: 0; right: 0; bottom: 0; height: 80px; background-image: linear-gradient(to top, rgba(0, 0, 0, 0.65), transparent); }
  .pchrome { position: absolute; left: 24px; right: 24px; bottom: 22px; display: flex; align-items: center; gap: 14px; }
  .track { flex: 1; height: 2px; background: rgba(243, 242, 238, 0.25); position: relative; }
  .trackfill { position: absolute; left: 0; top: 0; bottom: 0; width: 25%; background: #f3f2ee; }
  .trackhead { position: absolute; left: 25%; top: -4px; width: 1px; height: 10px; background: #f3f2ee; }
  .pvrule { width: 1px; height: 16px; background: rgba(243, 242, 238, 0.25); }

  .block { padding: 18px 24px 14px; border-bottom: 1px solid var(--rule); }
  .bhead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
  .scenes { display: flex; gap: 1px; height: 56px; background: var(--rule); }
  .scene { position: relative; overflow: hidden; background: var(--surface-2); }
  .scene.sel { box-shadow: inset 0 0 0 1px var(--invert); }
  .scenenum { position: absolute; bottom: 2px; left: 4px; font-family: var(--ak-mono); font-size: 9px; color: #f3f2ee; background: rgba(10, 10, 12, 0.7); padding: 1px 3px; letter-spacing: 0.02em; }
  .wfmeta { display: flex; gap: 14px; }
  .bigwf { position: relative; height: 78px; }
  .bigwfsvg { width: 100%; height: 100%; display: block; }
  .bmark { position: absolute; top: -4px; bottom: -4px; width: 1px; background: var(--invert); }
  .bmarklabel { position: absolute; top: -14px; left: -10px; font-family: var(--ak-mono); font-size: 8.5px; letter-spacing: 0.08em; color: var(--ink); padding: 1px 3px; background: var(--bg); border: 1px solid var(--invert); line-height: 1; }
  .bplayhead { position: absolute; top: -8px; bottom: -8px; left: 32%; width: 1px; background: var(--invert); }
  .bplaydot { position: absolute; top: -3px; left: -3px; width: 7px; height: 7px; background: var(--invert); }
  .tagsblock { flex: 1; overflow: hidden; border-bottom: none; padding: 16px 24px; }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; }
  .tag { font-family: var(--ak-mono); font-size: 11.5px; line-height: 1; padding: 5px 9px; border: 1px solid var(--rule); color: var(--ink-2); display: inline-flex; align-items: center; gap: 6px; }
  .tag.manual { border-color: var(--ink); }
  .addtag { font-family: var(--ak-mono); font-size: 11.5px; padding: 5px 9px; border: 1px dashed var(--rule-hi); color: var(--quiet); }

  .right { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .rblock { padding: 16px 24px; border-bottom: 1px solid var(--rule); }
  .rblock:first-child { padding: 20px 24px 18px; }
  .fname { font-family: var(--ak-mono); font-size: 14px; font-weight: 500; line-height: 1.3; word-break: break-all; color: var(--ink); }
  .metagrid { display: grid; grid-template-columns: 90px 1fr; row-gap: 5px; column-gap: 14px; font-size: 12px; }
  .transcript { flex: 1; overflow: hidden; display: flex; flex-direction: column; min-height: 0; }
  .langtabs { display: flex; margin-bottom: 10px; border-bottom: 1px solid var(--rule); }
  .langtab { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 6px 10px; background: transparent; color: var(--quiet); border: none; border-bottom: 2px solid transparent; margin-bottom: -1px; cursor: pointer; }
  .langtab.on { color: var(--ink); border-bottom-color: var(--invert); font-weight: 700; }
  .lines { flex: 1; overflow: hidden; display: flex; flex-direction: column; gap: 9px; font-size: 12.5px; line-height: 1.45; }
  .line { display: flex; gap: 12px; }
  .ttext { color: var(--ink); }
  .ttext.hl { border-bottom: 1px solid var(--invert); padding-bottom: 1px; }
  .rate { display: flex; flex-direction: column; gap: 10px; border-bottom: none; }
  .ratebtns { display: flex; gap: 4px; }
  .ratebtn { flex: 1; font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; padding: 8px 0; background: transparent; color: var(--ink-2); border: 1px solid var(--rule); cursor: pointer; }
  .ratebtn.rev { border: 1px dashed var(--rule-hi); }
  .ratebtn.active { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); font-weight: 700; }
  .exports { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; }
  .expbtn { padding: 7px 0; }
</style>
