<!-- Real audio waveform: backend peaks (0..1) → bars. Click to seek+play; the
     playhead tracks the player; IN/OUT trim markers are DRAGGABLE (grab the flag
     and slide). Flat baseline when a clip has no peaks. -->
<script>
  import { createEventDispatcher } from 'svelte'
  export let peaks = null // number[] 0..1 from /api/media/{id}/waveform, or null
  export let progress = 0 // 0..1 playhead position (player currentTime / duration)
  export let inFrac = null // 0..1 IN trim point, or null
  export let outFrac = null // 0..1 OUT trim point, or null
  const dispatch = createEventDispatcher()
  const _pct = (f) => Math.min(100, Math.max(0, f * 100))
  $: selL = inFrac != null ? _pct(inFrac) : 0
  $: selR = outFrac != null ? _pct(outFrac) : 100
  $: hasSel = inFrac != null || outFrac != null
  const N = 80
  $: bars = (() => {
    const src = Array.isArray(peaks) && peaks.length ? peaks : null
    if (!src) return Array.from({ length: N }, () => 2)
    return Array.from({ length: N }, (_, i) => {
      const v = src[Math.floor((i / N) * src.length)] || 0
      return Math.max(2, v * 50)
    })
  })()

  let wfEl
  let dragging = null // 'in' | 'out'
  let didDrag = false
  const fracAt = (clientX) => {
    const r = wfEl.getBoundingClientRect()
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width))
  }
  function startDrag(which, e) {
    e.stopPropagation()
    dragging = which
    didDrag = false
    wfEl.setPointerCapture?.(e.pointerId) // capture on container → moves land on .wf
  }
  function onMove(e) {
    if (!dragging) return
    didDrag = true
    dispatch('trim', { which: dragging, frac: fracAt(e.clientX) })
  }
  function endDrag() { dragging = null }
  function onClick(e) {
    if (didDrag) { didDrag = false; return } // finished a drag, not a click
    dispatch('seek', fracAt(e.clientX)) // seek + play (handled by the inspector)
  }
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
<div class="wf" bind:this={wfEl} on:click={onClick} on:pointermove={onMove}
  on:pointerup={endDrag} on:pointercancel={endDrag} title="點擊跳播 · 拖曳 IN/OUT 標記調整區間">
  {#if hasSel}
    <div class="sel" style="left:{selL}%;right:{100 - selR}%"></div>
  {/if}
  <svg viewBox="0 0 80 56" preserveAspectRatio="none" class="svg">
    {#each bars as h, i}
      <rect x={i + 0.15} y={(56 - h) / 2} width="0.7" height={h} fill="var(--ink-2)" />
    {/each}
  </svg>
  {#if inFrac != null}
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="mark mk-in handle" style="left:{_pct(inFrac)}%" on:pointerdown={(e) => startDrag('in', e)}></div>
  {/if}
  {#if outFrac != null}
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="mark mk-out handle" style="left:{_pct(outFrac)}%" on:pointerdown={(e) => startDrag('out', e)}></div>
  {/if}
  <div class="playhead" style="left:{_pct(progress)}%"></div>
</div>

<style>
  .wf { position: relative; height: 56px; cursor: pointer; touch-action: none; }
  .svg { width: 100%; height: 100%; display: block; }
  .playhead {
    position: absolute; top: -6px; bottom: -6px; width: 1px;
    background: var(--ink); pointer-events: none;
  }
  .sel { position: absolute; top: 0; bottom: 0; background: var(--ink); opacity: 0.10; pointer-events: none; }
  .mark { position: absolute; top: -4px; bottom: -4px; width: 1px; background: var(--invert); pointer-events: none; }
  /* draggable handles: wider invisible grab zone + a top flag, ew-resize cursor */
  .handle { pointer-events: auto; cursor: ew-resize; }
  .handle::after { content: ''; position: absolute; left: -6px; right: -6px; top: -4px; bottom: -4px; }
  .mark::before {
    content: ''; position: absolute; top: -4px; width: 6px; height: 5px; background: var(--invert);
  }
  .mk-in::before { left: 0; }
  .mk-out::before { right: 0; }
</style>
