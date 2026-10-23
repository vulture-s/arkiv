// Inspector transport keys — Resolve / Premiere layout (Hevin 2026-10-23: 「根據 DAW 的快捷鍵」).
// Pure functions so the key map and the J/K/L shuttle can be node-tested; the
// Inspector owns the player and applies the actions.

/** Shuttle speeds a repeated J or L steps through. */
export const SHUTTLE_SPEEDS = [1, 2, 4, 8]

/**
 * Next shuttle rate after a J / K / L press. Positive = forward, negative = reverse,
 * 0 = stopped. Pressing L while already going forward goes one speed faster (up to
 * the top), pressing it while stopped or reversing starts at 1×; J mirrors that.
 */
export function shuttleNext(rate, key) {
  if (key === 'k') return 0
  const dir = key === 'l' ? 1 : -1
  const cur = Math.abs(rate || 0)
  const same = Math.sign(rate || 0) === dir
  const i = same ? SHUTTLE_SPEEDS.indexOf(cur) : -1
  const next = SHUTTLE_SPEEDS[Math.min(i + 1, SHUTTLE_SPEEDS.length - 1)]
  return dir * next
}

/**
 * Map a keydown to a transport action, or null to leave the key alone.
 * Letters go by `e.code` so the map works under a Chinese IME and on any
 * keyboard layout; ⌥X on a Mac produces `≈` as `e.key`, which is why.
 * ⌘ / Ctrl chords are never taken — they belong to the app and the OS.
 */
export function keyAction(e) {
  if (e.metaKey || e.ctrlKey) return null
  const shift = !!e.shiftKey
  const alt = !!e.altKey
  switch (e.code) {
    case 'Space': return alt ? null : 'toggle'
    case 'KeyJ': return alt || shift ? null : 'shuttleJ'
    case 'KeyK': return alt || shift ? null : 'shuttleK'
    case 'KeyL': return alt || shift ? null : 'shuttleL'
    case 'KeyI': return alt ? null : shift ? 'gotoIn' : 'markIn'
    case 'KeyO': return alt ? null : shift ? 'gotoOut' : 'markOut'
    case 'KeyX': return alt && !shift ? 'clearInOut' : null
    case 'ArrowLeft': return alt ? null : shift ? 'back1s' : 'prevFrame'
    case 'ArrowRight': return alt ? null : shift ? 'fwd1s' : 'nextFrame'
    case 'Comma': return alt || shift ? null : 'prevFrame'
    case 'Period': return alt || shift ? null : 'nextFrame'
    case 'Home': return 'gotoStart'
    case 'End': return 'gotoEnd'
    case 'Slash': return alt || shift ? null : 'playRange'
    default: return null
  }
}

/** The hint row shown above the waveform: [keys, what it does]. */
export const KEY_HINTS = [
  ['Space', '播放'],
  ['J K L', '倒轉／停／快轉'],
  ['← →', '逐格'],
  ['I O', '設 IN／OUT'],
  ['⇧I ⇧O', '跳到 IN／OUT'],
  ['/', '播 IN→OUT'],
  ['⌥X', '清除'],
]
