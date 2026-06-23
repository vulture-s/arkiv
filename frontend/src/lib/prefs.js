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

// ── UI scale (Phase 9.7 G4) ──────────────────────────────────────────────────
// The layout is px-based, so a root font-size has no effect (why this was long
// deferred). CSS `zoom` scales the whole rendered tree regardless of px/rem and
// is supported in WebKit (Tauri/WKWebView) + Chromium — so it's the honest way
// to make a real, working UI-scale control. Type density (independent spacing)
// stays deferred: px spacing won't reflow from a density class.
const SCALE_KEY = 'arkiv.uiScale'
export const SCALE_MIN = 0.8
export const SCALE_MAX = 1.4
function readScale() {
  try {
    const v = parseFloat(localStorage.getItem(SCALE_KEY))
    return v >= SCALE_MIN && v <= SCALE_MAX ? v : 1.0
  } catch {
    return 1.0
  }
}
export const uiScale = writable(readScale())
uiScale.subscribe((v) => {
  try { localStorage.setItem(SCALE_KEY, String(v)) } catch { /* private mode / quota */ }
  try { document.documentElement.style.zoom = String(v) } catch { /* SSR / no DOM */ }
})
