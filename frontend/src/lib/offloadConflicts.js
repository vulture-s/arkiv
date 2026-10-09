// Offload same-name conflicts → rename dialog (Hevin 2026-10-09 23:14).
//
// The backend never overwrites an existing backup. When the destination already
// holds a DIFFERENT file with the incoming clip's name, the run ends with
// status "needs_rename" and one item per conflict (both sides' size / mtime /
// hash prefix + a suggested free name). This module holds the decisions the
// Offload view makes off that: open the dialog, prefill the names, and — the
// part that protects footage — whether the card may read as done. Only "done"
// (every clip on every drive, verified, with a manifest) is formattable.

const flatten = (summary) =>
  Object.values(summary || {}).flatMap((s) => (s && Array.isArray(s.needs_rename) ? s.needs_rename : []))

/** Conflict items from a `done` event → dialog rows (name prefilled). */
export function conflictsFromDone(ev) {
  const items = Array.isArray(ev?.needs_rename) ? ev.needs_rename : flatten(ev?.summary)
  return items.map((it) => ({ ...it, name: it.suggested_name || '', state: 'pending', error: '' }))
}

export const shouldOpenRenameDialog = (ev) => conflictsFromDone(ev).length > 0

/** One word for the card: done | needs_rename | incomplete | failed. */
export function cardStatus(ev) {
  if (!ev) return 'failed'
  if (conflictsFromDone(ev).length) return 'needs_rename'
  if (ev.status) return ev.status
  return ev.code === 0 ? 'done' : 'failed'
}

export const canFormat = (status) => status === 'done'

export function statusLine(status, pending = 0) {
  switch (status) {
    case 'done': return '✓ 轉存完成 · 全部校驗通過'
    case 'needs_rename': return `⚠ ${pending} 支同名衝突待改名 — 卡片尚未完整備份，請勿格式化`
    case 'incomplete': return '✗ 有檔案未備份（已跳過）— 卡片尚未完整備份，請勿格式化'
    default: return '✗ 轉存有失敗 — 請勿格式化'
  }
}

// Mirrors offload._validate_new_name so the user hears about a bad name before
// a round-trip; the server is still the authority.
const BAD = /[\\/:*?"<>|\x00-\x1f\x7f]/
export function checkNewName(name) {
  const n = String(name ?? '')
  if (!n.trim()) return '檔名不能空白'
  if (n !== n.trim()) return '檔名前後不能有空白'
  if (BAD.test(n)) return '只能是檔名，不能含 / \\ : * ? " < > | 等字元'
  if (n.startsWith('.')) return '檔名不能以 . 開頭'
  const low = n.toLowerCase()
  if (low.endsWith('.partial') || low.endsWith('.mhl') || low === 'ascmhl') return '這個副檔名保留給轉存流程'
  if (new TextEncoder().encode(n).length > 255) return '檔名太長'
  return ''
}

/** Fold one /api/offload/resolve reply into the rows + card status. */
export function applyResolveResult(rows, row, result) {
  const fileStatus = result?.file?.status
  const next = rows.map((r) => {
    if (r.dst !== row.dst || r.rel !== row.rel) return r
    if (fileStatus === 'verified') return { ...r, state: 'renamed', error: '', dst_rel: result.file.dst_rel }
    if (fileStatus === 'skipped_unbacked') return { ...r, state: 'skipped', error: '' }
    return { ...r, state: 'error', error: result?.file?.error || '補拷失敗' }
  })
  return { rows: next, status: result?.status || 'failed', summary: result?.summary }
}
