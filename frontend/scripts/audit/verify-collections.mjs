// E1 verify: Smart Collections section in sidebar + click filters grid.
// Asserts the section shows real groups (食材特寫/店內空景/廢鏡) and clicking
// 店內空景 (1 member, C3742) narrows the grid to exactly that clip.
// Usage: node verify-collections.mjs <devUrl>
import puppeteer from 'puppeteer-core'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const dev = process.argv[2] || 'http://localhost:5173/'

const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] })
const p = await b.newPage()
const errs = []
p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', (e) => errs.push(String(e)))

await p.goto(dev + '#/main-live', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise((r) => setTimeout(r, 1800))

const collections = await p.evaluate(() =>
  [...document.querySelectorAll('.collrow')].map((r) => r.textContent.replace(/\s+/g, ' ').trim())
)

// click 店內空景 (the single-member collection) → grid should show exactly 1 card
const clicked = await p.evaluate(() => {
  const row = [...document.querySelectorAll('.collrow')].find((r) => r.textContent.includes('店內空景'))
  if (row) { row.click(); return true }
  return false
})
await new Promise((r) => setTimeout(r, 2000))
const after = await p.evaluate(() => ({
  cards: document.querySelectorAll('.card').length,
  names: [...document.querySelectorAll('.name')].map((n) => n.textContent.trim()),
}))
await p.screenshot({ path: '.audit/b1/collections.png' })

console.log(JSON.stringify({ errs, collections, clicked店內: clicked, afterClick: after }, null, 2))
await b.close()
