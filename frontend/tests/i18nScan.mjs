// Shared scanner for the i18n coverage test (#581). Line-based on purpose: the
// UI's Chinese lives in template text, attribute values and JS string literals,
// all of which are single-line in this codebase.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

export const SRC = new URL('../src/', import.meta.url).pathname

// Claude-design mock artboards under /_design/* — reference only, not the product (App.svelte).
export const DESIGN_ONLY = new Set([
  'routes/Home.svelte', 'routes/Gallery.svelte', 'routes/MainDark.svelte', 'routes/MainStates.svelte',
  'routes/InspectorFull.svelte', 'routes/Search.svelte', 'routes/Ingest.svelte', 'routes/Settings.svelte',
  'routes/Flows.svelte', 'routes/Edge.svelte',
])

const CJK = /[㐀-鿿豈-﫿]/
// $t('…') / t('…') / tr('…') with a plain literal first argument
const CALL = /(?:\$t|\btr|\bt)\(\s*(['"`])((?:\\.|(?!\1)[^\\])*)\1/g

export function liveFiles() {
  const out = []
  const walk = (d) => {
    for (const n of readdirSync(d)) {
      const p = join(d, n)
      if (statSync(p).isDirectory()) { walk(p); continue }
      const rel = relative(SRC, p)
      if (!/\.(svelte|js)$/.test(n) || rel.startsWith('lib/i18n') || rel === 'lib/i18n.js' || DESIGN_ONLY.has(rel)) continue
      out.push(rel)
    }
  }
  walk(SRC)
  return out.sort()
}

// → { keys: [{key, line}], raw: [{line, text}] }
export function scan(rel) {
  const keys = []
  const raw = []
  let inHtmlComment = false
  let inBlockComment = false
  readFileSync(join(SRC, rel), 'utf8').split('\n').forEach((orig, i) => {
    let s = orig
    if (inHtmlComment) { const e = s.indexOf('-->'); if (e < 0) return; s = s.slice(e + 3); inHtmlComment = false }
    if (inBlockComment) { const e = s.indexOf('*/'); if (e < 0) return; s = s.slice(e + 2); inBlockComment = false }
    s = s.replace(/<!--.*?-->/g, '').replace(/\/\*.*?\*\//g, '')
    if (s.includes('<!--')) { s = s.slice(0, s.indexOf('<!--')); inHtmlComment = true }
    if (s.includes('/*')) { s = s.slice(0, s.indexOf('/*')); inBlockComment = true }
    s = s.replace(/(^|[^:'"`])\/\/.*$/, '$1')
    if (/^\s*\*/.test(s)) return
    s = s.replace(CALL, (m, q, key) => { keys.push({ key: key.replace(/\\(['"`\\])/g, '$1'), line: i + 1, q }); return '' })
    if (orig.includes('i18n-ignore')) return
    if (CJK.test(s)) raw.push({ line: i + 1, text: orig.trim() })
  })
  return { keys, raw }
}
