<!-- Deterministic SVG waveform with in/out markers + playhead. -->
<script>
  const N = 80
  const bars = Array.from({ length: N }, (_, i) => {
    const v = Math.sin(i * 0.7) * 0.4 + Math.sin(i * 0.13) * 0.35 + Math.sin(i * 0.42 + 1) * 0.3
    return Math.abs(v) * (0.6 + Math.sin(i * 0.18) * 0.4)
  }).map((v) => Math.max(2, v * 50))
</script>

<div class="wf">
  <svg viewBox="0 0 80 56" preserveAspectRatio="none" class="svg">
    {#each bars as h, i}
      <rect x={i + 0.15} y={(56 - h) / 2} width="0.7" height={h} fill="var(--ink-2)" />
    {/each}
  </svg>
  <div class="mark mark-in"><div class="flag"></div></div>
  <div class="mark mark-out"><div class="flag"></div></div>
  <div class="playhead"></div>
</div>

<style>
  .wf { position: relative; height: 56px; }
  .svg { width: 100%; height: 100%; display: block; }
  .mark { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--invert); }
  .mark-in { left: 8%; }
  .mark-out { left: 78%; }
  .flag { position: absolute; top: -4px; left: -3px; width: 7px; height: 4px; background: var(--invert); }
  .playhead { position: absolute; top: -6px; bottom: -6px; left: 32%; width: 1px; background: var(--ink); }
</style>
