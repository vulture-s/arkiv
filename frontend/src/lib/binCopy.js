// 精選集「複製進專案」ndjson stream → log lines + an honest final verdict.
//
// Audit 2026-10-09 K1: the inline handler only told `index` "start" from
// "anything else", so the backend's
//   {"type":"index","status":"busy","error":"…已複製檔案但略過索引…"}
// was logged as 「▸ 索引完成 (code undefined)」 and the run toasted 「複製完成」
// — while the destination project had nothing indexed. An ingest that exited
// non-zero, a refused project registration and a stream cut off before `done`
// were just as invisible. Pure module so node:test can pin it.
import { tr } from './i18n.js'

export function createCopyTracker() {
  let summary = null
  let sawDone = false
  const problems = []
  let busySkipped = false // the 「未索引」 problem is recorded (locale-independent check)

  function handle(ev) {
    if (!ev || typeof ev !== 'object') return null
    switch (ev.type) {
      case 'gate':
        return ev.action === 'skipped'
          ? tr('⤫ 跳過 {clip} · {status}', { clip: `${ev.project_name}#${ev.media_id}`, status: ev.status })
          : tr('＋ 排入 {clip}', { clip: `${ev.project_name}#${ev.media_id}` })
      case 'copy':
        if (ev.error) { problems.push(tr('複製失敗 {file}', { file: ev.file })); return tr('✗ 複製失敗 {file}: {error}', { file: ev.file, error: ev.error }) }
        return tr('⇄ 複製 {file} ({done}/{total})', { file: ev.file, done: ev.done, total: ev.total })
      case 'index':
        if (ev.status === 'start') return tr('▸ 索引 {n} 檔…', { n: ev.files })
        if (ev.status === 'busy') {
          problems.push(tr('未索引（已有匯入任務進行中，略過索引）')); busySkipped = true
          return tr('✗ 略過索引：{reason}', { reason: ev.error || tr('已有匯入任務進行中') })
        }
        if (ev.status === 'done' && ev.code === 0) return tr('▸ 索引完成 (code 0)')
        problems.push(tr('索引失敗（code {code}）', { code: ev.code }))
        return tr('✗ 索引失敗 (code {code})', { code: ev.code })
      case 'log':
        return ev.line || null
      case 'registered':
        if (ev.error) { problems.push(tr('註冊專案失敗：{error}', { error: ev.error })); return tr('✗ 註冊: {error}', { error: ev.error }) }
        return tr('✓ 已註冊新專案「{name}」', { name: ev.name })
      case 'done':
        sawDone = true
        summary = ev.summary || null
        if (summary && summary.index_skipped_busy && !busySkipped) {
          problems.push(tr('未索引（已有匯入任務進行中，略過索引）')); busySkipped = true
        }
        return null
      default:
        return null
    }
  }

  function outcome() {
    if (!sawDone) {
      const msg = tr('複製中斷：沒有收到完成訊號，結果不完整 — 請檢查目的專案')
      return { kind: 'error', msg: problems.length ? tr('{msg}（{problems}）', { msg, problems: problems.join(tr('；')) }) : msg }
    }
    const s = summary || {}
    const skipped = (s.skipped || []).length
    const base = skipped
      ? tr('→ {dest}：{n} 支，略過 {skipped}', { dest: s.dest, n: s.copied ?? 0, skipped })
      : tr('→ {dest}：{n} 支', { dest: s.dest, n: s.copied ?? 0 })
    if (problems.length) return { kind: 'error', msg: tr('複製未完整 {base} · {problems}', { base, problems: problems.join(tr('；')) }) }
    return { kind: 'ok', msg: tr('複製完成 {base}', { base }) }
  }

  return { handle, outcome, get summary() { return summary } }
}
