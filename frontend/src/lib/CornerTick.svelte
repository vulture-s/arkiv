<!-- L-shaped tick at a corner of a selected card. -->
<script>
  export let pos // 'tl' | 'tr' | 'bl' | 'br'
</script>

<div class="tick {pos}"></div>

<style>
  .tick { position: absolute; width: 14px; height: 14px; z-index: 2; }
  .tl { top: -1px; left: -1px; border-top: 2px solid var(--invert); border-left: 2px solid var(--invert); }
  .tr { top: -1px; right: -1px; border-top: 2px solid var(--invert); border-right: 2px solid var(--invert); }
  .bl { bottom: -1px; left: -1px; border-bottom: 2px solid var(--invert); border-left: 2px solid var(--invert); }
  .br { bottom: -1px; right: -1px; border-bottom: 2px solid var(--invert); border-right: 2px solid var(--invert); }
</style>
