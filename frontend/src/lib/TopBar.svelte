<!-- Top bar: logo · search · all-projects toggle · actions. crossProject is bindable. -->
<script>
  import { push } from 'svelte-spa-router'
  import ArkivLogo from './ArkivLogo.svelte'
  import Mono from './Mono.svelte'
  export let crossProject = false
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
    />
    <button class="allproj" class:on={crossProject} on:click={() => (crossProject = !crossProject)}>
      All projects {#if crossProject}·on{/if}
    </button>
    <Mono dim style="font-size:10px;letter-spacing:0.06em;white-space:nowrap;">⌘K</Mono>
  </div>

  <div class="actions">
    <button class="ak-btn ak-btn--primary" on:click={() => push('/ingest-setup')}>+ Ingest</button>
    <button class="ak-btn" on:click={() => push('/offload')} title="DIT offload · card → backup">DIT</button>
    <button class="ak-btn">EDL</button>
    <button class="ak-btn">FCPXML</button>
    <div class="vrule"></div>
    <button class="ak-btn" title="theme">◐</button>
    <button class="ak-btn" title="settings">···</button>
  </div>
</div>

<style>
  .topbar {
    display: grid; grid-template-columns: 220px 1fr auto; align-items: center;
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
  .actions { display: flex; align-items: center; gap: 8px; padding-left: 16px; }
  .vrule { width: 1px; height: 18px; background: var(--rule); margin: 0 4px; }
</style>
