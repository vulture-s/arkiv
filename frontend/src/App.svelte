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
  import IngestSetup from './routes/IngestSetup.svelte'
  import ChatLive from './routes/ChatLive.svelte'
  import SearchLive from './routes/SearchLive.svelte'
  import Offload from './routes/Offload.svelte'

  // Routes are added per overnight segment.
  const routes = {
    '/': MainLive, // g1 — default landing = live product (was Home token-proof scaffold)
    '/home': Home, // design scaffold kept for reference (was '/')
    '/live': Live, // B1 — live API proof (reads running backend)
    '/main-live': MainLive, // B1 — main grid wired to live data
    '/ingest-setup': IngestSetup, // S1b — ingest setup dialog (redesign op-01), wired to scan manifest + ingest options
    '/ingest-live': IngestLive, // B1+ — ingest progress wired to live ws
    '/offload': Offload, // S4 — DIT offload (card → backup), ported from the /dit island into the SPA
    '/chat-live': ChatLive, // E2 — chat wired to live /api/chat
    '/search-live': SearchLive, // search wired to live /api/media?q=
    '/gallery': Gallery, // seg 1 — shared-primitive gallery
    '/main': MainDark, // seg 2 — hero (interactive)
    '/states': MainStates, // seg 3 — state variants
    '/inspector': InspectorFull, // seg 4 — inspector full
    '/search': Search, // seg 5 — cross-project search
    '/ingest': Ingest, // seg 6 — ingest progress
    '/settings': Settings, // seg 7 — settings modal
    '/flows': Flows, // seg 8 — round-3 flows
    '/edge': Edge, // seg 9 — round-4 edge
    // 360 (.insv/.360): ingest reproject shipped (Phase 8.3b) — no dedicated SPA viewer route yet
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
