<script>
  import Router from 'svelte-spa-router'
  import Home from './routes/Home.svelte'
  import Gallery from './routes/Gallery.svelte'
  import MainDark from './routes/MainDark.svelte'
  import MainStates from './routes/MainStates.svelte'
  import InspectorFull from './routes/InspectorFull.svelte'
  import Search from './routes/Search.svelte'

  // Routes are added per overnight segment.
  const routes = {
    '/': Home,
    '/gallery': Gallery, // seg 1 — shared-primitive gallery
    '/main': MainDark, // seg 2 — hero (interactive)
    '/states': MainStates, // seg 3 — state variants
    '/inspector': InspectorFull, // seg 4 — inspector full
    '/search': Search, // seg 5 — cross-project search
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
