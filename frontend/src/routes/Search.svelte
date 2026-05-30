<!-- Seg 5 — Screen 4: cross-project search. Hero query + facets + result
     groups by project (with NAS-unmounted empty state). -->
<script>
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Thumb from '../lib/Thumb.svelte'

  const theme = 'dark'
  const facets = [
    ['All projects · 4', true, false], ['Current only', false, false], ['', false, true],
    ['Video · 124', false, false], ['Audio · 38', false, false],
    ['Transcript only', false, false], ['Tags only', false, false], ['', false, true],
  ]
  const groups = [
    {
      project: 'Bicycle Diaries', count: 3, total: 247, health: 'ok',
      results: [
        { id: 1, name: 'A7S3_C001_240515.mov', dur: '00:02:47', score: 0.94, snippet: '氧氣比想像中還少，海拔 4800。', tc: '00:24', tags: ['cycling', 'tibet', 'documentary'] },
        { id: 12, name: 'A7S3_C007_240518.mov', dur: '00:02:04', score: 0.81, snippet: '高度太高，氧氣稀薄到呼吸都吃力。', tc: '00:48', tags: ['cycling', 'tibet'] },
        { id: 4, name: 'A7S3_C003_240516.mov', dur: '00:01:12', score: 0.72, snippet: '其實主要是氧氣的問題，不是體力。', tc: '00:36', tags: ['interview', 'tibet'] },
      ],
    },
    {
      project: 'vulture.s reels', count: 2, total: 89, health: 'ok',
      results: [
        { id: 3, name: 'INTERVIEW_HEVIN_01.wav', dur: '00:18:04', score: 0.66, snippet: '我那時候完全沒氧氣可以呼吸的感覺。', tc: '04:12', tags: ['interview', 'podcast'] },
        { id: 9, name: 'A7S3_C005_240517.mov', dur: '00:00:42', score: 0.58, snippet: '氧氣罐用完了，只能慢慢撐。', tc: '00:08', tags: ['portrait', 'documentary'] },
      ],
    },
    {
      project: 'Furutech RCA spot', count: 2, total: 152, health: 'ok',
      results: [
        { id: 7, name: 'GH6_4825.mp4', dur: '00:00:14', score: 0.42, snippet: '聲音密度像氧氣一樣稀薄又透明。', tc: '00:04', tags: ['b-roll', 'product'] },
      ],
    },
    { project: 'KOL_2026Q1', count: '?', total: 38, health: 'unmounted', results: [] },
  ]

  const Q = '氧氣'
  const hl = (text) => {
    const i = text.indexOf(Q)
    return i === -1 ? { b: text, m: '', a: '' } : { b: text.slice(0, i), m: Q, a: text.slice(i + Q.length) }
  }
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <button class="ak-btn">Esc · CLOSE SEARCH</button>
  </div>

  <div class="main">
    <div class="hero">
      <Eyebrow style="margin-bottom:10px;">Search · cross-project federation</Eyebrow>
      <div class="queryrow">
        <Mono dim style="font-size:26px;font-weight:400;">⌕</Mono>
        <div class="querywrap">
          <div class="ak-display query">氧氣比想像中還少<span class="caret"></span></div>
          <div class="queryunderline"></div>
        </div>
        <Mono dim style="font-size:11px;">⌘K</Mono>
      </div>
      <div class="facets">
        {#each facets as [label, active, sep]}
          {#if sep}<div class="fvrule"></div>{:else}<button class="facet" class:active>{label}</button>{/if}
        {/each}
        <Mono dim style="font-size:10.5px;">7 matches · 4 projects · 0.043s · semantic + lexical</Mono>
      </div>
    </div>

    <div class="results">
      {#each groups as g}
        {@const offline = g.health !== 'ok'}
        <section class="rgroup" style="opacity:{offline ? 0.55 : 1};">
          <div class="ghead">
            <Mono dim style="font-size:10px;letter-spacing:0.1em;">PROJECT</Mono>
            <div class="ak-display gproj">{g.project}</div>
            <Mono dim style="font-size:10.5px;">{g.count} of {g.total}</Mono>
            <div class="grow"></div>
            {#if offline}
              <div class="offline">◇ NAS unmounted · cannot search</div>
            {:else}
              <Mono dim style="font-size:9.5px;letter-spacing:0.08em;">● ONLINE · 4.8 TB</Mono>
            {/if}
          </div>

          {#if g.results.length === 0}
            <div class="emptyresult">Mount NAS to search this project · ~/.arkiv-projects.json</div>
          {/if}

          <div class="rows">
            {#each g.results as r, i (r.id)}
              {@const p = hl(r.snippet)}
              <div class="rrow" class:first={i === 0}>
                <div class="rthumb">
                  <Thumb seed={r.id} kind="video" {theme} />
                  <Mono style="position:absolute;bottom:2px;right:3px;font-size:9px;color:#f3f2ee;background:rgba(10,10,12,.78);padding:1px 3px;">{r.dur}</Mono>
                </div>
                <div class="rcontent">
                  <div class="rtop">
                    <Mono style="font-size:11.5px;font-weight:500;color:var(--ink);">{r.name}</Mono>
                    <Mono dim style="font-size:9.5px;">{r.tc}</Mono>
                    <div class="rtags">{#each r.tags as t}<span class="rtag">{t}</span>{/each}</div>
                  </div>
                  <div class="snippet">{p.b}<span class="mark">{p.m}</span>{p.a}</div>
                </div>
                <div class="rscore">
                  <Mono style="font-size:13px;font-weight:600;color:var(--ink);">{r.score.toFixed(2)}</Mono>
                  <Mono dim style="font-size:9px;display:block;margin-top:1px;letter-spacing:0.08em;">SCORE</Mono>
                </div>
                <div class="raction"><button class="ak-btn openbtn">Open →</button></div>
              </div>
            {/each}
          </div>
        </section>
      {/each}
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .main { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .hero { padding: 24px 64px 18px; border-bottom: 1px solid var(--rule); }
  .queryrow { display: flex; align-items: baseline; gap: 16px; margin-bottom: 12px; }
  .querywrap { flex: 1; position: relative; }
  .query { font-size: 34px; letter-spacing: -0.03em; line-height: 1.05; color: var(--ink); }
  .caret { display: inline-block; width: 2px; height: 28px; background: var(--ink); margin-left: 5px; vertical-align: middle; }
  .queryunderline { height: 1px; background: var(--ink); margin-top: 10px; }
  .facets { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .facet { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; padding: 5px 10px; background: transparent; color: var(--ink-2); border: 1px solid var(--rule); cursor: pointer; line-height: 1; }
  .facet.active { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
  .fvrule { width: 1px; height: 14px; background: var(--rule); margin: 0 4px; }
  .results { flex: 1; overflow: hidden; padding: 14px 64px 18px; }
  .rgroup { margin-bottom: 16px; }
  .ghead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
  .gproj { font-size: 18px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .offline { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.1em; text-transform: uppercase; padding: 3px 7px; border: 1px dashed var(--rule-hi); color: var(--ink-2); }
  .emptyresult { padding: 12px 16px; font-family: var(--ak-mono); font-size: 10.5px; color: var(--quiet); text-align: center; letter-spacing: 0.05em; }
  .rrow { display: grid; grid-template-columns: 100px 1fr 60px 78px; gap: 14px; align-items: center; padding: 5px 0; border-top: 1px solid var(--rule); cursor: pointer; }
  .rrow.first { border-top: none; }
  .rthumb { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }
  .rcontent { min-width: 0; }
  .rtop { display: flex; align-items: baseline; gap: 8px; }
  .rtags { display: flex; gap: 4px; }
  .rtag { font-family: var(--ak-mono); font-size: 9px; padding: 1px 4px; border: 1px solid var(--rule); color: var(--quiet); line-height: 1.2; }
  .snippet { margin-top: 3px; font-size: 12.5px; color: var(--ink-2); line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mark { background: var(--invert); color: var(--invert-ink); padding: 0 2px; }
  .rscore { text-align: right; }
  .raction { display: flex; justify-content: flex-end; }
  .openbtn { padding: 5px 9px; }
</style>
