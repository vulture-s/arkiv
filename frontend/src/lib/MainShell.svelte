<!-- Fixed 1400×900 artboard shell: TopBar + (PoolSidebar | center | inspector).
     center = required slot; inspector = optional (defaults to <Inspector>);
     footer = optional (analytics). rows overrides grid-template-rows. -->
<script>
  import TopBar from './TopBar.svelte'
  import PoolSidebar from './PoolSidebar.svelte'
  import Inspector from './Inspector.svelte'
  import { MEDIA } from './mockData.js'
  export let theme = 'dark'
  export let rows = '52px 1fr'
</script>

<div class="artboard" data-theme={theme} style="grid-template-rows:{rows};">
  <TopBar />
  <div class="body">
    <PoolSidebar />
    <slot name="center" />
    {#if $$slots.inspector}
      <slot name="inspector" />
    {:else}
      <Inspector media={MEDIA[0]} {theme} />
    {/if}
  </div>
  <slot name="footer" />
</div>

<style>
  .artboard {
    width: 1400px; height: 900px; position: relative;
    display: grid; background: var(--bg); color: var(--ink); overflow: hidden;
    margin: 0 auto;
  }
  .body { display: grid; grid-template-columns: 220px 1fr 340px; min-height: 0; }
</style>
