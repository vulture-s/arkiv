<!-- Seg 7 — Screen 6: Settings modal over dimmed backdrop. 1px frame chrome
     (no drop shadow). Left nav + form sections. Form helpers inline. -->
<script>
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'

  const theme = 'dark'
  const nav = [
    ['general', 'General', true], ['ingest', 'Ingest', false], ['transcription', 'Transcription', false],
    ['vision', 'Vision tagging', false], ['export', 'Export defaults', false], ['storage', 'Storage · proxy', false],
    ['projects', 'Project registry', false], ['advanced', 'Advanced', false], ['about', 'About', false],
  ]
  const themeOpts = [['dark', 'Dark', true], ['light', 'Light', false], ['system', 'System', false]]
  const densityOpts = [['comfort', 'Comfortable', false], ['default', 'Default', true], ['dense', 'Dense', false]]
  const langs = [['zh-Hant', true], ['en', true], ['ja', true], ['ko', false], ['auto', true]]
  const edlFps = [['24', '24', true], ['25', '25', false], ['30', '30', false], ['60', '60', false]]
  const proxyRes = [['540', '540p', false], ['1080', '1080p', true], ['off', 'Off', false]]
  const ticks = [0, 1, 2, 3, 4]
  const backdrop = Array(12)
</script>

<div class="artboard" data-theme={theme}>
  <!-- backdrop -->
  <div class="backdrop">
    <div class="bdtop"></div>
    <div class="bdbody">
      <div class="bdside"></div>
      <div class="bdgrid">{#each backdrop as _}<div class="bdcell"></div>{/each}</div>
      <div class="bdside right"></div>
    </div>
  </div>
  <div class="scrim"></div>

  <!-- modal -->
  <div class="modal">
    <div class="mhead">
      <div class="mtitle">
        <Eyebrow style="color:var(--ink-2);">Settings</Eyebrow>
        <div class="ak-display mtitlebig">Preferences</div>
      </div>
      <button class="mclose">ESC · CLOSE</button>
    </div>

    <div class="mbody">
      <nav class="mnav">
        {#each nav as [id, label, active]}
          <button class="navbtn" class:active>{label}</button>
        {/each}
      </nav>

      <div class="form">
        <!-- Appearance -->
        <section>
          <div class="fshead">
            <Eyebrow style="margin-bottom:4px;">THEME · INTERFACE</Eyebrow>
            <div class="ak-display fstitle">Appearance</div>
            <div class="fsdesc">vulture.s editorial · dark default. UI scale adjusts root font-size only.</div>
          </div>
          <div class="frows">
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Theme</Mono>
              <div class="seg">{#each themeOpts as [id, label, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{label}</button>{/each}</div>
            </div>
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">UI scale</Mono>
              <div class="scale">
                <button class="ak-btn scalebtn">−</button>
                <div class="scaletrack"><div class="scaleticks">{#each ticks as _}<div class="tick"></div>{/each}</div><div class="scaleknob"></div></div>
                <button class="ak-btn scalebtn">+</button>
                <Mono style="font-size:12px;font-weight:600;color:var(--ink);min-width:48px;">100%</Mono>
              </div>
            </div>
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Type density</Mono>
              <div class="seg">{#each densityOpts as [id, label, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{label}</button>{/each}</div>
            </div>
          </div>
        </section>

        <!-- Transcription -->
        <section>
          <div class="fshead"><Eyebrow style="margin-bottom:4px;">WHISPER · LOCAL</Eyebrow><div class="ak-display fstitle">Transcription model</div></div>
          <div class="frows">
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Model</Mono>
              <div class="dropdown"><Mono style="font-size:11.5px;color:var(--ink);">whisper-large-v3</Mono><Mono dim style="font-size:10px;">▾</Mono></div>
            </div>
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Languages</Mono>
              <div class="langs">{#each langs as [l, on]}<span class="lang" class:on>{l}</span>{/each}</div>
            </div>
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">GPU</Mono>
              <div class="dropdown"><Mono style="font-size:11.5px;color:var(--ink);">CUDA · NVIDIA RTX 4070 · 12 GB</Mono><Mono dim style="font-size:10px;">▾</Mono></div>
            </div>
          </div>
        </section>

        <!-- Vision -->
        <section>
          <div class="fshead"><Eyebrow style="margin-bottom:4px;">OLLAMA · LOCAL</Eyebrow><div class="ak-display fstitle">Vision tagging</div></div>
          <div class="frows">
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Model</Mono>
              <div class="dropdown"><Mono style="font-size:11.5px;color:var(--ink);">llava:13b-v1.6-q4_K_M</Mono><Mono dim style="font-size:10px;">▾</Mono></div>
            </div>
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Tag pool</Mono>
              <Mono style="font-size:12.5px;color:var(--ink-2);">auto · max 8 tags per file · min confidence 0.62</Mono>
            </div>
          </div>
        </section>

        <!-- Export -->
        <section>
          <div class="fshead"><Eyebrow style="margin-bottom:4px;">DAVINCI RESOLVE · EXPORT</Eyebrow><div class="ak-display fstitle">Editor handoff</div></div>
          <div class="frows">
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">EDL frame rate</Mono>
              <div class="seg">{#each edlFps as [id, label, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{label}</button>{/each}</div>
            </div>
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Proxy resolution</Mono>
              <div class="seg">{#each proxyRes as [id, label, on], i}{#if i > 0}<div class="segsep"></div>{/if}<button class="segbtn" class:on>{label}</button>{/each}</div>
            </div>
            <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Drop frame</Mono>
              <div class="toggle"><div class="toggleknob"></div></div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); position: relative; overflow: hidden; margin: 0 auto; }
  .backdrop { position: absolute; inset: 0; display: grid; grid-template-rows: 52px 1fr; }
  .bdtop { border-bottom: 1px solid var(--rule); }
  .bdbody { display: grid; grid-template-columns: 220px 1fr 340px; }
  .bdside { border-right: 1px solid var(--rule); }
  .bdside.right { border-right: none; border-left: 1px solid var(--rule); }
  .bdgrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; padding: 22px; background: var(--rule); }
  .bdcell { aspect-ratio: 16 / 9; background: var(--surface-2); }
  .scrim { position: absolute; inset: 0; background: rgba(10, 10, 12, 0.82); }

  .modal { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 960px; height: 700px; background: var(--bg); box-shadow: inset 0 0 0 1px var(--invert); display: grid; grid-template-rows: 56px 1fr; }
  .mhead { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--invert); padding: 0 24px; }
  .mtitle { display: flex; align-items: baseline; gap: 16px; }
  .mtitlebig { font-size: 22px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .mclose { font-family: var(--ak-mono); font-size: 11px; letter-spacing: 0.08em; background: transparent; border: none; color: var(--ink-2); cursor: pointer; padding: 0; }
  .mbody { display: grid; grid-template-columns: 200px 1fr; min-height: 0; overflow: hidden; }
  .mnav { border-right: 1px solid var(--rule); padding: 20px 0; display: flex; flex-direction: column; }
  .navbtn { text-align: left; padding: 7px 24px; background: transparent; border: none; border-left: 2px solid transparent; color: var(--ink-2); font-size: 13px; font-weight: 400; cursor: pointer; font-family: inherit; }
  .navbtn.active { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  .form { overflow: hidden; padding: 24px 32px; display: flex; flex-direction: column; gap: 22px; }
  .fshead { margin-bottom: 12px; }
  .fstitle { font-size: 18px; letter-spacing: -0.02em; line-height: 1.1; color: var(--ink); }
  .fsdesc { font-size: 11.5px; color: var(--quiet); margin-top: 3px; }
  .frows { display: flex; flex-direction: column; gap: 10px; }
  .frow { display: grid; grid-template-columns: 140px 1fr; align-items: center; gap: 16px; }
  .seg { display: flex; border: 1px solid var(--rule); width: fit-content; }
  .segsep { width: 1px; background: var(--rule); }
  .segbtn { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 5px 12px; background: transparent; color: var(--ink-2); border: none; cursor: pointer; line-height: 1; font-weight: 400; }
  .segbtn.on { background: var(--invert); color: var(--invert-ink); font-weight: 700; }
  .scale { display: flex; align-items: center; gap: 14px; }
  .scalebtn { width: 28px; padding: 5px 0; text-align: center; }
  .scaletrack { flex: 1; max-width: 240px; height: 1px; background: var(--rule); position: relative; }
  .scaleticks { position: absolute; left: 0; right: 0; top: -2px; height: 5px; display: flex; justify-content: space-between; }
  .tick { width: 1px; height: 5px; background: var(--rule); }
  .scaleknob { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 10px; height: 10px; background: var(--ink); }
  .dropdown { display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--rule); padding: 6px 10px; width: 100%; max-width: 360px; cursor: pointer; }
  .langs { display: flex; gap: 4px; }
  .lang { font-family: var(--ak-mono); font-size: 10.5px; padding: 4px 8px; line-height: 1; background: transparent; color: var(--ink-2); border: 1px solid var(--rule); cursor: pointer; }
  .lang.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
  .toggle { width: 36px; height: 18px; border: 1px solid var(--rule-hi); position: relative; cursor: pointer; background: transparent; }
  .toggleknob { position: absolute; top: 1px; left: 1px; bottom: 1px; width: 14px; background: var(--ink-2); }
</style>
