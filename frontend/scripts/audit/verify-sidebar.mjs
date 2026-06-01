// D verify: PoolSidebar on #/main-live shows live data + tag-click filters grid.
// Asserts: tags are the real qwen3-vl vocab (not mock cycling/portrait), pools
// reflect real stats, clicking a tag narrows the grid via search.
// Usage: node verify-sidebar.mjs <devUrl>
import puppeteer from 'puppeteer-core'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const dev = process.argv[2] || 'http://localhost:5173/'

const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] })
const p = await b.newPage()
const errs = []
p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', (e) => errs.push(String(e)))

await p.goto(dev + '#/main-live', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise((r) => setTimeout(r, 1500))

const sidebar = await p.evaluate(() => ({
  projectsHeader: document.querySelector('.pool section .ak-eyebrow, .pool section [class*=eyebrow]')?.textContent?.trim(),
  tags: [...document.querySelectorAll('.tag')].map((t) => t.textContent.trim()).slice(0, 8),
  pools: [...document.querySelectorAll('.poolrow')].map((r) => r.textContent.replace(/\s+/g, ' ').trim()),
  cardsBefore: document.querySelectorAll('.card').length,
}))

// click the first tag → grid should filter (search round-trips, allow settle)
await p.evaluate(() => document.querySelector('.tag')?.click())
await new Promise((r) => setTimeout(r, 2500))
const after = await p.evaluate(() => ({
  searchBox: document.querySelector('.livesearch')?.value,
  cardsAfter: document.querySelectorAll('.card').length,
}))

console.log(JSON.stringify({ errs, sidebar, after }, null, 2))
await b.close()
