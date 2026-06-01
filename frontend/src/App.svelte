<script>
  import Router from 'svelte-spa-router'
  import Home from './routes/Home.svelte'
  import Gallery from './routes/Gallery.svelte'
  import MainDark from './routes/MainDark.svelte'
  import MainStates from './routes/MainStates.svelte'
  import InspectorFull from './routes/InspectorFull.svelte'
  import Search from './routes/Search.svelte'
  import Ingest from './routes/Ingest.svelte'
  import Settings from './routes/Settings.svelte'
  import Flows from './routes/Flows.svelte'
  import Edge from './routes/Edge.svelte'
  import Live from './routes/Live.svelte'
  import MainLive from './routes/MainLive.svelte'
  import IngestLive from './routes/IngestLive.svelte'
  import ChatLive from './routes/ChatLive.svelte'

  // Routes are added per overnight segment.
  const routes = {
    '/': Home,
    '/live': Live, // B1 — live API proof (reads running backend)
    '/main-live': MainLive, // B1 — main grid wired to live data
    '/ingest-live': IngestLive, // B1+ — ingest progress wired to live ws
    '/chat-live': ChatLive, // E2 — chat wired to live /api/chat
    '/gallery': Gallery, // seg 1 — shared-primitive gallery
    '/main': MainDark, // seg 2 — hero (interactive)
    '/states': MainStates, // seg 3 — state variants
    '/inspector': InspectorFull, // seg 4 — inspector full
    '/search': Search, // seg 5 — cross-project search
    '/ingest': Ingest, // seg 6 — ingest progress
    '/settings': Settings, // seg 7 — settings modal
    '/flows': Flows, // seg 8 — round-3 flows
    '/edge': Edge, // seg 9 — round-4 edge
    // (insta360 — excluded, gated on 8.3a POC)
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
