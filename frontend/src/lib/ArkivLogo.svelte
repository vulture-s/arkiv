<!-- 1px frame around 'arkiv.' wordmark (vulture.s 'tv.' style). -->
<script>
  export let size = 18
  export let theme = 'dark' // API parity; styling is theme-agnostic (CSS vars)
</script>

<div
  class="logo"
  style="padding: {size * 0.18}px {size * 0.42}px; font-size: {size}px;"
>
  arkiv<span class="dot" style="font-size: {size * 0.55}px;">.</span>
</div>

<style>
  .logo {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-shadow: inset 0 0 0 1px var(--ink);
    font-family: var(--ak-display);
    line-height: 1;
    letter-spacing: -0.04em;
    color: var(--ink);
  }
  .dot {
    font-family: var(--ak-mono);
    font-weight: 400;
    margin-left: 4px;
    letter-spacing: 0;
    transform: translateY(-1px);
  }
</style>
