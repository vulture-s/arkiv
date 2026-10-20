// Settings → Projects: first-run guidance + human-readable registry feedback.
//
// Why this exists (2026-10-20 Windows first-run walkthrough): a new user opened
// the desktop app, saw the "ADD PROJECT" form (name / path / tags) and read it as
// "start a new project here". They registered a NAS folder of raw footage. But
// the registry only records OTHER, already-built arkiv libraries so search can
// span them — it never creates a library and never imports media. The real way
// to start is + INGEST. These helpers decide when to say so, and translate the
// backend's terse registry errors / health codes into sentences a user can act on.

/** Media the user brought in themselves: `total` minus the bundled sample clips
 *  (the server auto-seeds a fresh library with them, so total > 0 does NOT mean
 *  "this person has started"). null when the total is unknown — callers must not
 *  show first-run guidance on a guess. */
export function ownMediaCount(stats, sample) {
  const total = stats && Number.isFinite(stats.total) ? stats.total : null
  if (total === null) return null
  const sampleN = sample && sample.loaded && Array.isArray(sample.media_ids) ? sample.media_ids.length : 0
  return Math.max(0, total - sampleN)
}

/** True only when we KNOW the open library holds none of the user's own media. */
export function needsIngestHint(stats, sample) {
  return ownMediaCount(stats, sample) === 0
}

/** Health code (from /api/projects/health) → short user-facing label. */
const HEALTH_LABELS = {
  ok: '正常',
  path_not_found: '找不到資料夾',
  db_missing: '不是 arkiv 素材庫',
  chroma_missing: '索引不完整',
  nas_unmounted: 'NAS 未掛載',
  timeout: '逾時',
}
export function healthLabel(status) {
  if (!status) return '—'
  return HEALTH_LABELS[status] || status
}

/** Follow-up note after a successful add, based on the new entry's health. The
 *  backend only checks that the path EXISTS — a plain footage folder registers
 *  fine but is not a library. Say so instead of a cheerful "已加入". */
export function addedMessage(name, status) {
  if (status === 'db_missing') {
    return `已登記 ${name}，但這個資料夾裡沒有 arkiv 素材庫（找不到 .arkiv/）。它不會出現在搜尋裡；要把素材匯入 arkiv，請改用右上角 ＋ INGEST，然後可以把這筆移除。`
  }
  if (status === 'path_not_found') return `已登記 ${name}，但目前找不到這個資料夾。`
  if (status === 'nas_unmounted') return `已登記 ${name}，但 NAS 目前沒有掛載，連上後才搜得到。`
  return `已登記 ${name}`
}

/** ApiError from POST /api/projects → sentence. */
export function addErrorMessage(err, name) {
  const status = err && err.status
  // Prefer the backend's own detail over ApiError's "arkiv API 400 on …：" wrapper.
  const d = err && err.body && typeof err.body === 'object' ? err.body.detail : null
  const msg = (d && typeof d === 'object' ? d.message : d) || (err && err.message) || ''
  if (status === 409) return `已經有叫「${name}」的專案了，換個名稱。`
  if (status === 403) return msg || '目前的方案不能再加入專案。'
  if (/project path not found/i.test(msg)) return '找不到這個資料夾。請確認路徑正確、NAS 已掛載。'
  if (/already registered/i.test(msg)) return '這個資料夾已經登記過了。'
  return `加入失敗：${msg || '未知錯誤'}`
}
