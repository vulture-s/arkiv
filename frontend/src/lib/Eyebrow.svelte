<!-- Mono uppercase tiny label. -->
<script>
  export let style = ''
</script>

<div class="ak-mono eyebrow" {style}>
  <slot />
</div>

<style>
  .eyebrow {
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--quiet);
    font-weight: 500;
  }
</style>
