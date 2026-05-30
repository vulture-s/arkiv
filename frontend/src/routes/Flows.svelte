<!-- Seg 8 — Round-3 flows (B1–B4): ingest setup / query builder /
     project registry / conflict merge. Stacked for audit. Helpers inline. -->
<script>
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Thumb from '../lib/Thumb.svelte'
  import Rating from '../lib/Rating.svelte'
  import { MEDIA } from '../lib/mockData.js'

  const theme = 'dark'
  const bd = Array(12)

  // B1 ingest-setup
  const srcStrategy = [['copy', 'Copy', true], ['move', 'Move', false], ['link', 'Link in place', false]]
  const onConflict = [['skip', 'Skip dup', true], ['rename', 'Rename', false], ['overwrite', 'Overwrite', false]]
  const setupLangs = [['zh-Hant', true], ['en', true], ['ja', false], ['auto', true]]
  const tagPool = [['auto', 'Auto', true], ['user', 'User pool', false], ['hybrid', 'Hybrid', false]]
  const manifest = [['Video', '.mov', 16, '15.8 GB'], ['Audio', '.wav', 5, '0.4 GB'], ['Unsupp.', '.crw', 2, '— skipped']]
  const estimate = [['PROBE', '0:30 · all files'], ['TRANSCRIBE', '3:42 · 5 audio + 16 video'], ['TAG', '2:18 · llava GPU']]

  // B2 query-builder
  const clauses = [
    ['transcript', 'contains', '氧氣 OR oxygen'], ['camera', 'equals', 'Sony α7S III'],
    ['tag', 'any of', 'cycling, tibet'], ['rating', 'between', 'REV → GOOD'],
    ['iso', 'between', '400 — 1600'], ['created', 'between', '2026-05-15 → 2026-05-19'],
    ['duration', 'equals', '> 30s'],
  ]
  const boolOpts = [['and', 'All  (AND)', true], ['or', 'Any  (OR)', false], ['custom', 'Custom expr', false]]
  const qbPreview = MEDIA.filter((m) => m.rating !== 'none').slice(0, 8)

  // B3 project-registry
  const PROJECTS_FULL = [
    { name: 'Bicycle Diaries', count: 247, size: '4.8 TB', last: '2026-05-26 14:08', mount: 'mounted', path: '/vol/nas01/bicycle-diaries', active: true, health: 'ok' },
    { name: 'vulture.s reels', count: 89, size: '1.2 TB', last: '2026-05-22 18:32', mount: 'mounted', path: '/vol/nas01/vulture-s-reels', active: false, health: 'ok' },
    { name: 'Furutech RCA spot', count: 152, size: '2.1 TB', last: '2026-05-20 11:48', mount: 'mounted', path: '/vol/nas01/furutech-rca', active: false, health: 'ok' },
    { name: 'KOL_2026Q1', count: 38, size: '—', last: '2026-04-18 09:12', mount: 'unmounted', path: '/Volumes/EXT_B/kol-2026q1', active: false, health: 'warn' },
    { name: 'Bonefolder (archived)', count: 412, size: '6.4 TB', last: '2024-12-31 23:59', mount: 'archived', path: '/vol/cold/bonefolder', active: false, health: 'archived' },
  ]
  const regCols = '24px 1.6fr 70px 70px 1fr 110px 110px'
  const mountText = { mounted: '● MOUNTED · NAS01', unmounted: '◇ UNMOUNTED · /Volumes/EXT_B', archived: '◇ ARCHIVED · COLD' }
  const mountAction = { mounted: 'Open', unmounted: 'Mount', archived: 'Restore' }
  const orphans = [
    { name: 'A7S3_C998_240501.mov', proj: 'Bicycle Diaries', path: '/vol/nas01/bicycle-diaries/raw/old/A7S3_C998_240501.mov', last: '2026-04-12' },
    { name: 'INTERVIEW_GUEST_03.wav', proj: 'vulture.s reels', path: '/Volumes/USB_OLD/INTERVIEW_GUEST_03.wav', last: '2026-03-22' },
  ]

  // B4 conflict-merge
  const A = { name: 'A7S3_C001_240515.mov', size: '1.44 GB', dur: '00:02:47', proj: 'Bicycle Diaries', path: '/vol/nas01/bicycle-diaries/raw/A7S3_C001_240515.mov', ingested: '2026-05-16 02:14:33', rating: 'good', tags: ['cycling', 'road', 'interview', 'tibet', 'documentary'] }
  const B = { name: 'A7S3_C001_240515 copy.mov', size: '1.44 GB', dur: '00:02:47', proj: 'vulture.s reels', path: '/Volumes/EXT_C/dailies/A7S3_C001_240515 copy.mov', ingested: '2026-05-19 11:08:17', rating: 'rev', tags: ['cycling', 'interview', 'tibet', 'b-roll'] }
  const mergeFields = [
    ['FILE PATH', 'A', '/vol/nas01/bicycle-diaries/raw/A7S3_C001_240515.mov'],
    ['PROJECT', 'A', 'Bicycle Diaries'],
    ['TAGS', 'A∪B', 'cycling, road, interview, tibet, documentary, b-roll'],
    ['TRANSCRIPT', 'B (newer)', '"我們從上海一路騎到拉薩…" · 1247 tokens'],
  ]
</script>

<div class="stack">

  <!-- B1 · INGEST SETUP -->
  <Eyebrow style="padding-left:8px;">B1 · ingest setup</Eyebrow>
  <div class="artboard rel">
    <div class="backdrop"><div class="bdtop"></div><div class="bdbody"><div class="bdside"></div><div class="bdgrid">{#each bd as _}<div class="bdcell"></div>{/each}</div><div class="bdside right"></div></div></div>
    <div class="scrim s84"></div>
    <div class="modal" style="width:1080px;height:780px;grid-template-rows:64px 1fr 72px;">
      <div class="mhead">
        <div class="mtitle"><Eyebrow style="color:var(--ink-2);">Step 1 / 1 · Configure</Eyebrow><div class="ak-display mt26">Ingest 23 files · 18.4 GB</div></div>
        <Mono dim style="font-size:11px;">ESC · CANCEL</Mono>
      </div>
      <div class="setupbody">
        <div class="setupleft">
          <section><div class="fshead"><Eyebrow style="margin-bottom:4px;">SOURCE · FOLDER</Eyebrow><div class="ak-display fstitle">From /Volumes/RECORDER_A/DCIM/100MSDCF</div></div>
            <div class="frows">
              <div class="frow"><span class="flabel">Strategy</span><div class="seg">{#each srcStrategy as [id, l, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{l}</button>{/each}</div></div>
              <div class="frow"><span class="flabel">Destination</span><div class="dropdown"><Mono style="font-size:11.5px;color:var(--ink);">/vol/nas01/bicycle-diaries/raw/day-20/</Mono><Mono dim style="font-size:10px;">▾</Mono></div></div>
              <div class="frow"><span class="flabel">On conflict</span><div class="seg">{#each onConflict as [id, l, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{l}</button>{/each}</div></div>
            </div>
          </section>
          <section><div class="fshead"><Eyebrow style="margin-bottom:4px;">WHISPER · LOCAL</Eyebrow><div class="ak-display fstitle">Transcribe audio tracks</div></div>
            <div class="frows">
              <div class="frow"><span class="flabel">Model</span><div class="dropdown"><Mono style="font-size:11.5px;color:var(--ink);">whisper-large-v3 · 1.55 GB</Mono><Mono dim style="font-size:10px;">▾</Mono></div></div>
              <div class="frow"><span class="flabel">Languages</span><div class="langs">{#each setupLangs as [l, on]}<span class="lang" class:on>{l}</span>{/each}</div></div>
              <div class="frow"><span class="flabel">Skip if</span><Mono dim style="font-size:11.5px;">audio &lt; -42 dB · duration &lt; 2s</Mono></div>
            </div>
          </section>
          <section><div class="fshead"><Eyebrow style="margin-bottom:4px;">OLLAMA · LOCAL</Eyebrow><div class="ak-display fstitle">Vision tagging</div></div>
            <div class="frows">
              <div class="frow"><span class="flabel">Model</span><div class="dropdown"><Mono style="font-size:11.5px;color:var(--ink);">llava:13b-v1.6-q4_K_M · 7.4 GB</Mono><Mono dim style="font-size:10px;">▾</Mono></div></div>
              <div class="frow"><span class="flabel">Tag pool</span><div class="seg">{#each tagPool as [id, l, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{l}</button>{/each}</div></div>
              <div class="frow"><span class="flabel">Frames sampled</span><Mono dim style="font-size:11.5px;">1 frame / scene · max 12 per clip</Mono></div>
            </div>
          </section>
        </div>
        <div class="setupright">
          <div><Eyebrow style="margin-bottom:6px;">Manifest</Eyebrow><Mono dim style="font-size:11px;letter-spacing:0.04em;">23 files · 18.4 GB</Mono></div>
          <div class="maniflist">
            {#each manifest as [k, ext, n, sz]}
              <div class="manifrow"><Mono style="font-size:11.5px;color:var(--ink);">{k}</Mono><Mono dim style="font-size:10.5px;">{ext}</Mono><Mono style="font-size:11.5px;text-align:right;">{n}</Mono><Mono dim style="font-size:10.5px;text-align:right;">{sz}</Mono></div>
            {/each}
          </div>
          <div><Eyebrow style="margin-bottom:6px;">Estimated</Eyebrow>
            <div class="estgrid">{#each estimate as [k, v]}<Mono dim>{k}</Mono><Mono>{v}</Mono>{/each}<Mono dim>TOTAL</Mono><Mono style="font-weight:600;">≈ 6:30</Mono></div>
          </div>
          <div class="grow"></div>
          <div class="notice"><Mono dim style="font-size:9.5px;letter-spacing:0.1em;display:block;margin-bottom:4px;">◇ NOTICE</Mono><Mono dim style="font-size:10.5px;line-height:1.5;">Files are processed locally. Nothing leaves this machine. GPU draw will spike during transcribe + tag stages.</Mono></div>
        </div>
      </div>
      <div class="mfoot">
        <Mono dim style="font-size:11px;letter-spacing:0.05em;">Destination · Bicycle Diaries  ·  est. 6:30  ·  GPU required</Mono>
        <div class="fbtns"><button class="ak-btn">Cancel</button><button class="ak-btn">Save preset…</button><button class="ak-btn ak-btn--primary wide">Start ingest →</button></div>
      </div>
    </div>
  </div>

  <!-- B2 · QUERY BUILDER -->
  <Eyebrow style="padding-left:8px;">B2 · query builder</Eyebrow>
  <div class="artboard rows52">
    <div class="topbar"><ArkivLogo size={16} /><Mono dim style="font-size:10px;">v0.9.2</Mono><div class="grow"></div><button class="ak-btn">⌘K · SIMPLE SEARCH</button><button class="ak-btn">Save as Smart Pool…</button></div>
    <div class="qbsplit">
      <div class="qbleft">
        <div class="qbhead"><Eyebrow style="margin-bottom:8px;">Query builder · compound</Eyebrow><div class="ak-display qbtitle">Find what<br />matches all of:</div></div>
        <div class="qbclauses">
          {#each clauses as [field, op, value]}
            <div class="clause"><span class="cfield">{field}</span><span class="cop">{op}</span><span class="cvalue">{value}</span><button class="cx">✕</button></div>
          {/each}
          <button class="addclause">+ Add clause</button>
        </div>
        <div class="qbbool">
          <Eyebrow style="margin-bottom:8px;">Boolean</Eyebrow>
          <div class="seg">{#each boolOpts as [id, l, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{l}</button>{/each}</div>
          <Mono dim style="font-size:10px;margin-top:10px;line-height:1.5;display:block;">transcript:"氧氣" AND camera:"α7S III" AND tag IN ["cycling","tibet"]<br />AND rating &gt;= REV AND iso BETWEEN 400 AND 1600<br />AND created BETWEEN '2026-05-15' AND '2026-05-19'<br />AND duration &gt; 30</Mono>
        </div>
      </div>
      <div class="qbright">
        <div class="qbprevhead">
          <div><Eyebrow>Preview · live</Eyebrow><Mono style="font-size:16px;font-weight:600;color:var(--ink);display:block;margin-top:6px;">14 matches <Mono dim style="font-size:11px;font-weight:400;margin-left:8px;">of 247 · 0.038s</Mono></Mono></div>
          <div class="fbtns"><button class="ak-btn">All projects · 4</button><button class="ak-btn">Open as pool</button></div>
        </div>
        <div class="qbresults">
          {#each qbPreview as m (m.id)}
            <div class="qbrow">
              <div class="qbthumb"><Thumb seed={m.id} kind={m.kind} {theme} /></div>
              <div class="qbcontent"><Mono style="font-size:11.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{m.name}</Mono><div class="qbtags"><Mono dim style="font-size:10px;">{m.cam.replace('Sony α', 'α')}</Mono><Mono dim style="font-size:10px;">{m.tags.slice(0, 2).join(' · ')}</Mono></div></div>
              <Rating value={m.rating} />
              <Mono dim style="font-size:11px;text-align:right;">{m.dur}</Mono>
            </div>
          {/each}
        </div>
      </div>
    </div>
  </div>

  <!-- B3 · PROJECT REGISTRY -->
  <Eyebrow style="padding-left:8px;">B3 · project registry</Eyebrow>
  <div class="artboard rows52">
    <div class="topbar"><ArkivLogo size={16} /><Mono dim style="font-size:10px;">v0.9.2</Mono><div class="grow"></div><Mono dim style="font-size:11px;">~/.arkiv-projects.json · 5 registered</Mono><button class="ak-btn">+ New project</button><button class="ak-btn">Import .arkiv</button></div>
    <div class="regwrap">
      <Eyebrow style="margin-bottom:8px;">Project registry</Eyebrow>
      <div class="reghero"><div class="ak-display reghbig">5 projects · 938 files · 14.5 TB</div><Mono dim style="font-size:11px;letter-spacing:0.05em;">3 mounted · 1 unmounted · 1 archived</Mono></div>
      <div class="reghead" style="grid-template-columns:{regCols};"><span></span><Mono dim style="font-size:10px;letter-spacing:0.12em;">PROJECT</Mono><Mono dim style="font-size:10px;letter-spacing:0.12em;text-align:right;">FILES</Mono><Mono dim style="font-size:10px;letter-spacing:0.12em;text-align:right;">SIZE</Mono><Mono dim style="font-size:10px;letter-spacing:0.12em;">PATH · MOUNT</Mono><Mono dim style="font-size:10px;letter-spacing:0.12em;">LAST INGEST</Mono><Mono dim style="font-size:10px;letter-spacing:0.12em;text-align:right;">ACTIONS</Mono></div>
      {#each PROJECTS_FULL as p (p.name)}
        <div class="regrow" class:archived={p.health === 'archived'} style="grid-template-columns:{regCols};">
          <div class="hdot {p.health}"></div>
          <div><div class="ak-display regname" class:strike={p.health === 'archived'}>{p.name}</div>{#if p.active}<Mono dim style="font-size:9.5px;letter-spacing:0.1em;margin-top:4px;color:var(--ink);">● ACTIVE</Mono>{/if}</div>
          <Mono style="font-size:13px;text-align:right;">{p.count}</Mono>
          <Mono dim style="font-size:12px;text-align:right;">{p.size}</Mono>
          <div class="regpath"><Mono dim style="font-size:10.5px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{p.path}</Mono><Mono dim style="font-size:9.5px;letter-spacing:0.08em;margin-top:2px;display:block;">{mountText[p.mount]}</Mono></div>
          <Mono dim style="font-size:11px;">{p.last}</Mono>
          <div class="regactions"><button class="ak-btn sm">{mountAction[p.mount]}</button><button class="ak-btn sm">···</button></div>
        </div>
      {/each}
      <div class="orphans">
        <div class="orphanhead"><div class="orphanhl"><Eyebrow>Orphans</Eyebrow><Mono style="font-size:14px;font-weight:600;">2 files</Mono><Mono dim style="font-size:11px;">indexed but source path is gone</Mono></div><button class="ak-btn">Resolve all…</button></div>
        {#each orphans as o (o.name)}
          <div class="orphanrow"><div class="odot"></div><Mono style="font-size:12px;color:var(--ink);">{o.name}</Mono><div><Mono dim style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;">{o.path}</Mono><Mono dim style="font-size:10px;margin-top:2px;display:block;">was in: {o.proj} · last seen {o.last}</Mono></div><div class="regactions"><button class="ak-btn sm">Locate</button><button class="ak-btn sm">Forget</button></div></div>
        {/each}
      </div>
    </div>
  </div>

  <!-- B4 · CONFLICT MERGE -->
  <Eyebrow style="padding-left:8px;">B4 · conflict merge</Eyebrow>
  <div class="artboard rel">
    <div class="scrim s82"></div>
    <div class="modal" style="width:1240px;height:760px;grid-template-rows:72px 1fr 72px;">
      <div class="mhead">
        <div><Eyebrow style="margin-bottom:4px;">Conflict · identical SHA-256</Eyebrow><div class="ak-display mt22">Two records, one file. Choose how to merge.</div></div>
        <Mono dim style="font-size:10.5px;letter-spacing:0.04em;">7f3e8a92c1b4d5f6e2a8c9d1b3e5f7a9c2b4d6e8f1a3c5d7e9b1f3a5c7d9e1f3</Mono>
      </div>
      <div class="cmpbody">
        {#each [{ label: 'A · Bicycle Diaries', data: A, pick: 'A', seed: 1, active: false }, { label: 'B · vulture.s reels', data: B, pick: 'B', seed: 7, active: true }] as col}
          <div class="cmpcol" class:active={col.active}>
            <div class="cmptop"><Eyebrow style={col.active ? 'color:var(--ink);' : ''}>{col.label}</Eyebrow><span class="pick" class:active={col.active}>{col.pick}</span></div>
            <div class="cmpthumb"><Thumb seed={col.seed} kind="video" {theme} /></div>
            <Mono style="font-size:11.5px;word-break:break-all;color:var(--ink);">{col.data.name}</Mono>
            <Mono dim style="font-size:10px;line-height:1.5;">{col.data.path}</Mono>
            <div class="cmpmeta"><Mono dim>PROJECT</Mono><Mono>{col.data.proj}</Mono><Mono dim>SIZE</Mono><Mono>{col.data.size}</Mono><Mono dim>DURATION</Mono><Mono>{col.data.dur}</Mono><Mono dim>INGESTED</Mono><Mono>{col.data.ingested}</Mono></div>
            <div><Mono dim style="font-size:9.5px;letter-spacing:0.1em;display:block;margin-bottom:6px;">RATING</Mono><Rating value={col.data.rating} /></div>
            <div><Mono dim style="font-size:9.5px;letter-spacing:0.1em;display:block;margin-bottom:6px;">TAGS · {col.data.tags.length}</Mono><div class="cmptags">{#each col.data.tags as t}<span class="cmptag">{t}</span>{/each}</div></div>
          </div>
        {/each}
        <div class="mergecol">
          <div class="cmptop"><Eyebrow>Merged result · preview</Eyebrow><span class="pick active">M</span></div>
          {#each mergeFields as [label, source, value]}
            <div class="fieldchoice"><Mono dim style="font-size:9.5px;letter-spacing:0.1em;">{label}</Mono><div class="fcvalue"><Mono style="font-size:11px;">{value}</Mono></div><span class="fcsource">{source}</span></div>
          {/each}
          <div class="fieldchoice"><Mono dim style="font-size:9.5px;letter-spacing:0.1em;">RATING</Mono><div class="fcvalue"><Rating value="rev" /></div><span class="fcsource">B</span></div>
          <div class="grow"></div>
          <div class="notice"><Mono dim style="font-size:9.5px;letter-spacing:0.1em;display:block;margin-bottom:4px;">◇ AFTER MERGE</Mono><Mono dim style="font-size:10.5px;line-height:1.5;">B's file deleted from /Volumes/EXT_C.<br />B's record reassigned to A. Index updated.<br />Reversible for 30 days · trash queue.</Mono></div>
        </div>
      </div>
      <div class="mfoot">
        <Mono dim style="font-size:11px;letter-spacing:0.05em;">Keep file at A · Adopt rating from B · Union tags · Use B transcript (newer)</Mono>
        <div class="fbtns"><button class="ak-btn">Skip · keep both</button><button class="ak-btn">Keep A · delete B</button><button class="ak-btn">Keep B · delete A</button><button class="ak-btn ak-btn--primary wide">Merge →</button></div>
      </div>
    </div>
  </div>

</div>

<style>
  .stack { display: flex; flex-direction: column; gap: 28px; padding: 24px 0; }
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); overflow: hidden; margin: 0 auto; }
  .artboard.rel { position: relative; }
  .artboard.rows52 { display: grid; grid-template-rows: 52px 1fr; }
  .grow { flex: 1; }
  .rel { position: relative; }

  .backdrop { position: absolute; inset: 0; display: grid; grid-template-rows: 52px 1fr; }
  .bdtop { border-bottom: 1px solid var(--rule); }
  .bdbody { display: grid; grid-template-columns: 220px 1fr 340px; }
  .bdside { border-right: 1px solid var(--rule); }
  .bdside.right { border-right: none; border-left: 1px solid var(--rule); }
  .bdgrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; padding: 22px; background: var(--rule); }
  .bdcell { aspect-ratio: 16 / 9; background: var(--surface-2); }
  .scrim { position: absolute; inset: 0; }
  .scrim.s84 { background: rgba(10, 10, 12, 0.84); }
  .scrim.s82 { background: rgba(10, 10, 12, 0.82); }

  .modal { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); background: var(--bg); box-shadow: inset 0 0 0 1px var(--invert); display: grid; }
  .mhead { display: flex; align-items: center; justify-content: space-between; padding: 0 28px; border-bottom: 1px solid var(--invert); }
  .mtitle { display: flex; align-items: baseline; gap: 14px; }
  .mt26 { font-size: 26px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .mt22 { font-size: 22px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .mfoot { display: flex; align-items: center; justify-content: space-between; padding: 0 28px; border-top: 1px solid var(--rule); }
  .fbtns { display: flex; gap: 8px; }
  .wide { padding: 8px 20px; }

  /* form bits */
  .fshead { margin-bottom: 12px; }
  .fstitle { font-size: 18px; letter-spacing: -0.02em; line-height: 1.1; color: var(--ink); }
  .frows { display: flex; flex-direction: column; gap: 10px; }
  .frow { display: grid; grid-template-columns: 140px 1fr; align-items: center; gap: 16px; }
  .flabel { font-family: var(--ak-mono); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--quiet); }
  .seg { display: flex; border: 1px solid var(--rule); width: fit-content; }
  .segsep { width: 1px; background: var(--rule); }
  .segbtn { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 5px 12px; background: transparent; color: var(--ink-2); border: none; cursor: pointer; line-height: 1; font-weight: 400; }
  .segbtn.on { background: var(--invert); color: var(--invert-ink); font-weight: 700; }
  .dropdown { display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--rule); padding: 6px 10px; width: 100%; max-width: 360px; cursor: pointer; }
  .langs { display: flex; gap: 4px; flex-wrap: wrap; }
  .lang { font-family: var(--ak-mono); font-size: 10.5px; padding: 4px 8px; line-height: 1; background: transparent; color: var(--ink-2); border: 1px solid var(--rule); cursor: pointer; }
  .lang.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
  .notice { border: 1px dashed var(--rule-hi); padding: 12px 14px; }

  /* B1 setup */
  .setupbody { display: grid; grid-template-columns: 1fr 360px; min-height: 0; overflow: hidden; }
  .setupleft { padding: 24px 32px; overflow: hidden; display: flex; flex-direction: column; gap: 22px; }
  .setupright { border-left: 1px solid var(--rule); padding: 24px; overflow: hidden; display: flex; flex-direction: column; gap: 18px; }
  .maniflist { display: flex; flex-direction: column; gap: 1px; background: var(--rule); }
  .manifrow { background: var(--bg); padding: 8px 10px; display: grid; grid-template-columns: 60px 50px 1fr 1fr; gap: 8px; align-items: baseline; }
  .estgrid { display: grid; grid-template-columns: 80px 1fr; row-gap: 5px; column-gap: 12px; font-size: 11.5px; }

  /* B2 query builder */
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .qbsplit { display: grid; grid-template-columns: 460px 1fr; min-height: 0; overflow: hidden; }
  .qbleft { border-right: 1px solid var(--rule); display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .qbhead { padding: 28px 28px 18px; border-bottom: 1px solid var(--rule); }
  .qbtitle { font-size: 32px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .qbclauses { padding: 20px 28px; flex: 1; overflow: hidden; display: flex; flex-direction: column; gap: 12px; }
  .clause { display: grid; grid-template-columns: 76px 84px 1fr 20px; gap: 6px; align-items: center; }
  .cfield { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 4px 6px; border: 1px solid var(--rule); color: var(--ink-2); text-align: center; }
  .cop { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 4px 6px; color: var(--quiet); text-align: center; }
  .cvalue { font-family: var(--ak-mono); font-size: 11.5px; padding: 4px 8px; border: 1px solid var(--rule-hi); color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cx { background: transparent; border: none; color: var(--quiet); cursor: pointer; font-family: var(--ak-mono); font-size: 13px; }
  .addclause { padding: 10px 0; border: 1px dashed var(--rule-hi); font-family: var(--ak-mono); font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-2); background: transparent; cursor: pointer; margin-top: 4px; }
  .qbbool { padding: 14px 28px; border-top: 1px solid var(--rule); }
  .qbright { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .qbprevhead { padding: 18px 32px; border-bottom: 1px solid var(--rule); display: flex; align-items: baseline; justify-content: space-between; }
  .qbresults { flex: 1; overflow: hidden; padding: 14px 32px; }
  .qbrow { display: grid; grid-template-columns: 90px 1fr 70px 60px; gap: 14px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--rule); }
  .qbthumb { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }
  .qbcontent { min-width: 0; }
  .qbtags { display: flex; gap: 12px; margin-top: 3px; }

  /* B3 registry */
  .regwrap { padding: 40px 60px; overflow: hidden; }
  .reghero { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 28px; }
  .reghbig { font-size: 44px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .reghead { display: grid; gap: 16px; padding: 0 0 10px; border-bottom: 1px solid var(--invert); }
  .regrow { display: grid; gap: 16px; padding: 16px 0; border-bottom: 1px solid var(--rule); align-items: center; }
  .regrow.archived { opacity: 0.55; }
  .hdot { width: 8px; height: 8px; border: 1px solid var(--rule-hi); }
  .hdot.ok { background: var(--ink); border: none; }
  .hdot.warn { border: 1px dashed var(--ink-2); }
  .regname { font-size: 18px; letter-spacing: -0.02em; line-height: 1; color: var(--ink); }
  .regname.strike { text-decoration: line-through; }
  .regpath { min-width: 0; }
  .regactions { display: flex; gap: 4px; justify-content: flex-end; }
  .sm { padding: 5px 9px; }
  .orphans { margin-top: 36px; }
  .orphanhead { display: flex; align-items: baseline; justify-content: space-between; padding-bottom: 10px; border-bottom: 1px solid var(--invert); margin-bottom: 14px; }
  .orphanhl { display: flex; align-items: baseline; gap: 14px; }
  .orphanrow { display: grid; grid-template-columns: 24px 1fr 1fr 120px; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--rule); align-items: center; }
  .odot { width: 8px; height: 8px; border: 1px dashed var(--ink-2); }

  /* B4 conflict */
  .cmpbody { display: grid; grid-template-columns: 1fr 1fr 1fr; min-height: 0; overflow: hidden; }
  .cmpcol { border-right: 1px solid var(--rule); padding: 20px 22px; display: flex; flex-direction: column; gap: 14px; overflow: hidden; }
  .cmpcol.active { background: var(--surface); box-shadow: inset 2px 0 0 var(--invert); }
  .cmptop { display: flex; justify-content: space-between; align-items: baseline; }
  .pick { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.08em; padding: 2px 6px; background: transparent; color: var(--ink-2); border: 1px solid var(--rule); }
  .pick.active { background: var(--invert); color: var(--invert-ink); border: none; }
  .cmpthumb { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }
  .cmpmeta { display: grid; grid-template-columns: 76px 1fr; row-gap: 5px; column-gap: 12px; font-size: 11px; margin-top: 4px; }
  .cmptags { display: flex; flex-wrap: wrap; gap: 4px; }
  .cmptag { font-family: var(--ak-mono); font-size: 10px; padding: 2px 5px; line-height: 1; border: 1px solid var(--rule); color: var(--ink-2); }
  .mergecol { padding: 20px 22px; display: flex; flex-direction: column; gap: 14px; overflow: hidden; }
  .fieldchoice { display: grid; grid-template-columns: 90px 1fr 50px; gap: 10px; align-items: baseline; }
  .fcvalue { font-size: 11.5px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fcsource { font-family: var(--ak-mono); font-size: 9px; letter-spacing: 0.08em; padding: 2px 5px; text-align: center; background: var(--invert); color: var(--invert-ink); }
</style>
