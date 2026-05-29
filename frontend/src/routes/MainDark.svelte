<!-- Seg 2 — Screen 1 hero (interactive). 1400×900 artboard.
     Three-column shell: PoolSidebar (220) | grid (flex) | Inspector (340). -->
<script>
  import TopBar from '../lib/TopBar.svelte'
  import PoolSidebar from '../lib/PoolSidebar.svelte'
  import MediaCard from '../lib/MediaCard.svelte'
  import FilterRow from '../lib/FilterRow.svelte'
  import ViewToggle from '../lib/ViewToggle.svelte'
  import Inspector from '../lib/Inspector.svelte'
  import Mono from '../lib/Mono.svelte'
  import { MEDIA } from '../lib/mockData.js'

  export let theme = 'dark'
  let selectedId = 1
  let hoverId = null
  let activeFilter = 'all'
  let activeRating = null
  let crossProject = false
  let view = 'grid'

  $: selected = MEDIA.find((m) => m.id === selectedId) || MEDIA[0]
  $: visibleMedia = MEDIA.filter((m) => {
    if (activeFilter === 'video' && m.kind !== 'video') return false
    if (activeFilter === 'audio' && m.kind !== 'audio') return false
    if (activeRating && m.rating !== activeRating) return false
    return true
  })
</script>

<div class="artboard" data-theme={theme}>
  <TopBar bind:crossProject />

  <div class="body">
    <PoolSidebar />

    <main class="center">
      <div class="toolrow">
        <div class="proj">
          <div class="ak-display projtitle">Bicycle Diaries</div>
          <Mono dim style="font-size:11px;margin-top:5px;letter-spacing:0.04em;">
            247 items · 11h 42m · last ingest 2026-05-24 23:08
          </Mono>
        </div>
        <FilterRow bind:activeFilter bind:activeRating />
        <ViewToggle bind:view />
      </div>

      <div class="gridwrap">
        <div class="mediagrid">
          {#each visibleMedia.slice(0, 12) as m (m.id)}
            <MediaCard
              {m}
              {theme}
              selected={m.id === selectedId}
              hover={m.id === hoverId}
              on:click={() => (selectedId = m.id)}
              on:mouseenter={() => (hoverId = m.id)}
              on:mouseleave={() => (hoverId = null)}
            />
          {/each}
        </div>
      </div>
    </main>

    <Inspector media={selected} {theme} />
  </div>
</div>

<style>
  .artboard {
    width: 1400px; height: 900px; position: relative;
    display: grid; grid-template-rows: 52px 1fr;
    background: var(--bg); color: var(--ink); overflow: hidden;
    margin: 0 auto;
  }
  .body { display: grid; grid-template-columns: 220px 1fr 340px; min-height: 0; }
  .center { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .toolrow {
    display: flex; align-items: center; gap: 14px;
    padding: 14px 22px; border-bottom: 1px solid var(--rule);
  }
  .proj { flex: 1; min-width: 0; }
  .projtitle { font-size: 28px; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
  .gridwrap { flex: 1; overflow: hidden; position: relative; }
  .mediagrid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 1px; padding: 22px; background: var(--rule);
  }
</style>
