// Export UX feedback (B11 follow-up). The toasts landed in 912207d; what was
// still missing is (1) any sign that an export is in flight — a slow timeline
// or whole-library CSV looked like a dead button, inviting double clicks that
// fired duplicate downloads — and (2) what actually landed: the toast named the
// format but not the file name or its size.
//
// The saved path is NOT shown: the download goes through a blob <a download>,
// and neither a browser nor WKWebView (Tauri) tells the page where the file was
// written. Claiming "~/Downloads/" would be a guess, so we don't.

// 1536 → "1.5 KB". Binary units (what Finder/Explorer-ish sizes read as).
export function formatBytes(n) {
  if (typeof n !== 'number' || !isFinite(n) || n < 0) return null
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

// "已下載 a.edl（1.5 KB）· note". Size omitted when unknown; an empty file is
// called out because a 0-byte export is almost always a backend-side problem.
export function downloadedMsg(filename, size, note = '') {
  const s = formatBytes(size)
  let msg = `已下載 ${filename}`
  if (s) msg += size === 0 ? '（0 B · 空檔案）' : `（${s}）`
  if (note) msg += ` · ${note}`
  return msg
}

// One export at a time per screen. run(key, fn) ignores calls while another is
// in flight (returns false) and reports the busy key through onChange so the
// component can disable its buttons / show a spinner. Always clears, even on throw.
export function createExportGuard(onChange = () => {}) {
  let busy = null
  return {
    get busy() { return busy },
    async run(key, fn) {
      if (busy) return false
      busy = key
      onChange(busy)
      try {
        await fn()
        return true
      } finally {
        busy = null
        onChange(busy)
      }
    },
  }
}
