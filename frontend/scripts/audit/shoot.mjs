// Audit screenshot + console capture — drives the system Chrome via
// puppeteer-core (no Chromium download). Used by the overnight loop's
// Tier-A (console==0) and Tier-B (port PNG) gates.
//
//   node scripts/audit/shoot.mjs <url> <out.png> [console.json]
//
// Exit 0 = no console.error / pageerror. Exit 1 = errors (gate fail).
// Exit 2 = bad usage. requestfailed (e.g. favicon) is logged, not gated.
import puppeteer from 'puppeteer-core'
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const [, , url, outPng, consoleJson] = process.argv
if (!url || !outPng) {
  console.error('usage: shoot.mjs <url> <out.png> [console.json]')
  process.exit(2)
}

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--hide-scrollbars', '--force-color-profile=srgb'],
})
const page = await browser.newPage()
await page.setViewport({ width: 1400, height: 900, deviceScaleFactor: 2 })

const isFavicon = (u) => /favicon\.ico(\?|$)/.test(u)
const msgs = []
page.on('console', (m) => {
  const t = m.type()
  if (t !== 'error' && t !== 'warning') return
  const text = m.text()
  // "Failed to load resource" console errors carry no URL; they duplicate the
  // response-status tracking below (which is URL-aware + favicon-tolerant).
  if (/Failed to load resource/.test(text)) return
  msgs.push({ type: t, text })
})
page.on('pageerror', (e) => msgs.push({ type: 'pageerror', text: String(e) }))
page.on('response', (r) => {
  const s = r.status()
  const u = r.url()
  if (s >= 400 && !isFavicon(u)) msgs.push({ type: 'badresponse', text: `${s} ${u}` })
})
page.on('requestfailed', (r) => {
  const u = r.url()
  if (isFavicon(u)) return // benign
  msgs.push({ type: 'requestfailed', text: `${u} ${r.failure()?.errorText || ''}` })
})

try {
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 30000 })
} catch (e) {
  msgs.push({ type: 'pageerror', text: `goto failed: ${e}` })
}
await new Promise((r) => setTimeout(r, 400)) // let fonts/animation settle

mkdirSync(dirname(outPng), { recursive: true })
await page.screenshot({ path: outPng, fullPage: true })
await browser.close()

if (consoleJson) {
  mkdirSync(dirname(consoleJson), { recursive: true })
  writeFileSync(consoleJson, JSON.stringify(msgs, null, 2))
}

const hard = msgs.filter(
  (m) => m.type === 'error' || m.type === 'pageerror' || m.type === 'badresponse' || m.type === 'requestfailed'
)
console.log(`shot ${outPng} — ${hard.length} hard error(s), ${msgs.length} msg(s) total`)
for (const m of msgs) console.log(`  [${m.type}] ${m.text}`)
process.exit(hard.length ? 1 : 0)
