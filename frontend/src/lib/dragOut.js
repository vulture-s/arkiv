// Drag a clip out of arkiv into an NLE / Finder (roadmap 12.6) — desktop app only.
//
// A WebView's HTML5 drag can only carry text or URLs; Resolve and Finder want a real
// file. tauri-plugin-drag starts a NATIVE drag session, but it needs an absolute
// path at the moment the drag begins — and macOS only starts a drag while the mouse
// button is still down, so there is no time to ask the backend then. Hence the
// two halves: `prepare` (on hover / when the Inspector opens) asks
// POST /api/media/{id}/drag-source for the path — the original, or an IN/OUT sub-clip
// it cuts — and `startDragOut` (inside dragstart) only reads that cache.
import * as api from './api.js'

export function canDragOut() {
  const t = typeof window !== 'undefined' ? window.__TAURI__ : null
  return !!(t?.core?.invoke && t?.core?.Channel)
}

/** Cache key: same clip + same window (to the centisecond the backend names files by). */
export function sourceKey(id, trim) {
  const r = (v) => (v == null ? '' : Number(v).toFixed(2))
  return `${id}|${r(trim?.in_s)}|${r(trim?.out_s)}`
}

const cache = new Map()
const pending = new Map()

/** Ask the backend for the path to drag; cached per clip + window. Rejects with the
 *  backend's reason (missing original, cut failed). */
export function prepare(id, trim) {
  const key = sourceKey(id, trim)
  if (cache.has(key)) return Promise.resolve(cache.get(key))
  if (pending.has(key)) return pending.get(key)
  const p = api.dragSource(id, trim || {})
    .then((src) => { cache.set(key, src); return src })
    .finally(() => pending.delete(key))
  pending.set(key, p)
  return p
}

export function ready(id, trim) {
  return cache.get(sourceKey(id, trim)) || null
}

/** PNG data URL for the drag preview: the clip's thumbnail, else a plain tile. */
export function dragImage(imgEl) {
  const c = document.createElement('canvas')
  c.width = 128
  c.height = 72
  const g = c.getContext('2d')
  g.fillStyle = '#3a3a3a'
  g.fillRect(0, 0, c.width, c.height)
  try {
    if (imgEl && imgEl.complete && imgEl.naturalWidth) g.drawImage(imgEl, 0, 0, c.width, c.height)
    return c.toDataURL('image/png')
  } catch {
    // A cross-origin thumbnail taints the canvas; the plain tile still drags.
    g.fillRect(0, 0, c.width, c.height)
    return c.toDataURL('image/png')
  }
}

/** Call from a dragstart handler (after preventDefault). True when a native drag
 *  was started; false when the path is not prepared yet — the caller should prepare
 *  and tell the user to drag again. */
export function startDragOut(id, trim, imgEl) {
  const src = ready(id, trim)
  if (!src || !canDragOut()) return false
  const core = window.__TAURI__.core
  core.invoke('plugin:drag|start_drag', {
    item: [src.path],
    image: dragImage(imgEl),
    options: { mode: 'copy' },
    onEvent: new core.Channel(),
  }).catch(() => {})
  return true
}
