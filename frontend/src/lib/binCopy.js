// 精選集「複製進專案」ndjson stream → log lines + an honest final verdict.
//
// Audit 2026-10-09 K1: the inline handler only told `index` "start" from
// "anything else", so the backend's
//   {"type":"index","status":"busy","error":"…已複製檔案但略過索引…"}
// was logged as 「▸ 索引完成 (code undefined)」 and the run toasted 「複製完成」
// — while the destination project had nothing indexed. An ingest that exited
// non-zero, a refused project registration and a stream cut off before `done`
// were just as invisible. Pure module so node:test can pin it.

export function createCopyTracker() {
  let summary = null
  let sawDone = false
  const problems = []

  function handle(ev) {
    if (!ev || typeof ev !== 'object') return null
    switch (ev.type) {
      case 'gate':
        return `${ev.action === 'skipped' ? '⤫ 跳過' : '＋ 排入'} ${ev.project_name}#${ev.media_id}${ev.action === 'skipped' ? ' · ' + ev.status : ''}`
      case 'copy':
        if (ev.error) { problems.push(`複製失敗 ${ev.file}`); return `✗ 複製失敗 ${ev.file}: ${ev.error}` }
        return `⇄ 複製 ${ev.file} (${ev.done}/${ev.total})`
      case 'index':
        if (ev.status === 'start') return `▸ 索引 ${ev.files} 檔…`
        if (ev.status === 'busy') {
          problems.push('未索引（已有匯入任務進行中，略過索引）')
          return `✗ 略過索引：${ev.error || '已有匯入任務進行中'}`
        }
        if (ev.status === 'done' && ev.code === 0) return '▸ 索引完成 (code 0)'
        problems.push(`索引失敗（code ${ev.code}）`)
        return `✗ 索引失敗 (code ${ev.code})`
      case 'log':
        return ev.line || null
      case 'registered':
        if (ev.error) { problems.push(`註冊專案失敗：${ev.error}`); return `✗ 註冊: ${ev.error}` }
        return `✓ 已註冊新專案「${ev.name}」`
      case 'done':
        sawDone = true
        summary = ev.summary || null
        if (summary && summary.index_skipped_busy && !problems.some((p) => p.startsWith('未索引'))) {
          problems.push('未索引（已有匯入任務進行中，略過索引）')
        }
        return null
      default:
        return null
    }
  }

  function outcome() {
    if (!sawDone) {
      return { kind: 'error', msg: '複製中斷：沒有收到完成訊號，結果不完整 — 請檢查目的專案' + (problems.length ? `（${problems.join('；')}）` : '') }
    }
    const s = summary || {}
    const skipped = (s.skipped || []).length
    const base = `→ ${s.dest}：${s.copied ?? 0} 支${skipped ? '，略過 ' + skipped : ''}`
    if (problems.length) return { kind: 'error', msg: `複製未完整 ${base} · ${problems.join('；')}` }
    return { kind: 'ok', msg: `複製完成 ${base}` }
  }

  return { handle, outcome, get summary() { return summary } }
}
