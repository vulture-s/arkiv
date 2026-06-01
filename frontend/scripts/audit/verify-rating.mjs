// C verify: rate button on #/main-live writes through to the backend DB.
// Clicks "Good" on the selected clip, asserts the button activates AND
// GET /api/media/{id} reflects rating='good'. Then clears it back.
// Usage: node verify-rating.mjs <devUrl> <backendBase>
import puppeteer from 'puppeteer-core'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const dev = process.argv[2] || 'http://localhost:5173/'
const api = process.argv[3] || 'http://127.0.0.1:8501'

const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] })
const p = await b.newPage()
const errs = []
p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', (e) => errs.push(String(e)))

await p.goto(dev + '#/main-live', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise((r) => setTimeout(r, 1500)) // detail fetch

// which clip is selected? read inspector filename
const fname = await p.evaluate(() => document.querySelector('.fname')?.textContent?.trim())

// find the "Good" rate button (first .ratebtn) and click it
const before = await p.evaluate(() => {
  const btns = [...document.querySelectorAll('.ratebtn')]
  return { count: btns.length, activeText: btns.find((b) => b.classList.contains('active'))?.textContent?.trim() || null }
})
await p.evaluate(() => [...document.querySelectorAll('.ratebtn')].find((b) => b.textContent.trim() === 'Good')?.click())
await new Promise((r) => setTimeout(r, 800)) // let PATCH resolve

const afterUi = await p.evaluate(() => {
  const active = [...document.querySelectorAll('.ratebtn')].find((b) => b.classList.contains('active'))
  return active?.textContent?.trim() || null
})

// read DB truth via API — find the media id by filename
const list = await (await fetch(`${api}/api/media?limit=60`)).json()
const item = (list.items || []).find((i) => i.filename === fname || i.name === fname)
const dbRating = item ? item.rating : '(not found)'

// cleanup: clear rating back to null on that id
let cleared = null
if (item) {
  const res = await fetch(`${api}/api/media/${item.id}/rating`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating: null }),
  })
  cleared = res.ok
}

console.log(JSON.stringify({
  errs, selectedClip: fname, beforeActive: before.activeText,
  afterUiActive: afterUi, dbRatingAfterClick: dbRating, cleanedUp: cleared,
}, null, 2))
await b.close()
