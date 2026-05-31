// B1 verify: main-live grid + live inspector + search. DOM-asserts real data,
// captures screenshots. Usage: node verify-live.mjs <baseUrl>
import puppeteer from 'puppeteer-core'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const base = process.argv[2] || 'http://localhost:5173/'

const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] })
const p = await b.newPage()
await p.setViewport({ width: 1400, height: 900, deviceScaleFactor: 2 })
const errs = []
p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', (e) => errs.push(String(e)))

await p.goto(base + '#/main-live', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise((r) => setTimeout(r, 1200)) // let detail fetch resolve

const def = await p.evaluate(() => {
  const cards = [...document.querySelectorAll('.card')]
  const imgs = [...document.querySelectorAll('.thumbimg')].map((i) => i.naturalWidth)
  const fname = document.querySelector('.fname')?.textContent
  const previmg = document.querySelector('.previmg')?.naturalWidth || 0
  const eyebrows = [...document.querySelectorAll('.inspector .ak-eyebrow, .inspector [class*=eyebrow]')].map((e) => e.textContent.trim())
  const lines = [...document.querySelectorAll('.inspector .ttext')].map((t) => t.textContent.trim()).slice(0, 6)
  const blocks = [...document.querySelectorAll('.inspector')].length
  return { cards: cards.length, thumbsDecoded: imgs.filter((w) => w > 0).length, fname, previmgW: previmg, eyebrows, lines, hasInspector: blocks }
})
await p.screenshot({ path: '.audit/b1/live-default.png' })

// search
await p.type('.livesearch', '餐廳')
await p.keyboard.press('Enter')
await new Promise((r) => setTimeout(r, 1500))
const srch = await p.evaluate(() => {
  const cards = [...document.querySelectorAll('.card')]
  const names = [...document.querySelectorAll('.name')].map((n) => n.textContent.trim())
  return { cards: cards.length, names }
})
await p.screenshot({ path: '.audit/b1/live-search.png' })

console.log(JSON.stringify({ errs, def, srch }, null, 2))
await b.close()
