<!-- Inline monospace text. dim = quiet color. -->
<script>
  export let dim = false
  export let style = ''
</script>

<span class="ak-mono" class:dim {style}><slot /></span>

<style>
  .dim {
    color: var(--quiet);
  }
</style>
