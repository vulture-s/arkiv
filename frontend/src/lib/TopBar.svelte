<!-- Top bar: logo · search · all-projects toggle · actions. crossProject is bindable. -->
<script>
  import { onMount, onDestroy } from 'svelte'
  import { push } from 'svelte-spa-router'
  import ArkivLogo from './ArkivLogo.svelte'
  import Mono from './Mono.svelte'
  import { cycleTheme, themePref } from './prefs.js'
  export let crossProject = false

  let query = ''
  let searchEl
  // Enter → ranked search screen; SearchLive reads ?q= on mount.
  function runSearch() {
    const q = query.trim()
    push(q ? `/search-live?q=${encodeURIComponent(q)}` : '/search-live')
  }
  // ⌘K / Ctrl-K focuses the search box (the shortcut the hint advertises).
  function onGlobalKey(e) {
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault()
      searchEl?.focus()
    }
  }
  onMount(() => window.addEventListener('keydown', onGlobalKey))
  onDestroy(() => window.removeEventListener('keydown', onGlobalKey))
</script>

<div class="topbar">
  <div class="logo">
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;letter-spacing:0.05em;">v0.9.2</Mono>
  </div>

  <div class="search">
    <Mono dim style="font-size:11px;">⌕</Mono>
    <input
      class="ak-input searchinput"
      placeholder={'搜尋媒體… "格爾木氧氣"、camera:a7s3、tag:cycling、lang:zh'}
      bind:this={searchEl}
      bind:value={query}
      on:keydown={(e) => e.key === 'Enter' && runSearch()}
    />
    <Mono dim style="font-size:10px;letter-spacing:0.06em;white-space:nowrap;">⌘K</Mono>
    <button class="allproj" class:on={crossProject} on:click={() => (crossProject = !crossProject)}>
      All projects {#if crossProject}·on{/if}
    </button>
  </div>

  <div class="actions">
    <button class="ak-btn ak-btn--primary" on:click={() => push('/ingest-setup')}>+ Ingest</button>
    <button class="ak-btn" on:click={() => push('/chat-live')} title="AI chat · 問你的素材庫">Chat</button>
    <button class="ak-btn" on:click={() => push('/offload')} title="DIT offload · card → backup">DIT</button>
    <div class="vrule"></div>
    <button class="ak-btn" title={`theme · ${$themePref}`} on:click={cycleTheme}>◐</button>
    <button class="ak-btn" title="settings" on:click={() => push('/settings')}>···</button>
  </div>
</div>

<style>
  .topbar {
    /* 3rd col = 340px so the search box's right rule lines up with the body's
       340px inspector column below (was `auto`, which drifted). */
    display: grid; grid-template-columns: 220px 1fr 340px; align-items: center;
    border-bottom: 1px solid var(--rule); padding-right: 16px; background: var(--bg);
  }
  .logo { padding-left: 16px; display: flex; align-items: center; gap: 12px; }
  .search {
    display: flex; align-items: center; gap: 14px;
    border-left: 1px solid var(--rule); border-right: 1px solid var(--rule);
    padding-left: 16px; height: 100%;
  }
  .searchinput { flex: 1; font-size: 13px; font-family: var(--ak-sans); border-bottom: none; }
  .allproj {
    font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase;
    padding: 4px 8px; background: transparent; color: var(--ink-2);
    border: 1px solid var(--rule-hi); cursor: pointer; line-height: 1; white-space: nowrap;
  }
  .allproj.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
  .actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding-left: 16px; }
  .vrule { width: 1px; height: 18px; background: var(--rule); margin: 0 4px; }
</style>
