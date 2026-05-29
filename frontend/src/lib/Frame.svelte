<!-- 1px square frame motif. hi = inverted (selected/active). -->
<script>
  export let hi = false
  export let style = ''
</script>

<div class="frame" class:hi {style} {...$$restProps} on:click on:mouseenter on:mouseleave>
  <slot />
</div>

<style>
  .frame {
    box-shadow: inset 0 0 0 1px var(--rule);
  }
  .frame.hi {
    box-shadow: inset 0 0 0 1px var(--invert);
  }
</style>
