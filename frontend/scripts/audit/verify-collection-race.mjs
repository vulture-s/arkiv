// Deterministic repro for the load race the Codex audit of #292 found: picking a Smart
// Collection while a search is still in flight left the sidebar highlighting the
// collection and the grid repainting with the search results a moment later.
//
// Unlike the other verify-* scripts this one ASSERTS and exits non-zero, because a race
// is not something you can eyeball off a screenshot. The interleaving is forced rather
// than raced for: request interception holds the /api/media?q= response open while the
// collection click goes through, so the "slow response lands last" ordering happens
// every run instead of on an unlucky one.
//
// Needs a backend + `npm run dev`. Point it at a library where a collection's membership
// differs from the whole library (otherwise both outcomes look identical):
//   node verify-collection-race.mjs http://localhost:5173/ a_roll
import puppeteer from 'puppeteer-core'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const dev = process.argv[2] || 'http://localhost:5173/'
const collectionKey = process.argv[3] || 'a_roll'
const HOLD_MS = 3000

const fail = []
const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] })
const p = await b.newPage()
const errs = []
p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', (e) => errs.push(String(e)))

await p.goto(dev + '#/main-live', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise((r) => setTimeout(r, 1800))

// Read the collection's expected membership straight from the API the sidebar renders,
// so the assertion compares against the server's answer rather than a hardcoded count.
const target = await p.evaluate(async (key) => {
  const r = await fetch('/api/collections')
  const d = await r.json()
  const c = (d.collections || []).find((x) => x.key === key)
  return c ? { title: c.title, count: c.count, names: (c.items || []).map((i) => i.filename) } : null
}, collectionKey)
if (!target) { console.error(`collection ${collectionKey} not present — nothing to test`); await b.close(); process.exit(2) }

const libraryCount = await p.evaluate(() => document.querySelectorAll('.card').length)
if (libraryCount <= target.count) {
  console.error(`library (${libraryCount}) must be larger than the collection (${target.count}) or the race is unobservable`)
  await b.close(); process.exit(2)
}

// Hold the search response open. Everything else passes through untouched.
await p.setRequestInterception(true)
let heldSearch = 0
p.on('request', (req) => {
  const u = req.url()
  if (/\/api\/media\?/.test(u) && /[?&]q=/.test(u)) {
    heldSearch++
    setTimeout(() => { try { req.continue() } catch { /* page gone */ } }, HOLD_MS)
    return
  }
  try { req.continue() } catch { /* page gone */ }
})

// Fire a search, then pick the collection while the response is still held.
await p.evaluate(() => {
  const box = document.querySelector('input.livesearch')
  if (!box) throw new Error('search input not found')
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(box, '唱片')
  box.dispatchEvent(new Event('input', { bubbles: true }))
  box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
})
await new Promise((r) => setTimeout(r, 300))

const clicked = await p.evaluate((title) => {
  const row = [...document.querySelectorAll('.collrow')].find((r) => r.textContent.includes(title))
  if (!row) return false
  row.click()
  return true
}, target.title)
if (!clicked) { console.error(`sidebar row for ${target.title} not found`); await b.close(); process.exit(2) }

// The collection is synchronous, so the grid is already correct here. The bug shows up
// only once the held search response finally lands.
const beforeLanding = await p.evaluate(() => ({
  cards: document.querySelectorAll('.card').length,
  highlighted: !!document.querySelector('.collrow.activecoll'),
}))
await new Promise((r) => setTimeout(r, HOLD_MS + 1500))

const after = await p.evaluate(() => ({
  cards: document.querySelectorAll('.card').length,
  names: [...document.querySelectorAll('.name')].map((n) => n.textContent.trim()),
  highlighted: [...document.querySelectorAll('.collrow.activecoll')].map((r) => r.textContent.replace(/\s+/g, ' ').trim()),
}))

if (heldSearch === 0) fail.push('no search request was intercepted — the interleaving never happened, so a pass here means nothing')
if (beforeLanding.cards !== target.count) fail.push(`right after the click the grid showed ${beforeLanding.cards} cards, expected ${target.count}`)
if (after.cards !== target.count) fail.push(`after the held search landed the grid showed ${after.cards} cards, expected the collection's ${target.count}`)
if (after.highlighted.length !== 1) fail.push(`expected exactly one highlighted collection row, saw ${after.highlighted.length}`)
const missing = target.names.filter((n) => !after.names.includes(n))
if (missing.length) fail.push(`collection members missing from the grid: ${missing.join(', ')}`)
if (errs.length) fail.push(`console errors: ${errs.join(' | ')}`)

console.log(JSON.stringify({
  collection: collectionKey, title: target.title, expected: target.count,
  libraryCount, heldSearch, beforeLanding, after, fail,
}, null, 2))
await b.close()
if (fail.length) { console.error(`\nFAIL (${fail.length})\n` + fail.map((f) => '  - ' + f).join('\n')); process.exit(1) }
console.log('\nPASS — the superseded search did not repaint the collection view')
