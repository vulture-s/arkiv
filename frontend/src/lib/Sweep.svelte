<!-- CSS-only skeleton sweep — 1px cyan vertical line translating (no gradient). -->
<script>
  export let delay = 0
</script>

<div class="sweep" style="animation-delay:{delay}s;"></div>

<style>
  .sweep {
    position: absolute; top: 0; bottom: 0; left: 0; width: 1px;
    background: var(--cyan); opacity: 0.6;
    animation: ak-sweep 1.4s infinite linear;
  }
</style>
