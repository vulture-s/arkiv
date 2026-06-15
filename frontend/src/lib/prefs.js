// User preferences — currently just the theme, persisted to localStorage.
// Theme is the one Appearance pref that genuinely works: app.css ships full
// [data-theme='dark'] and [data-theme='light'] token sets, so flipping the
// data-theme attribute re-themes the whole product. (UI scale / type density
// from the design mock are NOT wired — the app is px-based on a fixed 1400×900
// artboard, so a root font-size / density class has no effect; faking them would
// violate the evidence discipline. Settings shows them as deferred.)
import { writable, derived } from 'svelte/store'

const THEME_KEY = 'arkiv.theme'
const VALID = new Set(['dark', 'light', 'system'])

function readTheme() {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return VALID.has(v) ? v : 'dark' // default dark — preserves the app's prior look
  } catch {
    return 'dark'
  }
}

// 'dark' | 'light' | 'system'
export const themePref = writable(readTheme())
themePref.subscribe((v) => {
  try { localStorage.setItem(THEME_KEY, v) } catch { /* private mode / quota */ }
})

function systemDark() {
  try { return window.matchMedia('(prefers-color-scheme: dark)').matches } catch { return true }
}

// Resolved 'dark' | 'light' — 'system' follows the OS and re-resolves live when it
// changes. This is what the root + every live route binds its data-theme to.
export const resolvedTheme = derived(
  themePref,
  ($t, set) => {
    if ($t !== 'system') { set($t); return }
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => set(mq.matches ? 'dark' : 'light')
    apply()
    mq.addEventListener?.('change', apply)
    return () => mq.removeEventListener?.('change', apply)
  },
  'dark',
)

// Cycle order for a single toggle button: dark → light → system → dark.
export function cycleTheme() {
  themePref.update((t) => (t === 'dark' ? 'light' : t === 'light' ? 'system' : 'dark'))
}
