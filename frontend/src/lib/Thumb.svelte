<!-- Abstract placeholder composition (no real images). `seed` drives a
     deterministic variant so the grid has visual rhythm. Brutalist:
     blocks / stripes / color fields. mix-blend-mode overlay is canonical. -->
<script>
  export let seed = 0
  export let kind = 'video'
  export let state = undefined // API parity (not visualized here)
  export let theme = 'dark'

  const palettesDark = [
    ['#2a2a2e', '#1a1a1d', '#3a3a3f'],
    ['#222226', '#0f0f12', '#4a4a4e'],
  ]
  const palettesLight = [
    ['#d2d0c8', '#e8e6df', '#b8b6ad'],
    ['#c8c6bd', '#ddd9cf', '#a8a59c'],
  ]

  $: v = (((seed % 12) + 12) % 12)
  $: palettes = theme === 'dark' ? palettesDark : palettesLight
  $: p = palettes[v % 2]
  $: layout = v % 6
  $: barColor = theme === 'dark' ? '#f3f2ee' : '#0a0a0c'
  const stripes = [0, 1, 2, 3, 4, 5, 6]
  $: audioBars = Array.from({ length: 22 }, (_, i) => 6 + Math.abs(Math.sin(i * 1.3 + seed) * 32))
</script>

<div class="thumb" style="background: {p[1]};">
  {#if layout === 0}
    <div class="fill" style="background:{p[1]};"></div>
    <div class="pos" style="left:0;right:0;top:38%;height:24%;background:{p[0]};"></div>
    <div class="pos" style="left:0;right:0;top:62%;height:38%;background:{p[2]};"></div>
  {:else if layout === 1}
    <div class="fill" style="background:{p[0]};"></div>
    <div class="pos" style="left:32%;top:14%;width:36%;height:70%;background:{p[1]};"></div>
    <div class="pos" style="left:38%;top:18%;width:24%;height:28%;background:{p[2]};border-radius:50%;"></div>
  {:else if layout === 2}
    <div class="fill" style="background:{p[1]};"></div>
    {#each stripes as i}
      <div class="pos" style="left:0;right:0;top:{10 + i * 12}%;height:3%;background:{i % 2 ? p[2] : p[0]};"></div>
    {/each}
  {:else if layout === 3}
    <div class="fill" style="background:{p[2]};"></div>
    <div class="pos" style="right:0;top:0;width:60%;height:60%;background:{p[0]};"></div>
    <div class="pos" style="left:0;bottom:0;width:70%;height:40%;background:{p[1]};"></div>
  {:else if layout === 4}
    <div class="fill" style="background:{p[1]};"></div>
    <div class="pos" style="left:15%;top:8%;width:34%;height:84%;background:{p[2]};"></div>
    <div class="pos" style="left:54%;top:20%;width:30%;height:60%;background:{p[0]};"></div>
  {:else}
    <div class="fill" style="background:{p[0]};"></div>
    <div class="fill" style="background:{p[2]};clip-path:polygon(0 100%, 100% 0, 100% 100%);"></div>
  {/if}

  <div class="grain"></div>

  {#if kind === 'audio'}
    <div class="audio">
      {#each audioBars as h}
        <div class="bar" style="height:{h}px;background:{barColor};"></div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .thumb {
    position: relative;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }
  .fill {
    position: absolute;
    inset: 0;
  }
  .pos {
    position: absolute;
  }
  .grain {
    position: absolute;
    inset: 0;
    background-image: radial-gradient(rgba(255, 255, 255, 0.015) 1px, transparent 1px);
    background-size: 3px 3px;
    mix-blend-mode: overlay;
    pointer-events: none;
  }
  .audio {
    position: absolute;
    left: 0;
    right: 0;
    top: 50%;
    transform: translateY(-50%);
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 2px;
  }
  .bar {
    width: 2px;
    opacity: 0.7;
  }
</style>
