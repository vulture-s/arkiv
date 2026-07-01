// Render the social-preview card to a fixed-size PNG via system Chrome
// (puppeteer-core, no Chromium download). Sibling of audit/shoot.mjs,
// pinned to the 1280x640 GitHub social-preview size (GitHub rejects larger
// dimensions with "can't process that picture"). Set SCALE=2 for a 2560x1280
// @2x render (e.g. README hero) — not accepted by GitHub social preview.
//
//   node scripts/render-social.mjs [in.html] [out.png]
//   SCALE=2 node scripts/render-social.mjs
//
// Defaults: ../social-preview.html  ->  ../social_preview.png
import puppeteer from 'puppeteer-core'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const REPO = resolve(process.cwd(), '..') // frontend/ -> repo root
const inHtml = resolve(process.argv[2] || `${REPO}/social-preview.html`)
const outPng = resolve(process.argv[3] || `${REPO}/social_preview.png`)

const W = 1280
const H = 640
const SCALE = Number(process.env.SCALE) || 1 // GitHub social preview needs 1x (1280x640)

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--hide-scrollbars', '--force-color-profile=srgb'],
})
const page = await browser.newPage()
await page.setViewport({ width: W, height: H, deviceScaleFactor: SCALE })

const msgs = []
page.on('console', (m) => {
  const t = m.type()
  if ((t === 'error' || t === 'warning') && !/Failed to load resource/.test(m.text())) {
    msgs.push({ type: t, text: m.text() })
  }
})
page.on('pageerror', (e) => msgs.push({ type: 'pageerror', text: String(e) }))

await page.goto(pathToFileURL(inHtml).href, { waitUntil: 'networkidle0', timeout: 30000 })
await page.evaluate(() => document.fonts.ready)
await new Promise((r) => setTimeout(r, 400)) // let fonts/grain settle

mkdirSync(dirname(outPng), { recursive: true })
await page.screenshot({ path: outPng, clip: { x: 0, y: 0, width: W, height: H } })
await browser.close()

const hard = msgs.filter((m) => m.type === 'error' || m.type === 'pageerror')
console.log(`rendered ${outPng} @ ${W * SCALE}x${H * SCALE} — ${hard.length} hard error(s), ${msgs.length} msg(s)`)
for (const m of msgs) console.log(`  [${m.type}] ${m.text}`)
process.exit(hard.length ? 1 : 0)
