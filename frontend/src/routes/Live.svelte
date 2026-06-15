<!-- B1 proof — loads real data from the running backend via src/lib/api.js.
     Verifies: Svelte → fetch → Vite proxy → FastAPI → render. Additive (does
     not touch the mock screens). Empty DB → empty payloads (correct). -->
<script>
  import { onMount } from 'svelte'
  import * as api from '../lib/api.js'
  import Eyebrow from '../lib/Eyebrow.svelte'
  import Mono from '../lib/Mono.svelte'
  import { resolvedTheme } from '../lib/prefs.js'

  let state = 'loading' // loading | ok | error
  let err = ''
  let stats, projects, tags, media

  onMount(async () => {
    try {
      ;[stats, projects, tags, media] = await Promise.all([
        api.getStats(),
        api.getProjects(),
        api.getTags(),
        api.getMedia({ limit: 4 }),
      ])
      state = 'ok'
    } catch (e) {
      state = 'error'
      err = e.message + (e.body ? ' · ' + JSON.stringify(e.body) : '')
    }
  })
</script>

<div class="live ak-root" data-theme={$resolvedTheme}>
  <Eyebrow>B1 · live API proof — GET against running backend</Eyebrow>
  <h1 class="ak-display title">api.</h1>

  {#if state === 'loading'}
    <Mono dim>loading…</Mono>
  {:else if state === 'error'}
    <Mono style="color:var(--cyan);">ERROR: {err}</Mono>
  {:else}
    <div class="grid">
      <div class="cell">
        <Eyebrow>/api/stats</Eyebrow>
        <Mono>total {stats.total} · transcripts {stats.with_transcript} · GOOD {stats.rating.good} / N·G {stats.rating.ng} / REV {stats.rating.review}</Mono>
      </div>
      <div class="cell">
        <Eyebrow>/api/projects</Eyebrow>
        <Mono>{projects.total} projects{#each projects.projects as p} · {p.name}{/each}</Mono>
      </div>
      <div class="cell">
        <Eyebrow>/api/tags</Eyebrow>
        <Mono>{tags.length} tags</Mono>
      </div>
      <div class="cell">
        <Eyebrow>/api/media</Eyebrow>
        <Mono>{media.total} items (search={String(media.search)})</Mono>
      </div>
    </div>
    <Mono dim style="display:block;margin-top:var(--s-6);line-height:1.6;">
      ✅ end-to-end OK: Svelte → fetch → Vite proxy → FastAPI → render.<br />
      Empty DB = empty payloads (correct). Real data appears after ingest.
    </Mono>
  {/if}
</div>

<style>
  .live { padding: var(--s-12); min-height: 100vh; display: flex; flex-direction: column; gap: var(--s-3); }
  .title { font-size: var(--fs-display-sm); margin: var(--s-2) 0 var(--s-6); color: var(--ink); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-6); max-width: 900px; }
  .cell { display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-4); box-shadow: inset 0 0 0 1px var(--rule); }
</style>
