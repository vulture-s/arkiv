<!-- Seg 3 — Screen 1 state variants (01a–01f). Reuses chrome from lib;
     only the CENTER (and sometimes inspector/footer) changes per state.
     All 6 artboards stacked vertically for audit. -->
<script>
  import MainShell from '../lib/MainShell.svelte'
  import Thumb from '../lib/Thumb.svelte'
  import Rating from '../lib/Rating.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import FilterRow from '../lib/FilterRow.svelte'
  import ViewToggle from '../lib/ViewToggle.svelte'
  import Sweep from '../lib/Sweep.svelte'
  import { MEDIA } from '../lib/mockData.js'

  const theme = 'dark'
  const listCols = '110px 1fr 70px 80px 76px 84px 70px 80px'
  const listHeaders = ['THUMB', 'FILENAME', 'RATING', 'DURATION', 'SIZE', 'CAMERA', 'LENS', 'CREATED']
  const camAbbr = (c) => c.replace('Sony α', 'α').replace('Panasonic ', '')

  const navItems = [
    ['↑ ↓ ← →', 'Move selection'], ['Space', 'Preview play / pause'], ['Enter', 'Open in Inspector'],
    ['⌘K', 'Search'], ['⌘P', 'Pool / project switcher'], ['⌘1…4', 'Jump to project'],
    ['G G', 'Top of grid'], ['G E', 'End of grid'],
  ]
  const actItems = [
    ['1', 'Rate Good'], ['2', 'Rate Review'], ['3', 'Rate N·G'], ['0', 'Clear rating'],
    ['T', 'Add tag…'], ['I  O', 'Set In / Out'], ['⌘E', 'Export EDL'], ['⌘⇧E', 'Export FCPXML'],
  ]
  const probe = [
    ['PROBE', 'codec · resolution · bitrate · color · duration'],
    ['THUMB', 'scene-detect cuts · proxy 540p · waveform'],
    ['TRANS', 'whisper-large-v3 · zh/en/ja/auto'],
    ['TAG', 'llava 13b · max 8 tags per clip'],
    ['HASH', 'sha-256 dedupe · arkiv://...'],
  ]
  const ratingDist = [
    { label: 'GOOD', count: 158, fill: 'var(--invert)' },
    { label: 'REV', count: 34, fill: 'var(--ink-2)' },
    { label: 'N·G', count: 19, fill: 'var(--quiet)' },
    { label: '—', count: 36, fill: 'var(--surface-3)' },
  ]
  const distTotal = ratingDist.reduce((a, s) => a + s.count, 0)
  const ingest7 = [8, 12, 18, 5, 9, 0, 0]
  const ingest7max = Math.max(...ingest7, 1)
  const ingestLabels = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
  const topTags = [['cycling', 88, 0.55], ['b-roll', 71, 0.46], ['product', 62, 0.4], ['portrait', 41, 0.27], ['documentary', 28, 0.18]]
  const skelMeta = [0, 1, 2, 3]
</script>

<div class="stack">

  <!-- 01a · LIST VIEW -->
  <Eyebrow style="padding-left:8px;">01a · list view</Eyebrow>
  <MainShell {theme}>
    <main slot="center" class="col">
      <div class="toolrow">
        <div class="grow">
          <div class="ak-display title28">Bicycle Diaries</div>
          <Mono dim style="font-size:11px;margin-top:5px;letter-spacing:0.04em;">247 items · 11h 42m · sorted by created ▾</Mono>
        </div>
        <FilterRow activeFilter="all" activeRating={null} />
        <ViewToggle view="list" />
      </div>
      <div class="lrow lhead" style="grid-template-columns:{listCols};">
        {#each listHeaders as h}<Mono dim style="font-size:9.5px;letter-spacing:0.12em;">{h}</Mono>{/each}
      </div>
      <div class="grow ovh">
        {#each MEDIA.slice(0, 14) as m, i (m.id)}
          <div class="lrow" class:first={i === 0} class:ng={m.rating === 'ng'} style="grid-template-columns:{listCols};">
            <div class="lthumb"><Thumb seed={m.id} kind={m.kind} {theme} /></div>
            <Mono style="font-size:11.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;{m.rating === 'ng' ? 'text-decoration:line-through;' : ''}">{m.name}</Mono>
            <Rating value={m.rating} />
            <Mono dim style="font-size:11px;">{m.dur}</Mono>
            <Mono dim style="font-size:11px;">{m.size}</Mono>
            <Mono dim style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{camAbbr(m.cam)}</Mono>
            <Mono dim style="font-size:11px;">{m.fl}</Mono>
            <Mono dim style="font-size:11px;">05-{15 + (m.id % 7)}</Mono>
          </div>
        {/each}
      </div>
    </main>
  </MainShell>

  <!-- 01b · DROP ZONE -->
  <Eyebrow style="padding-left:8px;">01b · drop-zone</Eyebrow>
  <MainShell {theme}>
    <main slot="center" class="rel ovh">
      <div class="grid4 dim" style="height:100%;">
        {#each MEDIA.slice(0, 12) as m (m.id)}
          <div class="cellbg"><div class="thumb169"><Thumb seed={m.id} kind={m.kind} {theme} /></div></div>
        {/each}
      </div>
      <div class="dropzone">
        <Eyebrow style="color:var(--cyan);">DROP TO INGEST</Eyebrow>
        <div class="ak-display dzbig">23 files<br /><span class="quiet">·  18.4 GB</span></div>
        <Mono dim style="font-size:12px;letter-spacing:0.05em;">16 video · 5 audio · 2 unsupported · Bicycle Diaries</Mono>
        <div class="dzfoot">
          <Mono dim style="font-size:10.5px;">HOLD ⌥ TO SKIP DUPLICATES</Mono>
          <Mono dim style="font-size:10.5px;">RELEASE TO START · Esc CANCEL</Mono>
        </div>
      </div>
    </main>
  </MainShell>

  <!-- 01c · SKELETON -->
  <Eyebrow style="padding-left:8px;">01c · skeleton</Eyebrow>
  <MainShell {theme}>
    <main slot="center" class="col">
      <div class="toolrow">
        <div class="grow">
          <div class="skel" style="width:200px;height:28px;background:var(--surface-3);"><Sweep /></div>
          <div class="skel" style="width:280px;height:11px;background:var(--surface-2);margin-top:8px;"><Sweep delay={0.3} /></div>
        </div>
        <Mono style="font-size:11px;color:var(--cyan);letter-spacing:0.08em;">● LOADING · 0.4s</Mono>
      </div>
      <div class="grow ovh rel">
        <div class="grid4">
          {#each Array(12) as _, i}
            <div class="cellbg">
              <div class="thumb169 skel"><Sweep delay={i * 0.08} /></div>
              <div class="skelmeta">
                <div class="skel" style="width:85%;height:11px;background:var(--surface-2);"><Sweep delay={i * 0.08 + 0.2} /></div>
                <div class="skelrow"><div style="width:30px;height:10px;background:var(--surface-2);"></div><div style="width:46px;height:10px;background:var(--surface-2);"></div></div>
              </div>
            </div>
          {/each}
        </div>
      </div>
    </main>
    <aside slot="inspector" class="skelinsp">
      <div class="skel" style="width:140px;height:11px;background:var(--surface-3);"><Sweep /></div>
      <div style="width:100%;height:14px;background:var(--surface-2);"></div>
      <div class="thumb169 skel"><Sweep delay={0.5} /></div>
      <div class="skelmetagrid">
        {#each skelMeta as i}
          <div class="skelmg"><div style="height:9px;background:var(--surface-3);"></div><div style="height:9px;background:var(--surface-2);width:{50 + (i * 13) % 40}%;"></div></div>
        {/each}
      </div>
      <div class="skel" style="height:56px;background:var(--surface-2);"><Sweep delay={0.7} /></div>
    </aside>
  </MainShell>

  <!-- 01d · EMPTY PROJECT -->
  <Eyebrow style="padding-left:8px;">01d · empty project</Eyebrow>
  <MainShell {theme}>
    <main slot="center" class="col">
      <div class="toolrow">
        <div class="grow">
          <div class="ak-display title28">KOL_2026Q1</div>
          <Mono dim style="font-size:11px;margin-top:5px;letter-spacing:0.04em;">0 items · created 2026-05-26 · NAS not mounted</Mono>
        </div>
        <button class="ak-btn">Configure NAS</button>
      </div>
      <div class="emptywrap">
        <div class="emptyhero">
          <Eyebrow>EMPTY PROJECT</Eyebrow>
          <div class="ak-display emptybig">Nothing<br />here yet.</div>
          <Mono dim style="font-size:12px;line-height:1.6;max-width:400px;margin-top:4px;">Drop a folder anywhere in this window to start ingesting, or point arkiv at a watched directory. Local-first. No cloud.</Mono>
          <div class="emptybtns">
            <button class="ak-btn ak-btn--primary">Choose folder…</button>
            <button class="ak-btn">Watch directory…</button>
            <button class="ak-btn">Import from .arkiv</button>
          </div>
          <div class="supported">
            <Mono dim style="font-size:10.5px;letter-spacing:0.08em;">SUPPORTED</Mono>
            <Mono dim style="font-size:10.5px;">.mov · .mp4 · .mxf · .braw · .r3d · .wav · .flac</Mono>
          </div>
        </div>
      </div>
    </main>
    <aside slot="inspector" class="emptyinsp">
      <Eyebrow>Inspector · no selection</Eyebrow>
      <div class="ak-display emptyinsptitle">Pick a clip<br />to inspect.</div>
      <Mono dim style="font-size:11px;line-height:1.6;margin-top:8px;">Once ingestion finishes, files appear in the grid and metadata shows here. Every file gets:</Mono>
      <div class="probe">
        {#each probe as [k, v]}
          <div class="probrow"><Mono dim style="font-size:10px;letter-spacing:0.1em;">{k}</Mono><Mono dim style="font-size:10.5px;">{v}</Mono></div>
        {/each}
      </div>
      <div class="grow"></div>
      <div class="emptyfoot"><Mono dim style="font-size:10px;letter-spacing:0.08em;">Local-first · MIT · arkiv v0.9.2</Mono></div>
    </aside>
  </MainShell>

  <!-- 01e · SHORTCUTS -->
  <Eyebrow style="padding-left:8px;">01e · shortcuts</Eyebrow>
  <MainShell {theme}>
    <main slot="center" class="rel ovh">
      <div class="grid4 dim" style="height:100%;">
        {#each MEDIA.slice(0, 12) as m (m.id)}
          <div class="cellbg"><div class="thumb169"><Thumb seed={m.id} kind={m.kind} {theme} /></div></div>
        {/each}
      </div>
      <div class="scrim"></div>
      <div class="sheet">
        <div class="sheethead">
          <div>
            <Eyebrow style="margin-bottom:4px;">Keyboard</Eyebrow>
            <div class="ak-display sheettitle">Shortcuts</div>
          </div>
          <Mono dim style="font-size:11px;">? · TOGGLE  ·  Esc · CLOSE</Mono>
        </div>
        <div class="sheetbody">
          <div class="scgroup">
            <Eyebrow style="margin-bottom:14px;">Navigate</Eyebrow>
            <div class="sclist">
              {#each navItems as [k, label]}<div class="scrow"><span class="sckey">{k}</span><span class="sclabel">{label}</span></div>{/each}
            </div>
          </div>
          <div class="scgroup right">
            <Eyebrow style="margin-bottom:14px;">Act</Eyebrow>
            <div class="sclist">
              {#each actItems as [k, label]}<div class="scrow"><span class="sckey">{k}</span><span class="sclabel">{label}</span></div>{/each}
            </div>
          </div>
        </div>
        <div class="sheetfoot">
          <Mono dim style="font-size:10.5px;letter-spacing:0.05em;">Reconfigure in Settings · Advanced · Keyboard</Mono>
          <Mono dim style="font-size:10.5px;">32 shortcuts · 8 hidden</Mono>
        </div>
      </div>
    </main>
  </MainShell>

  <!-- 01f · ANALYTICS FOOTER -->
  <Eyebrow style="padding-left:8px;">01f · analytics footer</Eyebrow>
  <MainShell {theme} rows="52px 1fr 220px">
    <main slot="center" class="col">
      <div class="toolrow">
        <div class="grow">
          <div class="ak-display title28">Bicycle Diaries</div>
          <Mono dim style="font-size:11px;margin-top:5px;letter-spacing:0.04em;">247 items · 11h 42m · last ingest 23:08</Mono>
        </div>
        <FilterRow activeFilter="all" activeRating={null} />
        <ViewToggle view="grid" />
      </div>
      <div class="grow grid4 ovh">
        {#each MEDIA.slice(0, 8) as m (m.id)}
          <div class="cellbg">
            <div class="thumb169"><Thumb seed={m.id} kind={m.kind} {theme} /></div>
            <div class="acard">
              <Mono style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;">{m.name}</Mono>
              <div class="acardrow"><Rating value={m.rating} /><Mono dim style="font-size:10px;">{m.size}</Mono></div>
            </div>
          </div>
        {/each}
      </div>
    </main>
    <footer slot="footer" class="analytics">
      <div class="anleft">
        <div class="anrow"><Eyebrow>Analytics</Eyebrow><Mono dim style="font-size:10px;">▾</Mono></div>
        <div class="ak-display antitle">Bicycle Diaries · last 7 days</div>
        <Mono dim style="font-size:10.5px;line-height:1.5;margin-top:2px;">+52 ingested · 38 rated GOOD · 14 N·G ·<br />avg transcribe 0.41s/min · 0 errors</Mono>
      </div>
      <div class="anmid">
        <div>
          <div class="anrow mb10"><Eyebrow>Rating distribution</Eyebrow><Mono dim style="font-size:10px;">247 total</Mono></div>
          <div class="stackbar">{#each ratingDist as s}<div style="flex:{s.count};background:{s.fill};"></div>{/each}</div>
          <div class="stacklegend">
            {#each ratingDist as s}
              <div class="stackseg"><Mono dim style="font-size:9.5px;letter-spacing:0.08em;">{s.label}</Mono><Mono style="font-size:13px;font-weight:600;">{s.count}</Mono><Mono dim style="font-size:9.5px;">{Math.round((s.count / distTotal) * 100)}%</Mono></div>
            {/each}
          </div>
        </div>
        <div>
          <div class="anrow mb10"><Eyebrow>Ingest · 7 days</Eyebrow><Mono dim style="font-size:10px;">peak Tue · 18</Mono></div>
          <div class="barchart">
            {#each ingest7 as v}
              <div class="barcol"><div class="barcolinner"><div class="bar" class:zero={v === 0} style="height:{(v / ingest7max) * 100}%;"></div></div></div>
            {/each}
          </div>
          <div class="barlabels">{#each ingestLabels as l}<Mono dim style="flex:1;text-align:center;font-size:9.5px;letter-spacing:0.1em;">{l}</Mono>{/each}</div>
        </div>
      </div>
      <div class="anright">
        <div class="anrow mb10"><Eyebrow>Top tags · auto</Eyebrow><Mono dim style="font-size:10px;">view all →</Mono></div>
        <div class="taglist">
          {#each topTags as [t, n, frac]}
            <div class="tagrow"><Mono style="font-size:11px;">{t}</Mono><div class="tagbar"><div class="tagbarfill" style="width:{frac * 100}%;"></div></div><Mono dim style="font-size:10.5px;text-align:right;">{n}</Mono></div>
          {/each}
        </div>
      </div>
    </footer>
  </MainShell>

</div>

<style>
  .stack { display: flex; flex-direction: column; gap: 28px; padding: 24px 0; }
  .col { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .grow { flex: 1; min-width: 0; }
  .ovh { overflow: hidden; }
  .rel { position: relative; }
  .toolrow { display: flex; align-items: center; gap: 14px; padding: 14px 22px; border-bottom: 1px solid var(--rule); }
  .title28 { font-size: 28px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  .grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; padding: 22px; background: var(--rule); }
  .grid4.dim { opacity: 0.18; }
  .cellbg { background: var(--bg); }
  .thumb169 { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }

  /* list */
  .lrow { display: grid; gap: 14px; align-items: center; padding: 7px 22px; border-bottom: 1px solid var(--rule); cursor: pointer; }
  .lrow.lhead { align-items: baseline; padding: 10px 22px 8px; }
  .lrow.first { background: var(--surface); box-shadow: inset 2px 0 0 var(--invert); }
  .lrow.ng { opacity: 0.55; }
  .lthumb { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }

  /* dropzone */
  .dropzone { position: absolute; inset: 22px; border: 2px dashed var(--cyan); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px; background: rgba(10, 10, 12, 0.65); }
  .dzbig { font-size: 64px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); text-align: center; }
  .dzbig .quiet { color: var(--quiet); }
  .dzfoot { display: flex; align-items: baseline; gap: 24px; margin-top: 14px; padding-top: 18px; border-top: 1px solid var(--cyan); }

  /* skeleton */
  .skel { position: relative; overflow: hidden; }
  .skelmeta { padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 6px; }
  .skelrow { display: flex; justify-content: space-between; }
  .skelinsp { border-left: 1px solid var(--rule); padding: 18px; display: flex; flex-direction: column; gap: 16px; }
  .skelmetagrid { display: flex; flex-direction: column; gap: 8px; }
  .skelmg { display: grid; grid-template-columns: 60px 1fr; gap: 12px; }

  /* empty */
  .emptywrap { flex: 1; padding: 22px; display: flex; }
  .emptyhero { flex: 1; border: 1px dashed var(--rule-hi); display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 60px; gap: 16px; text-align: center; }
  .emptybig { font-size: 72px; letter-spacing: -0.04em; line-height: 0.95; color: var(--ink); }
  .emptybtns { display: flex; gap: 8px; margin-top: 14px; }
  .supported { display: flex; align-items: center; gap: 18px; margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--rule); width: 60%; }
  .emptyinsp { border-left: 1px solid var(--rule); padding: 24px 20px; display: flex; flex-direction: column; gap: 18px; }
  .emptyinsptitle { font-size: 28px; letter-spacing: -0.03em; line-height: 1; color: var(--quiet); }
  .probe { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
  .probrow { display: grid; grid-template-columns: 52px 1fr; gap: 10px; }
  .emptyfoot { padding-top: 14px; border-top: 1px solid var(--rule); }

  /* shortcuts */
  .scrim { position: absolute; inset: 0; background: rgba(10, 10, 12, 0.82); }
  .sheet { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 720px; background: var(--bg); box-shadow: inset 0 0 0 1px var(--invert); }
  .sheethead { padding: 18px 24px; border-bottom: 1px solid var(--invert); display: flex; align-items: baseline; justify-content: space-between; }
  .sheettitle { font-size: 24px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .sheetbody { display: grid; grid-template-columns: 1fr 1fr; padding: 24px; }
  .scgroup { padding-right: 24px; }
  .scgroup.right { padding-right: 0; padding-left: 24px; border-left: 1px solid var(--rule); }
  .sclist { display: flex; flex-direction: column; gap: 9px; }
  .scrow { display: grid; grid-template-columns: 110px 1fr; align-items: baseline; gap: 14px; }
  .sckey { font-family: var(--ak-mono); font-size: 11.5px; letter-spacing: 0.04em; padding: 3px 7px; border: 1px solid var(--rule-hi); color: var(--ink); width: fit-content; line-height: 1; }
  .sclabel { font-size: 12.5px; color: var(--ink-2); }
  .sheetfoot { padding: 14px 24px; border-top: 1px solid var(--rule); display: flex; justify-content: space-between; align-items: baseline; }

  /* analytics */
  .acard { padding: 8px 10px 10px; }
  .acardrow { display: flex; justify-content: space-between; margin-top: 4px; }
  .analytics { border-top: 1px solid var(--rule); display: grid; grid-template-columns: 220px 1fr 340px; background: var(--surface); }
  .anleft { padding: 16px; border-right: 1px solid var(--rule); display: flex; flex-direction: column; gap: 10px; }
  .anrow { display: flex; justify-content: space-between; align-items: baseline; }
  .anrow.mb10 { margin-bottom: 10px; }
  .antitle { font-size: 16px; letter-spacing: -0.02em; line-height: 1.1; color: var(--ink); }
  .anmid { padding: 14px 22px; border-right: 1px solid var(--rule); display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }
  .stackbar { display: flex; height: 8px; gap: 1px; background: var(--rule); }
  .stacklegend { display: flex; justify-content: space-between; margin-top: 8px; gap: 8px; }
  .stackseg { display: flex; flex-direction: column; gap: 1px; }
  .barchart { display: flex; align-items: flex-end; gap: 4px; height: 56px; }
  .barcol { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .barcolinner { flex: 1; width: 100%; display: flex; align-items: flex-end; }
  .bar { width: 100%; background: var(--ink-2); min-height: 4px; }
  .bar.zero { background: transparent; border-top: 1px solid var(--rule); min-height: 1px; }
  .barlabels { display: flex; gap: 4px; margin-top: 4px; }
  .anright { padding: 14px 18px; }
  .taglist { display: flex; flex-direction: column; gap: 5px; }
  .tagrow { display: grid; grid-template-columns: 70px 1fr 30px; align-items: center; gap: 10px; }
  .tagbar { height: 4px; background: var(--surface-3); position: relative; }
  .tagbarfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--ink-2); }
</style>
