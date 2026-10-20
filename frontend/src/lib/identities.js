// A6 person annotations (manual, human-entered — never face recognition).
// Pure helpers for the Inspector's 人物 block, kept out of the .svelte file so
// node --test can pin them (frontend/tests/identities.test.mjs).

// The range a new annotation covers, from the inspector's IN/OUT marks.
//   both marks, IN < OUT  → that range
//   only IN               → IN .. clip end   (needs a known duration)
//   only OUT              → 0 .. OUT
//   neither (or unusable) → {} = the whole clip
// Returns the POST body's time fields: {start_s, end_s} or {}.
export function identityRange(inSec, outSec, duration) {
  const ok = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0
  const dur = ok(duration) && duration > 0 ? duration : null
  let s = ok(inSec) ? inSec : null
  let e = ok(outSec) ? outSec : null
  if (s == null && e == null) return {}
  if (s == null) s = 0
  if (e == null) e = dur
  if (e == null || !(s < e)) return {}
  return { start_s: s, end_s: e }
}

// Autocomplete: names (from /api/identities/names, [{name, count}]) matching
// `query` as a case-insensitive substring. Prefix matches first, then by how
// many clips use the name, then alphabetically. Names already on this clip are
// skipped (`exclude`), and an exact match of what is typed is not suggested.
export function suggestNames(names, query, exclude = [], limit = 8) {
  const q = (query || '').trim().normalize('NFC').toLowerCase()
  const skip = new Set((exclude || []).map((n) => String(n).toLowerCase()))
  const rows = []
  for (const row of names || []) {
    if (!row || typeof row.name !== 'string') continue
    const low = row.name.toLowerCase()
    if (skip.has(low)) continue
    if (q && (low === q || !low.includes(q))) continue
    rows.push({ name: row.name, count: row.count || 0, prefix: q ? low.startsWith(q) : true })
  }
  rows.sort((a, b) => (b.prefix - a.prefix) || (b.count - a.count) || a.name.localeCompare(b.name))
  return rows.slice(0, limit).map((r) => r.name)
}

function mmss(sec) {
  const t = Math.max(0, Math.floor(sec))
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = t % 60
  const p2 = (n) => String(n).padStart(2, '0')
  return h ? `${h}:${p2(m)}:${p2(s)}` : `${p2(m)}:${p2(s)}`
}

// Label for one annotation row (or a {start_s, end_s} range): 整支片 / 00:12–00:31.
export function formatIdentityRange(ident) {
  if (!ident || ident.start_s == null || ident.end_s == null) return '整支片'
  return `${mmss(ident.start_s)}–${mmss(ident.end_s)}`
}

// Keyboard handling for the name input, as a pure state step so the
// Enter/Esc/arrow rules are testable. state = {highlight} (-1 = none).
// Returns {action, highlight, name?}: action ∈ 'submit' | 'cancel' | 'move' | null.
export function nameKeyStep(key, typed, suggestions, highlight) {
  const n = (suggestions || []).length
  if (key === 'Escape') return { action: 'cancel', highlight: -1 }
  if (key === 'ArrowDown' && n) return { action: 'move', highlight: (highlight + 1) % n }
  if (key === 'ArrowUp' && n) return { action: 'move', highlight: highlight <= 0 ? n - 1 : highlight - 1 }
  if (key === 'Enter') {
    const name = highlight >= 0 && highlight < n ? suggestions[highlight] : (typed || '').trim()
    return name ? { action: 'submit', highlight: -1, name } : { action: null, highlight }
  }
  return { action: null, highlight }
}
