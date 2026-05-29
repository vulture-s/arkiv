// Forbidden-CSS / brand-integrity scanner — Tier-A hard gate.
// Forbids: backdrop-filter, color-mix(, oklch( (WebView2+WKWebView compat).
// ALLOWS: mix-blend-mode (the design's own grain overlay + Thumb use it).
// Also: no icon-library imports; --cyan token must stay #18b6dc.
//
//   node scripts/audit/forbidden-css.mjs [srcDir=src]
//
// Exit 0 = clean. Exit 1 = violation(s).
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, extname } from 'node:path'

const SRC = process.argv[2] || 'src'
const FORBIDDEN = [
  { re: /backdrop-filter/, label: 'backdrop-filter' },
  { re: /color-mix\s*\(/, label: 'color-mix()' },
  { re: /oklch\s*\(/, label: 'oklch()' },
]
const ICON_LIB =
  /(?:import|from)\s+['"](?:@?lucide|@fortawesome|heroicons|feather-icons|react-icons|svelte-icons|@iconify)/
const EXTS = new Set(['.svelte', '.css', '.js', '.mjs'])
const SKIP = new Set(['node_modules', 'dist', '.audit'])

const files = []
;(function walk(d) {
  for (const e of readdirSync(d)) {
    const p = join(d, e)
    const s = statSync(p)
    if (s.isDirectory()) {
      if (!SKIP.has(e)) walk(p)
    } else if (EXTS.has(extname(p))) files.push(p)
  }
})(SRC)

const violations = []
let sawCyan = false
for (const f of files) {
  const txt = readFileSync(f, 'utf8')
  txt.split('\n').forEach((line, i) => {
    for (const { re, label } of FORBIDDEN) {
      if (re.test(line)) violations.push(`${f}:${i + 1}  forbidden ${label}: ${line.trim()}`)
    }
    if (ICON_LIB.test(line)) violations.push(`${f}:${i + 1}  icon-library import: ${line.trim()}`)
  })
  if (f.endsWith('app.css')) {
    sawCyan = true
    if (!/--cyan:\s*#18b6dc;/.test(txt))
      violations.push(`${f}  --cyan token altered/missing (must be #18b6dc)`)
  }
}
if (!sawCyan) violations.push('app.css not found — cannot verify --cyan integrity')

if (violations.length) {
  console.error('FORBIDDEN-CSS VIOLATIONS:')
  violations.forEach((v) => console.error('  ' + v))
  process.exit(1)
}
console.log(`forbidden-css scan clean (${files.length} files, mix-blend-mode allowlisted)`)
