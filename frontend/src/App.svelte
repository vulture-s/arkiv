<script>
  import Router from 'svelte-spa-router'
  import Home from './routes/Home.svelte'
  import Gallery from './routes/Gallery.svelte'
  import MainDark from './routes/MainDark.svelte'

  // Routes are added per overnight segment.
  const routes = {
    '/': Home,
    '/gallery': Gallery, // seg 1 — shared-primitive gallery
    '/main': MainDark, // seg 2 — hero (interactive)
    // ...screens land here per segment
  }
</script>

<div class="ak-root" data-theme="dark">
  <Router {routes} />
</div>

<style>
  .ak-root {
    min-height: 100vh;
  }
</style>
