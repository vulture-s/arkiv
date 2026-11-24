// User preferences — currently just the theme, persisted to localStorage.
// Theme is the one Appearance pref that genuinely works: app.css ships full
// [data-theme='dark'] and [data-theme='light'] token sets, so flipping the
// data-theme attribute re-themes the whole product. (UI scale / type density
// from the design mock are NOT wired — the app is px-based on a fixed 1400×900
// artboard, so a root font-size / density class has no effect; faking them would
// violate the evidence discipline. Settings shows them as deferred.)
import { writable, derived, get } from 'svelte/store'
import { shellLayout, togglePanel } from './shellLayout.js'

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

// ── Responsive shell (pool sidebar / inspector) ──────────────────────────────
// The breakpoint maths lives in shellLayout.js (pure, unit-tested). Here: the
// live inputs (window width ÷ UI zoom, the user's panel toggles) and the CSS
// vars the top bar reads so its rules stay on the body's column edges.

const PANELS_KEY = 'arkiv.panels'
const PANEL_VALUES = new Set(['auto', 'open', 'closed'])
function readPanels() {
  try {
    const v = JSON.parse(localStorage.getItem(PANELS_KEY) || '{}')
    return {
      pool: PANEL_VALUES.has(v.pool) ? v.pool : 'auto',
      insp: PANEL_VALUES.has(v.insp) ? v.insp : 'auto',
    }
  } catch {
    return { pool: 'auto', insp: 'auto' }
  }
}
export const panelPref = writable(readPanels())
panelPref.subscribe((v) => {
  try { localStorage.setItem(PANELS_KEY, JSON.stringify(v)) } catch { /* private mode / quota */ }
})

const winWidth = writable(typeof window === 'undefined' ? 0 : window.innerWidth)
if (typeof window !== 'undefined') {
  window.addEventListener('resize', () => winWidth.set(window.innerWidth))
}

export const shell = derived([winWidth, uiScale, panelPref], ([$w, $s, $p]) =>
  shellLayout($w / ($s || 1), $p),
)
shell.subscribe((s) => {
  try {
    const st = document.documentElement.style
    st.setProperty('--pool-w', `${s.poolCol}px`)
    st.setProperty('--inspector-w', `${s.inspCol}px`)
    document.documentElement.dataset.layout = s.layout
  } catch { /* SSR / no DOM */ }
})

export function togglePool() {
  const s = get(shell)
  const shown = s.poolMode !== 'hidden'
  // In narrow the drawer is transient: closing it goes back to 'auto' so the
  // pool returns as a column once the window is widened again.
  const next = s.layout === 'narrow' && shown ? 'auto' : togglePanel(shown)
  panelPref.update((p) => ({ ...p, pool: next }))
}
export function closePoolOverlay() {
  if (get(shell).poolMode === 'overlay') panelPref.update((p) => ({ ...p, pool: 'auto' }))
}
export function toggleInspector() {
  const shown = get(shell).inspOpen
  panelPref.update((p) => ({ ...p, insp: togglePanel(shown) }))
}
