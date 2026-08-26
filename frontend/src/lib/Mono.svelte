<!-- Inline monospace text. dim = quiet color. -->
<script>
  export let dim = false
  export let style = ''
  // Forwarded so a caller can explain a compact readout without wrapping this in
  // another element. Svelte drops attributes a component does not declare, and it
  // drops them silently — `title=` on <Mono> looked right in the source and simply
  // never reached the DOM.
  export let title = undefined
</script>

<span class="ak-mono" class:dim {style} {title}><slot /></span>

<style>
  .dim {
    color: var(--quiet);
  }
</style>
