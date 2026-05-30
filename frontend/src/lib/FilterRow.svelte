<!-- kind filter (all/video/audio) + rating filter (good/rev/ng, toggle). Bindable. -->
<script>
  export let activeFilter = 'all'
  export let activeRating = null
  const filters = [['all', 'All'], ['video', 'Video'], ['audio', 'Audio']]
  const ratings = [['good', 'Good'], ['rev', 'Review'], ['ng', 'N·G']]
</script>

<div class="filters">
  {#each filters as [key, label]}
    <button class="fbtn" class:active={activeFilter === key} on:click={() => (activeFilter = key)}>{label}</button>
  {/each}
  <div class="vrule"></div>
  {#each ratings as [key, label]}
    <button
      class="fbtn"
      class:active={activeRating === key}
      on:click={() => (activeRating = activeRating === key ? null : key)}
    >{label}</button>
  {/each}
</div>

<style>
  .filters { display: flex; gap: 4px; }
  .fbtn {
    font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 4px 9px; background: transparent; color: var(--ink-2);
    border: 1px solid var(--rule); cursor: pointer; line-height: 1; white-space: nowrap;
  }
  .fbtn.active { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
  .vrule { width: 1px; background: var(--rule); margin: 0 4px; }
</style>
