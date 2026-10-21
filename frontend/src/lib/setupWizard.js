// First-run setup wizard (roadmap 9.8a) — pure helpers, node:test-able.
//
// The wizard's job is to make the first ingest not be where the user discovers that
// ffmpeg is missing, that their PC has no NVIDIA card for the default CUDA path, or
// that ~10 GB of models are about to download. Everything here only READS
// /api/setup/status; the page does the fetching.

import { needsIngestHint } from './projectsFirstRun.js'

/** Should opening the app land on the wizard? Only for a user who has not finished
 *  (or skipped) it AND whose library holds none of their own media — someone who
 *  upgraded with a full library is not a first-run user and must not be ambushed. */
export function shouldShowWizard(setup, stats, sample) {
  if (!setup || setup.completed) return false
  return needsIngestHint(stats, sample) === true
}

export function fmtBytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '0 MB'
  const gb = n / 1024 ** 3
  return gb >= 1 ? `${gb.toFixed(2)} GB` : `${Math.round(n / 1024 ** 2)} MB`
}

/** One line of progress for a pull item. */
export function pullLine(item) {
  if (!item) return ''
  if (item.status === 'done') return '完成'
  if (item.status === 'error') return `失敗：${item.message || '未知錯誤'}`
  if (item.status === 'queued') return '排隊中'
  if (item.total) {
    const pct = Math.min(100, Math.round((100 * (item.completed || 0)) / item.total))
    return `${pct}% · ${fmtBytes(item.completed)} / ${fmtBytes(item.total)}`
  }
  return item.completed ? `已下載 ${fmtBytes(item.completed)}` : item.message || '下載中…'
}

/** The install command for ffmpeg on this OS (shown, never run). */
export function ffmpegHint(osName) {
  const os = String(osName || '')
  if (/^Darwin/i.test(os)) return 'brew install ffmpeg'
  if (/^Windows/i.test(os)) return 'winget install ffmpeg'
  return 'sudo apt install ffmpeg'
}

/** Rows for step 2: Whisper first (it is what ingest needs first), then Ollama. */
export function modelRows(setup) {
  if (!setup) return []
  const rows = []
  const w = setup.whisper
  if (w) rows.push({ key: w.name, label: '語音轉文字（Whisper）', name: w.name, present: !!w.present, size: w.size_hint || '' })
  for (const m of setup.ollama?.models || []) {
    rows.push({ key: m.name, label: `${m.label}（Ollama）`, name: m.name, present: !!m.present, size: m.size_hint || '' })
  }
  return rows
}

/** Problems that stop ingest outright, in the order the user should fix them. */
export function blockers(setup) {
  if (!setup) return []
  const out = []
  if (!setup.ffmpeg) out.push(`沒有 ffmpeg——匯入需要它。安裝：${ffmpegHint(setup.hardware?.os)}`)
  if (setup.transcribe && setup.transcribe.ok === 'false') out.push(setup.transcribe.note)
  return out
}
