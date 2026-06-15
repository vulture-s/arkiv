<!-- Settings, ported to a live route (was mock-only Settings.svelte → /_design/settings).
     Honest scope — only what's genuinely backed is interactive:
       · Appearance · Theme — REAL: writes the prefs store (localStorage), re-themes
         the whole product live (app.css ships dark + light token sets).
       · UI scale / Type density — shown DISABLED: the app is px-based on a fixed
         1400×900 artboard, so a root font-size / density class has no effect.
         Faking a working control would violate the evidence discipline.
       · Engine (transcription / vision / export) — read-only current behaviour;
         model/format pickers have no API yet (plan brick 4), marked pending.
       · System — REAL: version + backend reachability + library totals + disk,
         from /api/stats. -->
<script>
  import { onMount } from 'svelte'
  import { push } from 'svelte-spa-router'
  import * as api from '../lib/api.js'
  import Mono from '../lib/Mono.svelte'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import { themePref, resolvedTheme } from '../lib/prefs.js'

  const VERSION = 'v0.9.2'
  let section = 'appearance' // appearance | engine | system
  const nav = [
    ['appearance', 'Appearance'],
    ['engine', 'Engine'],
    ['system', 'System · about'],
  ]
  const themeOpts = [['dark', 'Dark'], ['light', 'Light'], ['system', 'System']]

  // System panel — real backend state.
  let sys = 'loading' // loading | ok | error
  let stats = null
  async function loadSystem() {
    sys = 'loading'
    try { stats = await api.getStats(); sys = 'ok' } catch { sys = 'error' }
  }
  onMount(loadSystem)

  const gb = (n) => (n == null ? '—' : n >= 1000 ? `${(n / 1000).toFixed(1)} TB` : `${Math.round(n)} GB`)
  $: disk = stats?.disk ?? null
</script>

<div class="artboard" data-theme={$resolvedTheme}>
  <div class="scrim"></div>
  <div class="modal">
    <div class="mhead">
      <div class="mtitle">
        <Eyebrow style="color:var(--ink-2);">Settings</Eyebrow>
        <div class="ak-display mtitlebig">Preferences</div>
      </div>
      <button class="mclose" on:click={() => push('/')}>ESC · CLOSE</button>
    </div>

    <div class="mbody">
      <nav class="mnav">
        {#each nav as [id, label]}
          <button class="navbtn" class:active={section === id} on:click={() => (section = id)}>{label}</button>
        {/each}
      </nav>

      <div class="form">
        {#if section === 'appearance'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">THEME · INTERFACE</Eyebrow>
              <div class="ak-display fstitle">Appearance</div>
              <div class="fsdesc">vulture.s editorial. Theme applies across the whole app and persists. System follows your OS.</div>
            </div>
            <div class="frows">
              <div class="frow">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Theme</Mono>
                <div class="seg">
                  {#each themeOpts as [id, label], i}
                    {#if i > 0}<div class="segsep"></div>{/if}
                    <button class="segbtn" class:on={$themePref === id} on:click={() => themePref.set(id)}>{label}</button>
                  {/each}
                </div>
              </div>
              <div class="frow disabled">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">UI scale</Mono>
                <span class="pend">px-layout · not adjustable yet</span>
              </div>
              <div class="frow disabled">
                <Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Type density</Mono>
                <span class="pend">px-layout · not adjustable yet</span>
              </div>
            </div>
          </section>
        {:else if section === 'engine'}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">WHISPER · OLLAMA · RESOLVE</Eyebrow>
              <div class="ak-display fstitle">Engine</div>
              <div class="fsdesc">Transcription / vision models and export defaults are chosen per ingest. In-app pickers have no API yet (brick 4).</div>
            </div>
            <div class="frows">
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Transcription</Mono><span class="pend">model picker pending · brick 4</span></div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Vision tagging</Mono><span class="pend">model + tag pool pending · brick 4</span></div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Export defaults</Mono><span class="pend">EDL fps / proxy pending · brick 4</span></div>
            </div>
          </section>
        {:else}
          <section>
            <div class="fshead">
              <Eyebrow style="margin-bottom:4px;">RUNTIME</Eyebrow>
              <div class="ak-display fstitle">System</div>
            </div>
            <div class="frows">
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Version</Mono><Mono style="font-size:12px;color:var(--ink);">arkiv {VERSION}</Mono></div>
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Backend</Mono>
                {#if sys === 'loading'}<Mono dim style="font-size:12px;">checking…</Mono>
                {:else if sys === 'ok'}<Mono style="font-size:12px;color:var(--ink);"><span class="livedot">●</span> online</Mono>
                {:else}<Mono style="font-size:12px;color:var(--cyan);">unreachable</Mono>{/if}
              </div>
              {#if sys === 'ok' && stats}
                <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Library</Mono><Mono style="font-size:12px;color:var(--ink);">{stats.total} media · {Math.round((stats.total_size_mb || 0) / 1024)} GB indexed</Mono></div>
                <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Disk</Mono>
                  {#if disk}<Mono style="font-size:12px;color:var(--ink);">{gb(disk.used_gb)} / {gb(disk.total_gb)} · {disk.pct}%</Mono>{:else}<Mono dim style="font-size:12px;">—</Mono>{/if}
                </div>
              {/if}
              <div class="frow"><Mono dim style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;">Privacy</Mono><Mono dim style="font-size:11.5px;">Everything runs locally. Nothing leaves this machine.</Mono></div>
            </div>
          </section>
        {/if}
      </div>
    </div>
  </div>
</div>

<style>
  .artboard { width: 1400px; height: 900px; background: var(--bg); color: var(--ink); position: relative; overflow: hidden; margin: 0 auto; }
  .scrim { position: absolute; inset: 0; background: rgba(10, 10, 12, 0.55); }

  .modal { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 960px; height: 640px; background: var(--bg); box-shadow: inset 0 0 0 1px var(--invert); display: grid; grid-template-rows: 56px 1fr; }
  .mhead { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--invert); padding: 0 24px; }
  .mtitle { display: flex; align-items: baseline; gap: 16px; }
  .mtitlebig { font-size: 22px; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
  .mclose { font-family: var(--ak-mono); font-size: 11px; letter-spacing: 0.08em; background: transparent; border: none; color: var(--ink-2); cursor: pointer; padding: 0; }
  .mclose:hover { color: var(--ink); }
  .mbody { display: grid; grid-template-columns: 200px 1fr; min-height: 0; overflow: hidden; }
  .mnav { border-right: 1px solid var(--rule); padding: 20px 0; display: flex; flex-direction: column; }
  .navbtn { text-align: left; padding: 7px 24px; background: transparent; border: none; border-left: 2px solid transparent; color: var(--ink-2); font-size: 13px; font-weight: 400; cursor: pointer; font-family: inherit; }
  .navbtn.active { border-left-color: var(--invert); color: var(--ink); font-weight: 600; }
  .form { overflow: auto; padding: 24px 32px; }
  .fshead { margin-bottom: 14px; }
  .fstitle { font-size: 18px; letter-spacing: -0.02em; line-height: 1.1; color: var(--ink); }
  .fsdesc { font-size: 11.5px; color: var(--quiet); margin-top: 3px; max-width: 460px; line-height: 1.5; }
  .frows { display: flex; flex-direction: column; gap: 12px; }
  .frow { display: grid; grid-template-columns: 140px 1fr; align-items: center; gap: 16px; }
  .frow.disabled { opacity: 0.55; }
  .seg { display: flex; border: 1px solid var(--rule); width: fit-content; }
  .segsep { width: 1px; background: var(--rule); }
  .segbtn { font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; padding: 5px 12px; background: transparent; color: var(--ink-2); border: none; cursor: pointer; line-height: 1; font-weight: 400; }
  .segbtn.on { background: var(--invert); color: var(--invert-ink); font-weight: 700; }
  .pend { font-family: var(--ak-mono); font-size: 9.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--quiet-2); border: 1px dashed var(--rule-hi); padding: 2px 7px; width: fit-content; }
  .livedot { color: var(--cyan); font-size: 9px; }
</style>
