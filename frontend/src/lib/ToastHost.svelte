<!-- B11 — global toast host. Bottom-right stack, on-brand (B&W, mono label,
     1px frame; success/error told apart by frame weight + label, not colour,
     per the brutalist design discipline). Mounted once in App.svelte. -->
<script>
  import { toasts, dismissToast } from './toast.js'
</script>

<div class="ak-toast-host" aria-live="polite">
  {#each $toasts as t (t.id)}
    <div class="ak-toast" class:err={t.kind === 'error'} role="status">
      <span class="ak-toast-label">{t.kind === 'error' ? 'ERROR' : 'OK'}</span>
      <span class="ak-toast-msg">{t.msg}</span>
      <button class="ak-toast-x" on:click={() => dismissToast(t.id)} aria-label="dismiss">✕</button>
    </div>
  {/each}
</div>

<style>
  .ak-toast-host {
    position: fixed;
    right: 20px;
    bottom: 20px;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: 8px;
    pointer-events: none; /* let clicks through the empty stack */
  }
  .ak-toast {
    pointer-events: auto;
    display: flex;
    align-items: baseline;
    gap: 12px;
    max-width: 380px;
    padding: 10px 14px;
    background: var(--surface-2);
    box-shadow: inset 0 0 0 1px var(--rule-hi);
    font-family: var(--ak-sans);
    font-size: 13px;
    line-height: 1.4;
  }
  .ak-toast.err {
    box-shadow: inset 0 0 0 1px var(--ink); /* stronger frame — no red, B&W discipline */
  }
  .ak-toast-label {
    font-family: var(--ak-mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--quiet);
    flex-shrink: 0;
  }
  .ak-toast.err .ak-toast-label {
    color: var(--ink);
  }
  .ak-toast-msg {
    color: var(--ink-2);
    flex: 1;
  }
  .ak-toast-x {
    flex-shrink: 0;
    background: transparent;
    border: none;
    color: var(--quiet);
    font-family: var(--ak-mono);
    font-size: 12px;
    line-height: 1;
    cursor: pointer;
    padding: 0 0 0 4px;
  }
  .ak-toast-x:hover {
    color: var(--ink);
  }
</style>
