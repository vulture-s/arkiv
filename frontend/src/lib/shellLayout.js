// Responsive main shell — decides how wide the pool sidebar and the inspector
// are, and whether each is shown, from the window's CSS width.
//
// Why a function and not @media rules: the UI-scale control (prefs.js) applies
// CSS `zoom` to <html>, which media queries ignore — at 1.2× a 1280px window
// lays out like 1067px but still matches `min-width: 1280px`. Dividing by the
// zoom here keeps the breakpoints honest at every scale.
//
// Before this the shell was a fixed 220 | 1fr | 400 grid: at the window's
// 900px minimum the media grid got 280px — one column of cards.

export const BREAKPOINTS = { wide: 1680, regular: 1280, compact: 1100 }

const WIDTHS = {
  wide: { pool: 240, insp: 460 },
  regular: { pool: 220, insp: 400 },
  compact: { pool: 200, insp: 360 },
  // narrow: the pool is an overlay drawer (takes no column), inspector slims.
  narrow: { pool: 260, insp: 320 },
}

/** 'wide' | 'regular' | 'compact' | 'narrow' for a layout width in CSS px. */
export function layoutClass(width) {
  if (!(width > 0)) return 'regular'
  if (width >= BREAKPOINTS.wide) return 'wide'
  if (width >= BREAKPOINTS.regular) return 'regular'
  if (width >= BREAKPOINTS.compact) return 'compact'
  return 'narrow'
}

/**
 * @param {number} width   window width in layout px (innerWidth / zoom)
 * @param {{pool?: 'auto'|'open'|'closed', insp?: 'auto'|'open'|'closed'}} pref
 * @returns {{layout: string, poolMode: 'column'|'overlay'|'hidden',
 *            poolW: number, inspOpen: boolean, inspW: number,
 *            poolCol: number, inspCol: number}}
 *   poolCol / inspCol = width the grid column actually takes (0 when the panel
 *   is hidden or floats as an overlay) — what the top bar aligns its rules to.
 */
export function shellLayout(width, pref = {}) {
  const layout = layoutClass(width)
  const w = WIDTHS[layout]
  const pool = pref.pool || 'auto'
  const insp = pref.insp || 'auto'

  let poolMode
  if (layout === 'narrow') poolMode = pool === 'open' ? 'overlay' : 'hidden'
  else poolMode = pool === 'closed' ? 'hidden' : 'column'

  const inspOpen = insp !== 'closed'
  return {
    layout,
    poolMode,
    poolW: w.pool,
    inspOpen,
    inspW: w.insp,
    poolCol: poolMode === 'column' ? w.pool : 0,
    inspCol: inspOpen ? w.insp : 0,
  }
}

/** Next pref value when the user clicks a panel toggle in the given state. */
export function togglePanel(isShown) {
  return isShown ? 'closed' : 'open'
}
