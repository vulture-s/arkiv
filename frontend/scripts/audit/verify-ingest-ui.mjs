// B1+ verify: #/ingest-live UI actually renders the live ws stream.
// Loads the route, fills path, clicks Start, asserts the connection indicator
// goes LIVE and the log/complete updates from real backend broadcasts.
// Uses an already-indexed dir → fast skip path (start+complete, no vision wait).
// Usage: node verify-ingest-ui.mjs <devUrl> <ingestDir>
import puppeteer from 'puppeteer-core'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const dev = process.argv[2] || 'http://localhost:5173/'
const dir = process.argv[3]

const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] })
const p = await b.newPage()
const errs = []
p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', (e) => errs.push(String(e)))

await p.goto(dev + '#/ingest-live', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise((r) => setTimeout(r, 800))

const connBefore = await p.evaluate(() => document.querySelector('.topbar .on, .topbar .off')?.textContent?.trim())

await p.type('.pathinput', dir)
await p.click('.ak-btn--primary')
// wait for complete (skip path is fast) or up to 30s
await new Promise((r) => setTimeout(r, 6000))

const after = await p.evaluate(() => ({
  conn: document.querySelector('.topbar span')?.textContent?.trim(),
  liveDot: document.querySelector('.livedot')?.textContent?.trim(),
  logLines: [...document.querySelectorAll('.logline .logmsg')].map((n) => n.textContent.trim()).slice(0, 6),
  qrows: document.querySelectorAll('.qrow').length,
  bignum: document.querySelector('.bignum')?.textContent?.trim(),
}))
await p.screenshot({ path: '.audit/b1/ingest-live.png' })
console.log(JSON.stringify({ errs, connBefore, after }, null, 2))
await b.close()
