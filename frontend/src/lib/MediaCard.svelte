<!-- Grid card. Forwards click/hover events to parent (selection lives upstream). -->
<script>
  import Thumb from './Thumb.svelte'
  import Rating from './Rating.svelte'
  import Mono from './Mono.svelte'
  import CornerTick from './CornerTick.svelte'
  export let m
  export let theme = 'dark'
  export let selected = false
  export let hover = false
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
<div class="card" class:selected class:hover on:click on:mouseenter on:mouseleave>
  <div class="thumbarea">
    <Thumb seed={m.id} kind={m.kind} {theme} />
    <div class="dur">{m.dur}</div>
    {#if selected}
      <CornerTick pos="tl" /><CornerTick pos="tr" /><CornerTick pos="bl" /><CornerTick pos="br" />
    {/if}
  </div>
  <div class="meta">
    <div class="name" class:ng={m.rating === 'ng'}>{m.name}</div>
    <div class="row">
      <Rating value={m.rating} />
      <Mono dim style="font-size:10px;">{m.size}</Mono>
    </div>
  </div>
</div>

<style>
  .card { background: var(--bg); position: relative; cursor: pointer; transition: box-shadow 0.12s; }
  .card.selected { box-shadow: inset 0 0 0 1px var(--invert); }
  .card.hover:not(.selected) { outline: 1px solid var(--rule-hi); outline-offset: -1px; }
  .thumbarea { position: relative; aspect-ratio: 16 / 9; background: var(--surface-2); overflow: hidden; }
  .dur {
    position: absolute; bottom: 6px; right: 6px;
    font-family: var(--ak-mono); font-size: 10.5px; letter-spacing: 0.02em;
    color: #f3f2ee; background: rgba(10, 10, 12, 0.78); padding: 2px 5px; line-height: 1;
  }
  .meta { padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 4px; }
  .name {
    font-family: var(--ak-mono); font-size: 11.5px; color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .name.ng { text-decoration: line-through; opacity: 0.5; }
  .row { display: flex; justify-content: space-between; align-items: center; }
</style>
