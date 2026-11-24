<!-- Reusable confirm modal. Open via `open`; resolves via on:confirm / on:cancel.
     Backdrop click cancels (unless busy). danger=true paints the confirm button red. -->
<script>
  import { createEventDispatcher } from 'svelte'
  import { t } from './i18n.js'
  export let open = false
  export let title = null // null → localized 確認
  export let message = ''
  export let confirmLabel = null // null → localized 確認
  export let cancelLabel = null // null → localized 取消
  export let busy = false
  export let danger = false
  const dispatch = createEventDispatcher()
  // Esc is handled on the window: the dialog's own keydown stops propagation, so
  // a backdrop listener never saw it once focus was inside. Focus moves to Cancel
  // on open (the safe default for a destructive confirm).
  function onWinKey(e) {
    if (open && !busy && e.key === 'Escape') { e.preventDefault(); dispatch('cancel') }
  }
  const autofocus = (node) => { node.focus() }
  // Focus is inside the dialog (Cancel is autofocused), and keys typed here must
  // not leak to the page's shortcuts — so this handler stops propagation, which
  // also means the window listener above never sees Esc. Handle it here too.
  function onModalKey(e) {
    if (e.key === 'Escape' && open && !busy) { e.preventDefault(); dispatch('cancel') }
    e.stopPropagation()
  }
</script>

<svelte:window on:keydown={onWinKey} />

{#if open}
  <div
    class="backdrop"
    role="button"
    tabindex="-1"
    on:click={() => !busy && dispatch('cancel')}
    on:keydown={(e) => {
      if (!busy && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault()
        dispatch('cancel')
      }
    }}
  >
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      tabindex="-1"
      on:click|stopPropagation
      on:keydown={onModalKey}
    >
      <div class="title" id="confirm-title">{title ?? $t('確認')}</div>
      {#if message}<div class="msg">{message}</div>{/if}
      <div class="actions">
        <button class="ak-btn" use:autofocus on:click={() => dispatch('cancel')} disabled={busy}>{cancelLabel ?? $t('取消')}</button>
        <button
          class="ak-btn {danger ? 'danger' : 'primary'}"
          on:click={() => dispatch('confirm')}
          disabled={busy}
        >{busy ? $t('處理中…') : (confirmLabel ?? $t('確認'))}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
    display: flex; align-items: center; justify-content: center; z-index: 1000;
  }
  .modal {
    background: var(--bg); color: var(--ink); width: 420px; max-width: 90vw;
    padding: 20px; box-shadow: inset 0 0 0 1px var(--invert);
    display: flex; flex-direction: column; gap: 14px;
  }
  .title { font-family: var(--ak-mono); font-size: 14px; font-weight: 600; }
  .msg { font-size: 12.5px; line-height: 1.5; color: var(--ink-2); white-space: pre-line; }
  .actions { display: flex; justify-content: flex-end; gap: 8px; }
  .danger { background: #b3261e; color: #fff; border-color: #b3261e; }
  .primary { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
</style>
