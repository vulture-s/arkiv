<!-- grid / list view toggle. Bindable. -->
<script>
  export let view = 'grid'
</script>

<div class="vt">
  <button class="vbtn" class:active={view === 'grid'} on:click={() => (view = 'grid')}>Grid</button>
  <div class="sep"></div>
  <button class="vbtn" class:active={view === 'list'} on:click={() => (view = 'list')}>List</button>
</div>

<style>
  .vt { display: flex; border: 1px solid var(--rule); }
  .vbtn {
    font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 4px 9px; background: transparent; color: var(--ink-2); border: none; cursor: pointer; line-height: 1;
  }
  .vbtn.active { background: var(--invert); color: var(--invert-ink); }
  .sep { width: 1px; background: var(--rule); }
</style>
