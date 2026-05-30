<!-- Seg 6 — Screen 5: ingest progress. Hero aggregate + per-file stage queue
     + live log stream. Static mock (no real websocket). cyan = sanctioned
     ingest-progress accent. -->
<script>
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'

  const theme = 'dark'
  const cols = '1fr 76px 76px 80px 76px 70px 1fr'
  const metrics = [
    ['PROBED', '48', '/ 52'], ['TRANSCRIBED', '36', '/ 52'], ['TAGGED', '34', '/ 52'],
    ['THROUGHPUT', '8.4', 'MB/s'], ['GPU', '84%', 'RTX 4070'],
  ]
  const FILES = [
    { name: 'A7S3_C012_240519.mov', size: '1.8 GB', probe: 'done', trans: 'done', tag: 'done', pct: 100 },
    { name: 'A7S3_C013_240519.mov', size: '2.1 GB', probe: 'done', trans: 'done', tag: 'done', pct: 100 },
    { name: 'A7S3_C014_240519.mov', size: '780 MB', probe: 'done', trans: 'done', tag: 'done', pct: 100 },
    { name: 'A7S3_C015_240519.mov', size: '1.4 GB', probe: 'done', trans: 'done', tag: 'running', pct: 82, eta: '00:14' },
    { name: 'A7S3_C016_240519.mov', size: '2.4 GB', probe: 'done', trans: 'running', tag: 'queued', pct: 48, eta: '01:42' },
    { name: 'GH6_4830.mp4', size: '186 MB', probe: 'done', trans: 'queued', tag: 'queued', pct: 22 },
    { name: 'GH6_4831.mp4', size: '212 MB', probe: 'running', trans: 'queued', tag: 'queued', pct: 8 },
    { name: 'INTERVIEW_AMB_03.wav', size: '64 MB', probe: 'queued', trans: 'queued', tag: 'queued', pct: 0 },
    { name: 'A7S3_C017_240519.mov', size: '1.2 GB', probe: 'queued', trans: 'queued', tag: 'queued', pct: 0 },
    { name: 'A7S3_C018_240519.mov', size: '980 MB', probe: 'queued', trans: 'queued', tag: 'queued', pct: 0 },
    { name: 'A7S3_C019_240519.mov', size: '1.7 GB', probe: 'queued', trans: 'queued', tag: 'queued', pct: 0 },
    { name: 'A7S3_C020_240519.mov', size: '2.2 GB', probe: 'queued', trans: 'queued', tag: 'queued', pct: 0 },
  ]
  const stageText = { done: 'OK', running: 'RUN', queued: '·' }
  const LOG = [
    { t: '14:12:34.214', lvl: 'info', stage: 'tag', msg: 'A7S3_C015 · vision: cycling, road, tibet, sky (4 tags)' },
    { t: '14:12:33.108', lvl: 'info', stage: 'trans', msg: 'A7S3_C015 · whisper done · 1247 tokens · 98.3% conf' },
    { t: '14:12:30.842', lvl: 'info', stage: 'probe', msg: 'A7S3_C016 · H.265 4K 24p · 240Mbps · S-Log3' },
    { t: '14:12:30.114', lvl: 'info', stage: 'thumb', msg: 'A7S3_C016 · scene detection: 12 cuts found' },
    { t: '14:12:28.491', lvl: 'warn', stage: 'trans', msg: 'GH6_4831 · low audio level (-42dB), transcript may be poor' },
    { t: '14:12:26.808', lvl: 'info', stage: 'probe', msg: 'GH6_4831 · H.264 4K 60p · 100Mbps' },
    { t: '14:12:24.022', lvl: 'info', stage: 'tag', msg: 'A7S3_C014 · vision: cycling, mountain, golden hour' },
    { t: '14:12:22.717', lvl: 'info', stage: 'trans', msg: 'A7S3_C014 · whisper done · 412 tokens · 96.8% conf' },
    { t: '14:12:18.443', lvl: 'error', stage: 'tag', msg: 'A7S3_C013 · vision model timeout, retry 1/3' },
    { t: '14:12:14.281', lvl: 'info', stage: 'probe', msg: 'INTERVIEW_AMB_03 · 48kHz 24bit · 8min 32s' },
    { t: '14:12:11.604', lvl: 'info', stage: 'tag', msg: 'A7S3_C013 · vision: cycling, tibet, prayer flags (5 tags)' },
    { t: '14:12:09.218', lvl: 'info', stage: 'trans', msg: 'A7S3_C013 · whisper done · 832 tokens' },
    { t: '14:12:06.018', lvl: 'info', stage: 'thumb', msg: 'A7S3_C013 · proxy 540p generated' },
  ]
  const lvlText = { error: 'ERR', warn: 'WRN', info: 'INF' }
</script>

<div class="artboard" data-theme={theme}>
  <div class="topbar">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;">v0.9.2</Mono>
    <div class="grow"></div>
    <Mono dim style="font-size:11px;">ws://localhost:8501/ws/ingest · CONNECTED</Mono>
    <button class="ak-btn">Run in background</button>
  </div>

  <div class="split">
    <!-- LEFT -->
    <div class="left">
      <div class="hero">
        <div class="herohead">
          <Eyebrow>Ingest · folder import</Eyebrow>
          <Mono dim style="font-size:10.5px;">started 2026-05-26 14:08 · 04:32 elapsed</Mono>
        </div>
        <div class="herobig">
          <div class="ak-display bignum">34<span class="quiet">/52</span></div>
          <div class="herometa">
            <Mono dim style="font-size:11px;letter-spacing:0.05em;">FILES PROCESSED</Mono>
            <Mono style="font-size:14px;font-weight:500;display:block;margin-top:4px;">/vol/nas01/bicycle-diaries/raw/day-19/</Mono>
            <Mono dim style="font-size:10.5px;display:block;margin-top:2px;">18 remaining · est. 08:14 · 12.4 GB total</Mono>
          </div>
        </div>
        <div class="aggwrap">
          <div class="aggrow"><Mono dim style="font-size:10px;">AGGREGATE</Mono><Mono style="font-size:11px;font-weight:600;color:var(--cyan);">65.4%</Mono></div>
          <div class="aggbar"><div class="aggfill"></div><div class="aggmark"></div></div>
          <div class="metrics">
            {#each metrics as [label, value, sub]}
              <div class="metric">
                <Mono dim style="font-size:9.5px;letter-spacing:0.1em;display:block;margin-bottom:3px;">{label}</Mono>
                <span class="metricval">{value}</span><Mono dim style="font-size:10px;margin-left:4px;">{sub}</Mono>
              </div>
            {/each}
          </div>
        </div>
      </div>

      <div class="queue">
        <div class="qhead">
          <Eyebrow>Queue · 18 remaining</Eyebrow>
          <div class="qbtns"><button class="ak-btn">Pause</button><button class="ak-btn">Skip non-video</button></div>
        </div>
        <div class="qrow qheadrow" style="grid-template-columns:{cols};">
          {#each ['FILE', 'SIZE', 'PROBE', 'TRANSCRIBE', 'TAG', 'ETA', 'PROGRESS'] as h}<Mono dim style="font-size:9.5px;letter-spacing:0.1em;">{h}</Mono>{/each}
        </div>
        <div class="qrows">
          {#each FILES as f (f.name)}
            <div class="qrow" class:done={f.pct === 100} style="grid-template-columns:{cols};">
              <Mono style="font-size:11.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{f.name}</Mono>
              <Mono dim style="font-size:10.5px;">{f.size}</Mono>
              <span class="stage {f.probe}">{stageText[f.probe]}</span>
              <span class="stage {f.trans}">{stageText[f.trans]}</span>
              <span class="stage {f.tag}">{stageText[f.tag]}</span>
              <Mono dim style="font-size:10.5px;">{f.eta || '—'}</Mono>
              <div class="pbar"><div class="pfill" class:full={f.pct === 100} style="width:{f.pct}%;"></div></div>
            </div>
          {/each}
        </div>
      </div>
    </div>

    <!-- RIGHT: log -->
    <div class="right">
      <div class="loghead">
        <div class="logheadrow"><Eyebrow>Live log · ws stream</Eyebrow><Mono style="font-size:10.5px;color:var(--cyan);letter-spacing:0.08em;">● LIVE</Mono></div>
        <Mono dim style="font-size:10px;margin-top:4px;">464 events · auto-scroll on</Mono>
      </div>
      <div class="logbody">
        {#each LOG as ln}
          <div class="logline">
            <span class="logt">{ln.t}</span>
            <span class="loglvl" class:err={ln.lvl === 'error'} class:warn={ln.lvl === 'warn'}>{lvlText[ln.lvl]}</span>
            <span class="logstage">{ln.stage}</span>
            <span class="logmsg">{ln.msg}</span>
          </div>
        {/each}
      </div>
      <div class="logfoot"><Mono dim style="font-size:10px;letter-spacing:0.05em;">filter: <span class="ink">all</span> · errors · warnings · stage:probe · stage:tag</Mono></div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); display: grid; grid-template-rows: 52px 1fr; overflow: hidden; margin: 0 auto; }
  .grow { flex: 1; }
  .quiet { color: var(--quiet); }
  .ink { color: var(--ink); }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .split { display: grid; grid-template-columns: 1fr 380px; min-height: 0; overflow: hidden; }
  .left { display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--rule); }
  .hero { padding: 32px 40px 28px; border-bottom: 1px solid var(--rule); }
  .herohead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  .herobig { display: flex; align-items: baseline; gap: 18px; }
  .bignum { font-size: 80px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  .herometa { flex: 1; }
  .aggwrap { margin-top: 22px; }
  .aggrow { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
  .aggbar { height: 4px; background: var(--surface-3); position: relative; }
  .aggfill { position: absolute; left: 0; top: 0; bottom: 0; width: 65.4%; background: var(--cyan); }
  .aggmark { position: absolute; left: 65.4%; top: -2px; width: 1px; height: 8px; background: var(--cyan); }
  .metrics { display: flex; gap: 32px; margin-top: 14px; }
  .metricval { font-family: var(--ak-mono); font-size: 18px; font-weight: 600; color: var(--ink); }
  .queue { flex: 1; padding: 14px 40px 20px; overflow: hidden; display: flex; flex-direction: column; }
  .qhead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
  .qbtns { display: flex; gap: 6px; }
  .qrow { display: grid; gap: 14px; align-items: center; padding: 7px 0; border-bottom: 1px solid var(--rule); }
  .qrow.qheadrow { align-items: baseline; padding: 6px 0 8px; }
  .qrow.done { opacity: 0.55; }
  .qrows { flex: 1; overflow: hidden; }
  .stage { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; padding: 2px 6px; width: fit-content; text-align: center; line-height: 1.1; color: var(--quiet); font-weight: 400; border: 1px solid var(--rule); }
  .stage.done { color: var(--ink); font-weight: 600; border-color: var(--ink); }
  .stage.running { color: var(--cyan); font-weight: 700; border-color: var(--cyan); }
  .pbar { position: relative; height: 3px; background: var(--surface-3); }
  .pfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--cyan); }
  .pfill.full { background: var(--quiet); }
  .right { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .loghead { padding: 16px 20px 12px; border-bottom: 1px solid var(--rule); }
  .logheadrow { display: flex; justify-content: space-between; align-items: baseline; }
  .logbody { flex: 1; overflow: hidden; padding: 14px 20px; display: flex; flex-direction: column; gap: 7px; }
  .logline { display: flex; gap: 8px; font-family: var(--ak-mono); font-size: 10.5px; line-height: 1.35; }
  .logt { color: var(--quiet); flex: 0 0 78px; }
  .loglvl { color: var(--quiet); flex: 0 0 30px; text-align: center; height: 14px; }
  .loglvl.warn { color: var(--ink-2); }
  .loglvl.err { color: var(--bg); background: var(--ink); padding: 0 4px; font-weight: 700; }
  .logstage { color: var(--quiet); flex: 0 0 42px; text-transform: uppercase; letter-spacing: 0.05em; }
  .logmsg { color: var(--ink-2); flex: 1; word-break: break-word; }
  .logfoot { padding: 12px 20px; border-top: 1px solid var(--rule); }
</style>
