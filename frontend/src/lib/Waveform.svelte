<!-- Real audio waveform: backend peaks (0..1) → bars, with a live playhead that
     tracks the inspector player + click-to-seek. Falls back to a flat baseline
     when a clip has no peaks (no audio / not yet computed). -->
<script>
  import { createEventDispatcher } from 'svelte'
  export let peaks = null // number[] 0..1 from /api/media/{id}/waveform, or null
  export let progress = 0 // 0..1 playhead position (player currentTime / duration)
  export let inFrac = null // 0..1 IN trim point, or null
  export let outFrac = null // 0..1 OUT trim point, or null
  const dispatch = createEventDispatcher()
  const _pct = (f) => Math.min(100, Math.max(0, f * 100))
  // shaded selection between IN and OUT (open-ended if only one set)
  $: selL = inFrac != null ? _pct(inFrac) : 0
  $: selR = outFrac != null ? _pct(outFrac) : 100
  $: hasSel = inFrac != null || outFrac != null
  const N = 80
  // Resample the incoming peaks to N bars and scale to the 56-tall viewBox.
  $: bars = (() => {
    const src = Array.isArray(peaks) && peaks.length ? peaks : null
    if (!src) return Array.from({ length: N }, () => 2) // flat baseline, no data
    return Array.from({ length: N }, (_, i) => {
      const v = src[Math.floor((i / N) * src.length)] || 0
      return Math.max(2, v * 50)
    })
  })()
  function onClick(e) {
    const r = e.currentTarget.getBoundingClientRect()
    dispatch('seek', Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)))
  }
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
<div class="wf" on:click={onClick} title="點擊跳到該位置">
  {#if hasSel}
    <div class="sel" style="left:{selL}%;right:{100 - selR}%"></div>
  {/if}
  <svg viewBox="0 0 80 56" preserveAspectRatio="none" class="svg">
    {#each bars as h, i}
      <rect x={i + 0.15} y={(56 - h) / 2} width="0.7" height={h} fill="var(--ink-2)" />
    {/each}
  </svg>
  {#if inFrac != null}<div class="mark mk-in" style="left:{_pct(inFrac)}%"></div>{/if}
  {#if outFrac != null}<div class="mark mk-out" style="left:{_pct(outFrac)}%"></div>{/if}
  <div class="playhead" style="left:{_pct(progress)}%"></div>
</div>

<style>
  .wf { position: relative; height: 56px; cursor: pointer; }
  .svg { width: 100%; height: 100%; display: block; }
  .playhead {
    position: absolute; top: -6px; bottom: -6px; width: 1px;
    background: var(--ink); pointer-events: none;
  }
  /* IN/OUT trim: shaded selection band + edge markers with a top flag */
  .sel { position: absolute; top: 0; bottom: 0; background: var(--ink); opacity: 0.10; pointer-events: none; }
  .mark { position: absolute; top: -4px; bottom: -4px; width: 1px; background: var(--invert); pointer-events: none; }
  .mark::before {
    content: ''; position: absolute; top: -4px; width: 5px; height: 4px; background: var(--invert);
  }
  .mk-in::before { left: 0; }
  .mk-out::before { right: 0; }
</style>
