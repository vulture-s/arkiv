<!-- Real audio waveform: backend peaks (0..1) → bars, with a live playhead that
     tracks the inspector player + click-to-seek. Falls back to a flat baseline
     when a clip has no peaks (no audio / not yet computed). -->
<script>
  import { createEventDispatcher } from 'svelte'
  export let peaks = null // number[] 0..1 from /api/media/{id}/waveform, or null
  export let progress = 0 // 0..1 playhead position (player currentTime / duration)
  const dispatch = createEventDispatcher()
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
  <svg viewBox="0 0 80 56" preserveAspectRatio="none" class="svg">
    {#each bars as h, i}
      <rect x={i + 0.15} y={(56 - h) / 2} width="0.7" height={h} fill="var(--ink-2)" />
    {/each}
  </svg>
  <div class="playhead" style="left:{Math.min(100, Math.max(0, progress * 100))}%"></div>
</div>

<style>
  .wf { position: relative; height: 56px; cursor: pointer; }
  .svg { width: 100%; height: 100%; display: block; }
  .playhead {
    position: absolute; top: -6px; bottom: -6px; width: 1px;
    background: var(--ink); pointer-events: none;
  }
</style>
