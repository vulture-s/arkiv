// Dev helper: node tests/i18n-remaining.mjs <rel-path>... → unwrapped Chinese lines left in those files.
import { scan } from './i18nScan.mjs'
let total = 0
for (const f of process.argv.slice(2)) {
  const { raw } = scan(f)
  total += raw.length
  for (const r of raw) console.log(`${f}:${r.line}: ${r.text}`)
}
console.log(`remaining: ${total}`)
