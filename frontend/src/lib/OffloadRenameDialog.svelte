<!-- Same-name conflicts after an offload (Hevin 2026-10-09 23:14).
     Opens by itself when the run ends with needs_rename: one row per clip that
     was NOT copied because the backup drive already holds a different file with
     that name. Each row shows both sides and an editable new name (prefilled
     with the server's suggestion). "改名並拷貝" copies just that clip; "跳過"
     asks a second time, inline — no window.confirm/prompt (WKWebView / Tauri). -->
<script>
  import { createEventDispatcher } from 'svelte'
  import { checkNewName } from './offloadConflicts.js'

  export let open = false
  export let rows = [] // [{dst, rel, card, name, state, error, existing, incoming, confirmSkip}]
  export let busyKey = '' // `${dst}\0${rel}` of the row being processed
  const dispatch = createEventDispatcher()

  const keyOf = (r) => `${r.dst}\u0000${r.rel}`
  const base = (p) => String(p || '').split(/[\\/]/).pop()
  const mb = (n) => (n == null ? '—' : n < 1048576 ? `${n} B` : `${(n / 1048576).toFixed(1)} MB`)
  const when = (iso) => (iso ? iso.replace('T', ' ').slice(0, 19) : '—')

  $: pending = rows.filter((r) => r.state === 'pending' || r.state === 'error').length

  function setRow(i, patch) { rows[i] = { ...rows[i], ...patch }; rows = rows }
</script>

{#if open}
  <div class="backdrop">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="ofr-title" data-testid="offload-rename-dialog">
      <div class="head">
        <div class="title" id="ofr-title">目的地已有同名檔 · {pending} 支待處理</div>
        <div class="sub">
          這些檔案<strong>沒有被拷貝</strong>：備份碟上已經有同名但內容不同的檔案，arkiv 不會覆蓋它。
          請給新的檔名再拷貝。全部處理完、校驗通過之前，這張卡<strong>不可格式化</strong>。
        </div>
      </div>

      <div class="list">
        {#each rows as r, i (keyOf(r))}
          {@const nameErr = r.state === 'pending' || r.state === 'error' ? checkNewName(r.name) : ''}
          <div class="row" class:done={r.state === 'renamed'} class:skipped={r.state === 'skipped'}>
            <div class="what">
              <span class="clip">{base(r.rel)}</span>
              <span class="dim">卡 {r.card || '—'} → {base(r.dst)}</span>
            </div>
            <div class="sides">
              <div><span class="lbl">備份碟現有</span> {mb(r.existing?.size)} · {when(r.existing?.mtime)}{r.existing?.hash_prefix ? ` · ${r.existing.hash_prefix}` : ''}</div>
              <div><span class="lbl">卡上這支</span> {mb(r.incoming?.size)} · {when(r.incoming?.mtime)}{r.incoming?.hash_prefix ? ` · ${r.incoming.hash_prefix}` : ''}</div>
            </div>

            {#if r.state === 'renamed'}
              <div class="ok">✓ 已拷貝為 {base(r.dst_rel)} · 校驗通過</div>
            {:else if r.state === 'skipped'}
              <div class="warn">✗ 已跳過 — 這支沒有備份</div>
            {:else if r.confirmSkip}
              <div class="confirm">
                <span class="warn">確定跳過？這支不會在備份碟上，卡片會標記為「未完整備份」。</span>
                <button class="ak-btn" on:click={() => setRow(i, { confirmSkip: false })}>取消</button>
                <button class="ak-btn danger" disabled={busyKey === keyOf(r)}
                        on:click={() => dispatch('skip', { row: r })}>確定跳過（不備份）</button>
              </div>
            {:else}
              <div class="edit">
                <input class="ak-input" aria-label={`${base(r.rel)} 的新檔名`} bind:value={r.name}
                       spellcheck="false" disabled={busyKey === keyOf(r)} />
                <button class="ak-btn primary" disabled={!!nameErr || busyKey === keyOf(r)}
                        on:click={() => dispatch('rename', { row: r })}>
                  {busyKey === keyOf(r) ? '拷貝中…' : '改名並拷貝'}
                </button>
                <button class="ak-btn" disabled={busyKey === keyOf(r)}
                        on:click={() => setRow(i, { confirmSkip: true })}>跳過這支（不拷）</button>
              </div>
              {#if nameErr}<div class="err">{nameErr}</div>{/if}
              {#if r.error}<div class="err">{r.error}</div>{/if}
            {/if}
          </div>
        {/each}
      </div>

      <div class="actions">
        <span class="dim">{pending ? '關閉後可從頁面下方再打開' : '全部處理完了'}</span>
        <button class="ak-btn" disabled={!!busyKey} on:click={() => dispatch('close')}>{pending ? '稍後處理' : '關閉'}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 16px; }
  .modal { background: var(--bg); color: var(--ink); width: 760px; max-width: 100%; max-height: 90vh; padding: 20px; box-shadow: inset 0 0 0 1px var(--invert); display: flex; flex-direction: column; gap: 14px; }
  .title { font-family: var(--ak-mono); font-size: 14px; font-weight: 600; color: var(--danger, #e0563a); }
  .sub { font-size: 12.5px; line-height: 1.55; color: var(--ink-2); margin-top: 6px; }
  .list { overflow: auto; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
  .row { border: 1px solid var(--rule-hi); padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
  .row.done { border-style: dashed; opacity: 0.75; }
  .row.skipped { border-color: var(--danger, #e0563a); }
  .what { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .clip { font-family: var(--ak-mono); font-size: 12.5px; font-weight: 600; }
  .dim { font-size: 11px; color: var(--quiet); }
  .sides { font-family: var(--ak-mono); font-size: 10.5px; color: var(--ink-2); display: flex; flex-direction: column; gap: 2px; }
  .lbl { display: inline-block; min-width: 72px; color: var(--quiet); }
  .edit, .confirm { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .edit .ak-input { flex: 1; min-width: 180px; }
  .err, .warn { font-size: 11px; color: var(--danger, #e0563a); }
  .ok { font-size: 11px; color: var(--ink); }
  .actions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; }
  .danger { background: #b3261e; color: #fff; border-color: #b3261e; }
  .primary { background: var(--invert); color: var(--invert-ink); border-color: var(--invert); }
</style>
