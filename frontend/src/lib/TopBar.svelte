<!-- Top bar: logo · search · all-projects toggle · actions. crossProject is bindable. -->
<script>
  import { onMount, onDestroy } from 'svelte'
  import { VERSION } from './version.js'
  import { push } from 'svelte-spa-router'
  import ArkivLogo from './ArkivLogo.svelte'
  import Mono from './Mono.svelte'
  import { cycleTheme, themePref, shell, togglePool, toggleInspector } from './prefs.js'
  import { t } from './i18n.js'
  export let crossProject = false
  /** The search text. Bindable so the main screen shares one box with the top bar. */
  export let query = ''
  /** In-library search handler. When set (main screen) and the scope is 此庫,
   *  Enter filters the grid in place; otherwise Enter opens the ranked screen. */
  export let onSearch = null
  /** Show the pool / inspector toggles — only screens that have those panels. */
  export let panels = false

  let searchEl
  // Enter → ranked search screen; SearchLive reads ?q= (+ ?all=1) on mount, so the
  // "All projects" toggle here seeds federation mode over there.
  function runSearch() {
    const q = query.trim()
    if (onSearch && !crossProject) { onSearch(q); return }
    const p = new URLSearchParams()
    if (q) p.set('q', q)
    if (crossProject) p.set('all', '1')
    const qs = p.toString()
    push(qs ? `/search-live?${qs}` : '/search-live')
  }
  // ⌘K / Ctrl-K focuses the search box (the shortcut the hint advertises).
  function clearSearch() {
    query = ''
    if (onSearch && !crossProject) onSearch('')
    searchEl?.focus()
  }
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
    {#if panels}
      <button class="ptoggle" class:on={$shell.poolMode !== 'hidden'} aria-pressed={$shell.poolMode !== 'hidden'}
        title={$t('側欄')} aria-label={$t('側欄')} on:click={togglePool}>◧</button>
    {/if}
    <ArkivLogo size={16} />
    <Mono dim style="font-size:10px;letter-spacing:0.05em;">{VERSION}</Mono>
  </div>

  <div class="search">
    <Mono dim style="font-size:11px;">⌕</Mono>
    <input
      class="ak-input searchinput"
      placeholder={$t('搜尋媒體… "格爾木氧氣"、camera:a7s3、tag:cycling、who:小明、lang:zh')}
      bind:this={searchEl}
      bind:value={query}
      on:keydown={(e) => e.key === 'Enter' && runSearch()}
    />
    {#if query}
      <button class="sclear" title={$t('清除搜尋')} aria-label={$t('清除搜尋')} on:click={clearSearch}>✕</button>
    {:else}
      <Mono dim style="font-size:10px;letter-spacing:0.06em;white-space:nowrap;">⌘K</Mono>
    {/if}
    <!-- Scope: 此庫 searches the open library (in the grid, on the main screen);
         全部專案 is the cross-project ranked search. Replaces the lone
         "All projects" toggle, whose off state didn't say what it was instead. -->
    <div class="scope" role="group" aria-label={$t('搜尋範圍')}>
      <button class="allproj" class:on={!crossProject} aria-pressed={!crossProject} on:click={() => (crossProject = false)}>{$t('此庫')}</button>
      <button class="allproj" class:on={crossProject} aria-pressed={crossProject} on:click={() => (crossProject = true)}>{$t('全部專案')}</button>
    </div>
  </div>

  <div class="actions">
    <button class="ak-btn ak-btn--primary" on:click={() => push('/ingest-setup')}>+ Ingest</button>
    <button class="ak-btn" on:click={() => push('/chat-live')} title={$t('AI chat · 問你的素材庫')}>Chat</button>
    <button class="ak-btn" on:click={() => push('/offload')} title="DIT offload · card → backup">DIT</button>
    <div class="vrule"></div>
    {#if panels}
      <button class="ak-btn ptoggle-r" class:on={$shell.inspOpen} aria-pressed={$shell.inspOpen}
        title={$t('檢視面板')} aria-label={$t('檢視面板')} on:click={toggleInspector}>◨</button>
    {/if}
    <button class="ak-btn" title={`theme · ${$themePref}`} on:click={cycleTheme}>◐</button>
    <button class="ak-btn" title="settings" on:click={() => push('/settings')}>···</button>
  </div>
</div>

<style>
  .topbar {
    /* 3rd col = --inspector-w so the search box's right rule lines up with the
       body's inspector column below (was `auto`, which drifted). No padding on the
       bar itself: a right padding here shrinks the 1fr column and pushes that rule
       17px left of the inspector edge — the actions column carries its own. */
    /* minmax(…, max-content): when a panel is hidden its column is 0, but the
       logo / action buttons still need their own width. */
    display: grid; grid-template-columns: minmax(var(--pool-w), max-content) 1fr minmax(var(--inspector-w), max-content);
    align-items: center;
    border-bottom: 1px solid var(--rule); background: var(--bg);
  }
  /* The two rules belong to the OUTER cells, not the search box: the body below
     draws them as the pool's right border and the inspector's left border, so a
     rule drawn by .search sat 1px on the other side of the same boundary and the
     lines never met. Same side as the body = one continuous line. */
  .logo {
    padding-left: 16px; display: flex; align-items: center; gap: 12px;
    height: 100%; border-right: 1px solid var(--rule);
  }
  .search {
    display: flex; align-items: center; gap: 14px;
    padding: 0 16px; height: 100%;
  }
  .searchinput { flex: 1; font-size: 13px; font-family: var(--ak-sans); border-bottom: none; }
  .allproj {
    font-family: var(--ak-mono); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase;
    padding: 4px 8px; background: transparent; color: var(--ink-2);
    border: 1px solid var(--rule-hi); cursor: pointer; line-height: 1; white-space: nowrap;
  }
  .scope { display: flex; }
  .scope .allproj + .allproj { border-left: none; }
  .sclear {
    font-family: var(--ak-mono); font-size: 11px; line-height: 1; padding: 3px 5px;
    background: transparent; color: var(--quiet); border: 1px solid transparent; cursor: pointer;
  }
  .sclear:hover { color: var(--ink); border-color: var(--rule-hi); }
  .allproj.on { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
  .actions {
    display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 0 16px;
    height: 100%; border-left: 1px solid var(--rule);
  }
  .ptoggle {
    font-family: var(--ak-mono); font-size: 13px; line-height: 1; padding: 3px 5px;
    background: transparent; color: var(--quiet); border: 1px solid transparent; cursor: pointer;
  }
  .ptoggle:hover, .ptoggle.on { color: var(--ink); }
  .ptoggle:hover { border-color: var(--rule-hi); }
  .ptoggle-r { color: var(--quiet); }
  .ptoggle-r.on { color: var(--ink); }
  .vrule { width: 1px; height: 18px; background: var(--rule); margin: 0 4px; }
</style>
