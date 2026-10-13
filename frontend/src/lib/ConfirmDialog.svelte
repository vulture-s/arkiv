<!-- Reusable confirm modal. Open via `open`; resolves via on:confirm / on:cancel.
     Backdrop click cancels (unless busy). danger=true paints the confirm button red. -->
<script>
  import { createEventDispatcher } from 'svelte'
  export let open = false
  export let title = '確認'
  export let message = ''
  export let confirmLabel = '確認'
  export let cancelLabel = '取消'
  export let busy = false
  export let danger = false
  const dispatch = createEventDispatcher()
</script>

{#if open}
  <div
    class="backdrop"
    role="button"
    tabindex="-1"
    on:click={() => !busy && dispatch('cancel')}
    on:keydown={(e) => {
      if (!busy && (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape')) {
        e.preventDefault()
        dispatch('cancel')
      }
    }}
  >
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      on:click|stopPropagation
      on:keydown={(e) => e.stopPropagation()}
    >
      <div class="title">{title}</div>
      {#if message}<div class="msg">{message}</div>{/if}
      <div class="actions">
        <button class="ak-btn" on:click={() => dispatch('cancel')} disabled={busy}>{cancelLabel}</button>
        <button
          class="ak-btn {danger ? 'danger' : 'primary'}"
          on:click={() => dispatch('confirm')}
          disabled={busy}
        >{busy ? '處理中…' : confirmLabel}</button>
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
