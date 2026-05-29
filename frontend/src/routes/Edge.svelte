<!-- Seg 9 — Round-4 edge (C1–C4): ws-error / update-banner / splash⚠ /
     onboarding. Stacked for audit. Splash = §11.1 brand mark (Nordic depth) —
     ⚠ PASS_WITH_NOTES: needs human brand-fidelity review (morning). -->
<script>
  import ArkivLogo from '../lib/ArkivLogo.svelte'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Thumb from '../lib/Thumb.svelte'
  import TopBar from '../lib/TopBar.svelte'
  import { MEDIA } from '../lib/mockData.js'

  const theme = 'dark'
  const wsCols = '1fr 80px 76px 80px 76px 70px 1fr'
  const wsQueue = [
    { name: 'A7S3_C015_240519.mov', size: '1.4 GB', probe: 'done', trans: 'done', tag: 'paused', pct: 82 },
    { name: 'A7S3_C016_240519.mov', size: '2.4 GB', probe: 'done', trans: 'paused', tag: '—', pct: 48 },
    { name: 'GH6_4830.mp4', size: '186 MB', probe: 'paused', trans: '—', tag: '—', pct: 22 },
    { name: 'GH6_4831.mp4', size: '212 MB', probe: '—', trans: '—', tag: '—', pct: 0 },
  ]
  const stageText = (s) => (s === 'paused' ? 'WAIT' : s === '—' ? '·' : 'OK')
  const notes = [
    ['REINSTATED', ['Apple Silicon Metal GPU path for whisper.cpp — 4.2× faster than CPU on M-series']],
    ['ADDED', ['.braw ingest (Blackmagic Raw 12K, 8K, 6K)', 'Ollama backend support for vision tagging (llava 13b, llava 34b)', 'Search clause: codec, bitrate, color-space']],
    ['FIXED · 14', ['Inspector waveform clipping at >30min audio', 'EDL export drops first event when project starts on non-zero timecode', 'NAS unmount during ingest no longer kills the daemon', 'Whisper hallucinations on silence > 8s', '+ 10 minor']],
  ]
  const stack = [
    ['FRONTEND', 'Tauri 2.0 · Svelte 4 · Vite'], ['BACKEND', 'Rust · SQLite + tantivy'],
    ['AUDIO', 'whisper.cpp · large-v3'], ['VISION', 'Ollama · llava-13b / qwen3-vl'], ['MEDIA', 'FFmpeg 7 · pyscenedetect'],
  ]
  const steps = [
    { n: '1', title: 'Storage', sub: 'Mount your NAS or pick a folder.', status: 'done', detail: 'NAS01 · /vol/nas01 · 12 TB' },
    { n: '2', title: 'AI models', sub: 'Whisper for transcripts. Ollama for tags.', status: 'active' },
    { n: '3', title: 'First project', sub: "Where today's footage lives.", status: 'pending' },
  ]
  const whisperDetail = [['LANGUAGES', '99 · auto-detect on'], ['DEVICE', 'Metal GPU · Apple M2 Max'], ['SPEED', '~0.4s per minute of audio'], ['QUALITY', 'state of the art · zh / en / ja']]
  const visionDetail = [['DEVICE', 'Metal GPU · Apple M2 Max'], ['BATCH', '12 frames per clip'], ['POOL', 'Auto · 8 tags max per clip'], ['QUALITY', 'good balance · alt: 34b for stronger tagging']]
</script>

<div class="stack">

  <!-- C1 · WS ERROR -->
  <Eyebrow style="padding-left:8px;">C1 · websocket disconnected</Eyebrow>
  <div class="artboard rows52">
    <div class="topbar"><ArkivLogo size={16} /><Mono dim style="font-size:10px;">v0.9.2</Mono><div class="grow"></div><Mono style="font-size:11px;color:var(--ink);letter-spacing:0.05em;">ws://localhost:8501/ws/ingest · <span class="b">DISCONNECTED</span></Mono><button class="ak-btn">Reconnect</button></div>
    <div>
      <div class="dbanner">
        <div>
          <div class="dbhead"><Eyebrow style="color:var(--ink);">◇ STREAM DISCONNECTED · 00:14 AGO</Eyebrow><Mono dim style="font-size:10px;">retry 3 of 8 · next in 00:08</Mono></div>
          <div class="ak-display dbtitle">Ingest paused. 34 of 52 files done · 18 queued.</div>
          <Mono dim style="font-size:11px;margin-top:6px;line-height:1.5;display:block;">No data loss — files already probed/transcribed/tagged are saved. Queue resumes automatically when the daemon comes back, or you can switch to background mode and close this window.</Mono>
        </div>
        <div class="dbbtns"><button class="ak-btn ak-btn--primary wide">Reconnect now</button><button class="ak-btn">Restart daemon</button><button class="ak-btn">Run in background</button></div>
      </div>
      <div class="wsqueue">
        <div class="wsqhead"><Eyebrow>Queue · frozen at 14:12:18</Eyebrow><Mono dim style="font-size:10.5px;">34/52 · 65.4%</Mono></div>
        <div class="wsbar"><div class="wsbarfill"></div></div>
        <div class="wsrow wshrow" style="grid-template-columns:{wsCols};">{#each ['FILE', 'SIZE', 'PROBE', 'TRANSCRIBE', 'TAG', 'ETA', 'PROGRESS'] as h}<Mono dim style="font-size:9.5px;letter-spacing:0.1em;">{h}</Mono>{/each}</div>
        {#each wsQueue as f (f.name)}
          <div class="wsrow" style="grid-template-columns:{wsCols};">
            <Mono style="font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{f.name}</Mono>
            <Mono dim style="font-size:10.5px;">{f.size}</Mono>
            <span class="pstage" class:ok={f.probe === 'done'}>{stageText(f.probe)}</span>
            <span class="pstage" class:ok={f.trans === 'done'}>{stageText(f.trans)}</span>
            <span class="pstage" class:ok={f.tag === 'done'}>{stageText(f.tag)}</span>
            <Mono dim style="font-size:10.5px;">—</Mono>
            <div class="wspbar"><div class="wspfill" style="width:{f.pct}%;"></div></div>
          </div>
        {/each}
        <Mono dim style="font-size:10.5px;margin-top:16px;letter-spacing:0.04em;display:block;">Diagnostics ·  systemctl status arkiv-daemon  ·  ~/.arkiv/logs/daemon-2026-05-26.log</Mono>
      </div>
    </div>
  </div>

  <!-- C2 · UPDATE BANNER -->
  <Eyebrow style="padding-left:8px;">C2 · update available</Eyebrow>
  <div class="artboard rows40">
    <div class="ubanner">
      <Mono style="font-size:10.5px;letter-spacing:0.1em;font-weight:700;">● UPDATE</Mono>
      <div class="ubmid"><Mono style="font-size:12.5px;font-weight:600;">arkiv  v0.9.3</Mono><Mono style="font-size:11px;opacity:0.7;">↘ v0.9.2 → 0.9.3 · ready · 18 MB · signed</Mono></div>
      <Mono style="font-size:10px;opacity:0.7;letter-spacing:0.06em;">REINSTATES Apple Silicon GPU PATH · ADDS .braw INGEST · FIXES 14 BUGS</Mono>
      <div class="ubbtns"><button class="ubghost">Notes</button><button class="ubsolid">Install &amp; restart</button><button class="ubx">✕</button></div>
    </div>
    <TopBar />
    <div class="ubmain">
      <div class="ubgrid">
        <div class="grid4">{#each MEDIA.slice(0, 12) as m (m.id)}<div class="cellbg"><div class="thumb169"><Thumb seed={m.id} kind={m.kind} {theme} /></div></div>{/each}</div>
      </div>
      <aside class="notes">
        <div class="noteshead"><Eyebrow style="margin-bottom:8px;">Release notes</Eyebrow><div class="ak-display notesver">v0.9.3</div><Mono dim style="font-size:11px;margin-top:6px;letter-spacing:0.04em;">released 2026-05-24 · 18.2 MB · signed sha-256</Mono></div>
        <div class="notesbody">
          {#each notes as [title, items]}
            <div><Eyebrow style="margin-bottom:8px;">{title}</Eyebrow><div class="notelist">{#each items as line}<div class="noteline"><Mono dim style="font-size:11px;">·</Mono><span>{line}</span></div>{/each}</div></div>
          {/each}
          <div class="grow"></div>
          <Mono dim style="font-size:10px;line-height:1.5;margin-top:4px;">Auto-update channel:  stable  ·  rollback to v0.9.2 stays available for 7 days.</Mono>
        </div>
      </aside>
    </div>
  </div>

  <!-- C3 · SPLASH ⚠ brand mark -->
  <Eyebrow style="padding-left:8px;">C3 · splash / about  ⚠ brand-artifact (human review)</Eyebrow>
  <div class="artboard rel">
    <div class="splashframe">
      <div class="splashhead"><Eyebrow>arkiv  ·  about · welcome</Eyebrow><div class="grow"></div><Mono dim style="font-size:11px;">ESC · CLOSE</Mono></div>
      <div class="splashbody">
        <div class="splashleft">
          <Eyebrow style="margin-bottom:22px;">vulture.s</Eyebrow>
          <div class="ak-display splashmark">arkiv<span class="splashdot">.</span></div>
          <div class="depth">
            <div class="depthsvg">
              <svg viewBox="0 0 560 320" width="100%" height="100%" preserveAspectRatio="none" style="display:block;">
                <defs>
                  <linearGradient id="ak-depth" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stop-color="var(--cyan)" stop-opacity="0.06" />
                    <stop offset="0.12" stop-color="var(--cyan)" stop-opacity="0.22" />
                    <stop offset="0.22" stop-color="var(--cyan)" stop-opacity="0.28" />
                    <stop offset="0.45" stop-color="var(--cyan)" stop-opacity="0.12" />
                    <stop offset="0.80" stop-color="var(--cyan)" stop-opacity="0.03" />
                    <stop offset="1" stop-color="var(--cyan)" stop-opacity="0" />
                  </linearGradient>
                  <linearGradient id="ak-left-fade" x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0" stop-color="#fff" stop-opacity="0.35" />
                    <stop offset="0.02" stop-color="#fff" stop-opacity="0.65" />
                    <stop offset="0.06" stop-color="#fff" stop-opacity="1" />
                  </linearGradient>
                  <mask id="ak-left-mask"><rect x="0" y="0" width="560" height="320" fill="url(#ak-left-fade)" /></mask>
                </defs>
                <rect x="0" y="0" width="560" height="320" fill="url(#ak-depth)" mask="url(#ak-left-mask)" />
              </svg>
            </div>
            <h1 class="ak-display splashsub">The open-source media asset manager for filmmakers who own their data.</h1>
            <div class="grow"></div>
          </div>
          <div class="etym">
            <Mono dim style="font-size:10.5px;letter-spacing:0.06em;line-height:1.7;"><span class="b ink">arkiv</span>  <span style="letter-spacing:0.02em;">/ˈarkiːv/</span>  <span class="i">n.</span>  [sv. da. no., ‹ L. <span class="i">archīvum</span>]<br />a place where things are kept.</Mono>
            <Mono dim style="font-size:10.5px;letter-spacing:0.06em;">v0.9.2 · 2026-05-26</Mono>
          </div>
        </div>
        <div class="splashright">
          <section><Eyebrow style="margin-bottom:10px;">Stack</Eyebrow><div class="sptext">Local-first. Zero cloud. FFmpeg probe · Whisper transcript · Ollama vision tags. Export to DaVinci Resolve EDL / FCPXML. MIT licensed.</div></section>
          <section><Eyebrow style="margin-bottom:12px;">Built with</Eyebrow><div class="builtgrid">{#each stack as [k, v]}<Mono dim>{k}</Mono><Mono>{v}</Mono>{/each}</div></section>
          <section><Eyebrow style="margin-bottom:12px;">Why local-first</Eyebrow><div class="sptext sm">Your footage is your work. Cloud DAMs lock you in, ration your bandwidth, and read your transcripts.<br /><br />arkiv stays on your machine. Same models the big platforms run — Whisper, llava — but on hardware you already own. Nothing leaves unless you export it yourself.</div></section>
          <section><Eyebrow style="margin-bottom:10px;">Acknowledgements</Eyebrow><Mono dim style="font-size:11px;line-height:1.65;">FFmpeg · whisper.cpp · Ollama · pyscenedetect · tantivy · Tauri · the SvelteKit team. Type: Helvetica Now Display Black ·  Inter · JetBrains Mono ·  Noto Sans TC. The mark above arkiv is still water with a single quiet event — Nordic, vertical, not painted.</Mono></section>
          <div class="grow"></div>
        </div>
      </div>
      <div class="splashfoot"><Mono dim style="font-size:10.5px;letter-spacing:0.06em;">MIT  ·  © 2026 vulture.s  ·  no telemetry  ·  no phone-home</Mono><div class="fbtns"><button class="ak-btn">License</button><button class="ak-btn">Diagnostics</button><button class="ak-btn ak-btn--primary">Get started →</button></div></div>
    </div>
  </div>

  <!-- C4 · ONBOARDING -->
  <Eyebrow style="padding-left:8px;">C4 · first-run onboarding</Eyebrow>
  <div class="artboard rows52f">
    <div class="topbar ob"><ArkivLogo size={16} /><Mono dim style="font-size:11px;">first run · setup</Mono><div class="grow"></div><Mono dim style="font-size:10.5px;letter-spacing:0.05em;">You can change any of this later in Settings.</Mono><button class="ak-btn">Skip · I'll do this later</button></div>
    <div class="obbody">
      <div class="stepper">
        <Eyebrow style="margin-bottom:28px;">Setup · 2 of 3</Eyebrow>
        <div class="steps">
          {#each steps as s}
            <div class="step">
              <span class="stepn" class:active={s.status === 'active'} class:done={s.status === 'done'}>{s.status === 'done' ? '✓' : s.n}</span>
              <div>
                <div class="ak-display steptitle" class:dim={s.status === 'pending'} class:muted={s.status !== 'active' && s.status !== 'done'}>{s.title}</div>
                <Mono dim style="font-size:11px;margin-top:4px;display:block;line-height:1.45;">{s.sub}</Mono>
                {#if s.detail}<Mono style="font-size:11px;margin-top:6px;display:block;color:var(--ink);">{s.detail}</Mono>{/if}
              </div>
            </div>
          {/each}
        </div>
        <div class="grow"></div>
        <div class="stepnote"><Mono dim style="font-size:10.5px;line-height:1.6;">All three steps run locally. No accounts, no sign-ups, no cloud.</Mono></div>
      </div>
      <div class="stepdetail">
        <Eyebrow style="margin-bottom:10px;">Step 2 · AI models</Eyebrow>
        <div class="ak-display obbig">Pick the models<br />that run locally.</div>
        <Mono dim style="font-size:13px;margin-top:14px;line-height:1.65;max-width:640px;display:block;">arkiv detected your hardware. These defaults are tuned for an Apple Silicon machine with 64 GB RAM — most filmmakers don't need to change them.</Mono>
        <div class="modelcards">
          {#each [{ eyebrow: 'WHISPER · TRANSCRIPTION', title: 'whisper-large-v3', size: '1.55 GB · download once', detail: whisperDetail }, { eyebrow: 'OLLAMA · VISION', title: 'llava:13b-v1.6-q4_K_M', size: '7.4 GB · download on first ingest', detail: visionDetail }] as c}
            <div class="modelcard">
              <div class="mctop"><Eyebrow>{c.eyebrow}</Eyebrow><Mono style="font-size:9.5px;letter-spacing:0.08em;padding:2px 6px;background:var(--invert);color:var(--invert-ink);">SELECTED</Mono></div>
              <div class="mctitle">{c.title}</div>
              <Mono dim style="font-size:11px;letter-spacing:0.03em;">{c.size}</Mono>
              <div class="mcdetail">{#each c.detail as [k, v]}<Mono dim style="font-size:10px;letter-spacing:0.08em;">{k}</Mono><Mono style="font-size:11px;color:var(--ink-2);">{v}</Mono>{/each}</div>
              <button class="ak-btn mcbtn">Pick a different model…</button>
            </div>
          {/each}
        </div>
        <div class="dlbox">
          <Mono dim style="font-size:9.5px;letter-spacing:0.1em;">◇ DOWNLOAD</Mono>
          <Mono style="font-size:12px;color:var(--ink);">Combined  ≈ 8.95 GB · about 14 minutes on a typical home connection</Mono>
          <div class="grow"></div>
          <button class="ak-btn">Start download now</button>
          <button class="ak-btn">Defer to first ingest</button>
        </div>
        <div class="grow"></div>
        <Mono dim style="font-size:10.5px;margin-top:16px;letter-spacing:0.04em;">Want a different model? More options in Settings · Transcription / Vision tagging.</Mono>
      </div>
    </div>
    <div class="obfoot">
      <div class="dots"><span class="dot done"></span><div class="dline ink2"></div><span class="dot active"></span><div class="dline"></div><span class="dot"></span></div>
      <div class="fbtns"><button class="ak-btn">← Storage</button><button class="ak-btn ak-btn--primary widex">Next · First project →</button></div>
    </div>
  </div>

</div>

<style>
  .stack { display: flex; flex-direction: column; gap: 28px; padding: 24px 0; }
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); overflow: hidden; margin: 0 auto; }
  .artboard.rel { position: relative; }
  .artboard.rows52 { display: grid; grid-template-rows: 52px 1fr; }
  .artboard.rows52f { display: grid; grid-template-rows: 52px 1fr 88px; }
  .artboard.rows40 { display: grid; grid-template-rows: 40px 52px 1fr; }
  .grow { flex: 1; }
  .b { font-weight: 700; }
  .i { font-style: italic; }
  .ink { color: var(--ink); }
  .wide { padding: 8px 18px; }
  .widex { padding: 10px 24px; }
  .fbtns { display: flex; gap: 8px; }
  .topbar { display: flex; align-items: center; border-bottom: 1px solid var(--rule); padding: 0 16px; gap: 16px; }
  .topbar.ob { padding: 0 24px; gap: 18px; }
  .grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--rule); }
  .cellbg { background: var(--bg); }
  .thumb169 { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); }

  /* C1 ws error */
  .dbanner { border-top: 1px solid var(--ink); border-bottom: 1px dashed var(--rule-hi); background: var(--surface); padding: 18px 60px; display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 32px; }
  .dbhead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 6px; }
  .dbtitle { font-size: 22px; letter-spacing: -0.03em; line-height: 1.1; color: var(--ink); }
  .dbbtns { display: flex; flex-direction: column; gap: 6px; }
  .wsqueue { padding: 24px 60px; opacity: 0.55; }
  .wsqhead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  .wsbar { height: 4px; background: var(--surface-3); position: relative; margin-bottom: 16px; }
  .wsbarfill { position: absolute; left: 0; top: 0; bottom: 0; width: 65.4%; background: var(--ink-2); }
  .wsrow { display: grid; gap: 14px; align-items: center; padding: 7px 0; border-bottom: 1px solid var(--rule); }
  .wsrow.wshrow { padding: 0 0 8px; }
  .pstage { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; color: var(--quiet); padding: 2px 6px; border: 1px dashed var(--rule); width: fit-content; text-align: center; line-height: 1.1; }
  .pstage.ok { color: var(--ink); font-weight: 600; border: 1px solid var(--ink); }
  .wspbar { height: 3px; background: var(--surface-3); position: relative; }
  .wspfill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--ink-2); }

  /* C2 update banner */
  .ubanner { background: var(--invert); color: var(--invert-ink); padding: 0 24px; display: flex; align-items: center; gap: 24px; }
  .ubmid { flex: 1; display: flex; align-items: baseline; gap: 14px; }
  .ubbtns { display: flex; gap: 6px; }
  .ubghost { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; padding: 4px 10px; background: transparent; color: var(--invert-ink); border: 1px solid var(--invert-ink); cursor: pointer; line-height: 1; }
  .ubsolid { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; padding: 4px 10px; background: var(--invert-ink); color: var(--invert); border: 1px solid var(--invert-ink); cursor: pointer; line-height: 1; font-weight: 700; }
  .ubx { background: transparent; border: none; color: var(--invert-ink); cursor: pointer; font-size: 13px; opacity: 0.6; }
  .ubmain { display: grid; grid-template-columns: 1fr 480px; min-height: 0; overflow: hidden; }
  .ubgrid { border-right: 1px solid var(--rule); padding: 22px; opacity: 0.6; overflow: hidden; }
  .notes { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .noteshead { padding: 22px 28px 18px; border-bottom: 1px solid var(--rule); }
  .notesver { font-size: 34px; letter-spacing: -0.04em; line-height: 0.95; color: var(--ink); }
  .notesbody { padding: 20px 28px; flex: 1; overflow: hidden; display: flex; flex-direction: column; gap: 18px; }
  .notelist { display: flex; flex-direction: column; gap: 5px; }
  .noteline { display: grid; grid-template-columns: 12px 1fr; gap: 8px; font-size: 12px; color: var(--ink-2); line-height: 1.5; }

  /* C3 splash */
  .splashframe { position: absolute; inset: 28px; box-shadow: inset 0 0 0 1px var(--invert); display: grid; grid-template-rows: 60px 1fr 88px; }
  .splashhead { display: flex; align-items: center; padding: 0 28px; border-bottom: 1px solid var(--invert); }
  .splashbody { display: grid; grid-template-columns: 1.1fr 1fr; min-height: 0; overflow: hidden; }
  .splashleft { padding: 48px 56px 36px; border-right: 1px solid var(--rule); display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .splashmark { font-size: 168px; letter-spacing: -0.06em; line-height: 0.82; color: var(--ink); font-weight: 900; }
  .splashdot { font-family: var(--ak-mono); font-size: 96px; font-weight: 400; letter-spacing: 0; }
  .depth { position: relative; margin-top: 28px; margin-bottom: 4px; flex: 1; min-height: 240px; display: flex; flex-direction: column; }
  .depthsvg { position: absolute; inset: 0; }
  .splashsub { position: relative; margin: 40px 0 0 0; font-size: 26px; letter-spacing: -0.025em; line-height: 1.08; color: var(--ink); max-width: 460px; font-weight: 900; }
  .etym { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding-top: 14px; border-top: 1px solid var(--rule); }
  .splashright { padding: 48px 48px 36px; display: flex; flex-direction: column; gap: 28px; min-height: 0; overflow: hidden; }
  .sptext { font-size: 13px; line-height: 1.7; color: var(--ink-2); }
  .sptext.sm { font-size: 12.5px; line-height: 1.65; }
  .builtgrid { display: grid; grid-template-columns: 100px 1fr; row-gap: 6px; column-gap: 14px; font-size: 12px; }
  .splashfoot { display: flex; align-items: center; justify-content: space-between; padding: 0 28px; border-top: 1px solid var(--invert); }

  /* C4 onboarding */
  .obbody { display: grid; grid-template-columns: 320px 1fr; min-height: 0; overflow: hidden; }
  .stepper { border-right: 1px solid var(--rule); padding: 40px 32px; display: flex; flex-direction: column; }
  .steps { display: flex; flex-direction: column; gap: 22px; }
  .step { display: grid; grid-template-columns: 32px 1fr; gap: 14px; align-items: baseline; }
  .stepn { font-family: var(--ak-mono); font-size: 11px; letter-spacing: 0.06em; width: 24px; height: 24px; background: transparent; color: var(--quiet); border: 1px solid var(--rule); display: inline-flex; align-items: center; justify-content: center; font-weight: 400; }
  .stepn.done { color: var(--ink); border: 1px solid var(--ink); }
  .stepn.active { background: var(--invert); color: var(--invert-ink); border: none; font-weight: 700; }
  .steptitle { font-size: 18px; letter-spacing: -0.02em; line-height: 1.1; color: var(--ink); }
  .steptitle.muted { color: var(--ink-2); }
  .steptitle.dim { opacity: 0.55; }
  .stepnote { padding-top: 24px; border-top: 1px solid var(--rule); }
  .stepdetail { padding: 40px 60px 30px; overflow: hidden; display: flex; flex-direction: column; }
  .obbig { font-size: 56px; letter-spacing: -0.04em; line-height: 0.95; color: var(--ink); }
  .modelcards { margin-top: 36px; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  .modelcard { padding: 18px 20px; border: 1px solid var(--rule-hi); box-shadow: inset 0 0 0 1px var(--invert); background: var(--surface); display: flex; flex-direction: column; gap: 12px; }
  .mctop { display: flex; justify-content: space-between; align-items: baseline; }
  .mctitle { font-family: var(--ak-mono); font-size: 16px; font-weight: 600; color: var(--ink); }
  .mcdetail { display: grid; grid-template-columns: 90px 1fr; row-gap: 4px; column-gap: 12px; font-size: 11.5px; margin-top: 4px; }
  .mcbtn { margin-top: 4px; }
  .dlbox { margin-top: 28px; padding: 14px 18px; border: 1px dashed var(--rule-hi); display: flex; align-items: baseline; gap: 14px; }
  .obfoot { border-top: 1px solid var(--rule); padding: 0 60px; display: flex; align-items: center; justify-content: space-between; }
  .dots { display: flex; align-items: center; gap: 14px; }
  .dot { display: inline-block; width: 8px; height: 8px; background: transparent; border: 1px solid var(--rule-hi); }
  .dot.done { background: var(--ink-2); border: none; }
  .dot.active { background: var(--ink); border: none; }
  .dline { width: 28px; height: 1px; background: var(--rule); }
  .dline.ink2 { background: var(--ink-2); }
</style>
