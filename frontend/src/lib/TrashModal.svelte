<!-- Trash (recycle bin) browser. Lists trashed originals, restores one back, or
     purges expired / all. Requires admin scope on remote; token-free on loopback.
     Opened via `open`; close via on:close. -->
<script>
  import { createEventDispatcher } from 'svelte'
  import * as api from './api.js'
  import Eyebrow from './Eyebrow.svelte'
  import Mono from './Mono.svelte'
  import ConfirmDialog from './ConfirmDialog.svelte'
  export let open = false
  const dispatch = createEventDispatcher()

  let items = []
  let loading = false
  let busyId = null
  let msg = ''
  let _wasOpen = false

  // (re)load whenever the modal is (re)opened
  $: if (open && !_wasOpen) { _wasOpen = true; load() }
  $: if (!open) { _wasOpen = false }

  async function load() {
    loading = true
    msg = ''
    try {
      const r = await api.getTrash()
      items = (r && r.trash) || []
    } catch (e) {
      msg = '讀取回收桶失敗: ' + e.message
    } finally {
      loading = false
    }
  }

  async function restore(t) {
    busyId = t.id
    msg = ''
    try {
      const r = await api.restoreTrash(t.id)
      msg =
        r.ingest === 'triggered'
          ? `已還原並觸發重新匯入：${r.restored_to || t.filename}`
          : `已還原：${r.restored_to || t.filename}`
      items = items.filter((x) => x.id !== t.id)
    } catch (e) {
      msg = '還原失敗: ' + e.message
    } finally {
      busyId = null
    }
  }

  async function purge(ttlDays) {
    busyId = 'purge'
    msg = ''
    try {
      const r = await api.purgeTrash(ttlDays)
      msg = `已清空 ${r.purged} 項`
      items = ttlDays ? [] : items
      await load()
    } catch (e) {
      msg = '清空失敗: ' + e.message
    } finally {
      busyId = null
    }
  }

  // "清空全部" permanently unlinks every trashed ORIGINAL — the one irreversible
  // action in this modal — so it goes through the same ConfirmDialog the
  // (reversible) delete uses, with the count and total size spelled out.
  let confirmPurgeAll = false
  const fmtBytes = (n) =>
    n >= 1073741824 ? (n / 1073741824).toFixed(1) + ' GB'
      : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB'
      : Math.max(0, Math.round((n || 0) / 1024)) + ' KB'
  $: purgeAllBytes = items.reduce((a, t) => a + (t.size_bytes || 0), 0)

  const fmt = (s) => (s ? s.replace('T', ' ').slice(0, 16) : '—')
</script>

{#if open}
  <div
    class="backdrop"
    role="button"
    tabindex="-1"
    on:click={() => dispatch('close')}
    on:keydown={(e) => {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape') {
        e.preventDefault()
        dispatch('close')
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
      <div class="head">
        <Eyebrow>回收桶 · Trash</Eyebrow>
        <div class="head-actions">
          <button class="ak-btn" on:click={() => purge(null)} disabled={busyId === 'purge' || !items.length} title="清空過期項（依 TTL）">清空過期</button>
          <button class="ak-btn danger" on:click={() => (confirmPurgeAll = true)} disabled={busyId === 'purge' || !items.length} title="立即清空全部">清空全部</button>
          <button class="ak-btn" on:click={() => dispatch('close')}>關閉</button>
        </div>
      </div>

      {#if msg}<div class="msg"><Mono>{msg}</Mono></div>{/if}

      {#if loading}
        <div class="empty"><Mono dim>讀取中…</Mono></div>
      {:else if items.length === 0}
        <div class="empty"><Mono dim>回收桶是空的。</Mono></div>
      {:else}
        <div class="rows">
          {#each items as t (t.id)}
            <div class="row">
              <div class="meta">
                <div class="fname">{t.filename}</div>
                <Mono dim style="font-size:10px;">刪除 {fmt(t.deleted_at)} · 過期 {fmt(t.expires_at)}</Mono>
              </div>
              <button class="ak-btn" on:click={() => restore(t)} disabled={busyId === t.id}>還原</button>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}

<!-- after the modal in DOM order: same z-index, so this paints on top -->
<ConfirmDialog
  open={confirmPurgeAll}
  danger
  title="永久清空回收桶？"
  message={`將永久刪除 ${items.length} 個原始檔（共 ${fmtBytes(purgeAllBytes)}），無法復原。`}
  confirmLabel="永久刪除"
  busy={busyId === 'purge'}
  on:cancel={() => (confirmPurgeAll = false)}
  on:confirm={async () => { await purge(0); confirmPurgeAll = false }}
/>

<style>
  .backdrop {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
    display: flex; align-items: center; justify-content: center; z-index: 1000;
  }
  .modal {
    background: var(--bg); color: var(--ink); width: 560px; max-width: 92vw;
    max-height: 80vh; padding: 18px; box-shadow: inset 0 0 0 1px var(--invert);
    display: flex; flex-direction: column; gap: 12px; overflow: hidden;
  }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .head-actions { display: flex; gap: 6px; }
  .msg { font-size: 11.5px; }
  .empty { padding: 24px 0; text-align: center; }
  .rows { overflow-y: auto; display: flex; flex-direction: column; gap: 1px; background: var(--rule); }
  .row {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    background: var(--bg); padding: 8px 12px;
  }
  .meta { min-width: 0; }
  .fname { font-size: 12.5px; font-weight: 500; word-break: break-all; }
  .danger { background: #b3261e; color: #fff; border-color: #b3261e; }
</style>
