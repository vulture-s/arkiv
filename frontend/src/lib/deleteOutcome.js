// Turn a delete response into the toast the user should actually read.
//
// Audit 2026-10-09 K1: the toast used to read `r.external` / `r.filename`,
// fields the backend never sends — DELETE /api/media/{id} answers
// {ok, media_id, file_deleted, warning}. So a metadata-only delete (the clip
// lives outside PROJECT_ROOT / ARKIV_MEDIA_ROOTS — every NAS /Volumes library
// by default — or the move into .arkiv/trash failed) was announced as
// 「已移至回收桶」, right under a confirm dialog promising 30-day restore. In
// that case nothing is in the recycle bin and the tags / rating / transcript are
// gone for good; the user has to be told, not reassured.
//
// Pure function so it can be pinned by node:test without mounting Svelte.
//   kind:      'single' | 'bulk'
//   r:         the API response
//   label:     what the confirm dialog called it (filename or "N 支素材")
//   requested: the ids that were sent
// → { msg, kind: 'ok' | 'error', removeIds }  (removeIds = rows that are gone)

const NOT_TRASHED_HINT = '原始檔未進回收桶，標籤／評分／逐字稿無法還原'

export function describeDelete(kind, r, label, requested = []) {
  r = r || {}
  if (kind === 'single') {
    const id = r.media_id != null ? r.media_id : requested[0]
    if (r.file_deleted) {
      if (r.warning) {
        return { msg: `已移至回收桶 · ${label}（注意：${r.warning}）`, kind: 'error', removeIds: [id] }
      }
      return { msg: `已移至回收桶 · ${label}`, kind: 'ok', removeIds: [id] }
    }
    return {
      msg: `已刪除「${label}」的資料，但未進回收桶：${NOT_TRASHED_HINT}` +
        (r.warning ? `（${r.warning}）` : ''),
      kind: 'error',
      removeIds: [id],
    }
  }

  const deleted = r.deleted || []
  const skipped = r.skipped || [] // not found: already gone, safe to drop from the grid
  const errors = r.errors || []
  const notTrashed = r.not_trashed || []
  const notTrashedIds = new Set(notTrashed.map((x) => x.media_id))
  const otherWarnings = (r.warnings || []).filter((w) => !notTrashedIds.has(w.media_id))
  const trashed = deleted.length - notTrashed.length

  const parts = [`已移至回收桶 ${trashed} 支`]
  if (notTrashed.length) parts.push(`${notTrashed.length} 支未進回收桶（${NOT_TRASHED_HINT}）`)
  if (skipped.length) parts.push(`跳過 ${skipped.length} 支（找不到）`)
  if (errors.length) parts.push(`${errors.length} 支失敗`)
  if (otherWarnings.length) parts.push(`${otherWarnings.length} 支有警告：${otherWarnings[0].warning}`)

  return {
    msg: parts.join(' · '),
    kind: notTrashed.length || errors.length || otherWarnings.length ? 'error' : 'ok',
    removeIds: [...deleted, ...skipped],
  }
}
