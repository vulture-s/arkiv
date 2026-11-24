<!-- Top bar for every screen except the library (which has TopBar: search +
     panels). One layout everywhere:
       [logo → library] [page name] ……… [page actions (slot)] [← 回到素材庫]
     Before, each screen drew its own: some had two nav buttons, some an ESC
     label, Offload and IngestSetup had no way back at all, and three showed the
     version string where the page name belongs. -->
<script>
  import { push } from 'svelte-spa-router'
  import ArkivLogo from './ArkivLogo.svelte'
  import Mono from './Mono.svelte'
  import { t } from './i18n.js'
  /** Page name, shown next to the logo. */
  export let title = ''
  /** What the logo (and the back link) do. null → go to the library.
   *  Offload passes its own: leaving mid-copy must ask first. */
  export let onHome = null
  /** false → the logo is not a link (first-run setup has nowhere to go back to). */
  export let home = true
  /** Show the trailing ← 回到素材庫 link. Off where the page already has an
   *  ESC control that does the same (Chat, the ingest / offload dialogs). */
  export let back = true

  function goHome() {
    if (onHome) onHome()
    else push('/main-live')
  }
</script>

<div class="pagebar">
  {#if home}
    <button class="homebtn" title={$t('回到素材庫')} aria-label={$t('回到素材庫')} on:click={goHome}>
      <ArkivLogo size={16} />
    </button>
  {:else}
    <ArkivLogo size={16} />
  {/if}
  {#if title}<Mono dim style="font-size:11px;letter-spacing:0.04em;white-space:nowrap;">{title}</Mono>{/if}
  <div class="grow"></div>
  <slot />
  {#if back}
    <button class="ak-btn" on:click={goHome}>{$t('← 回到素材庫')}</button>
  {/if}
</div>

<style>
  .pagebar {
    display: flex; align-items: center; gap: 16px; padding: 0 16px;
    height: 52px; box-sizing: border-box; flex: 0 0 52px; min-width: 0;
    border-bottom: 1px solid var(--rule); background: var(--bg);
  }
  .grow { flex: 1; min-width: 0; }
  .homebtn { background: none; border: none; padding: 0; margin: 0; cursor: pointer; display: inline-flex; color: inherit; }
  .homebtn:hover :global(.logo) { box-shadow: inset 0 0 0 1px var(--ink), 0 0 0 1px var(--ink); }
  /* page actions are links or buttons — keep them on one line */
  .pagebar :global(.ak-btn) { white-space: nowrap; }
</style>
