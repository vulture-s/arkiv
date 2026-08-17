// Verify the sidebar's Shoot date facet against a live backend, at both widths.
//
// Two things are being checked and they fail differently:
//   1. Behaviour — a year filters and opens its days; a day narrows further; the
//      header count agrees with the sidebar row that produced it.
//   2. Layout — an expanded year lengthens a 220px column that has clipped its own
//      content before (the `.tagsec { flex-shrink: 0 }` note in PoolSidebar records
//      that bug), so the Storage footer must stay reachable and unoverlapped.
//
// Usage: node scripts/audit/verify-shot-date.mjs <baseUrl> [outDir]
import puppeteer from 'puppeteer-core'
import { mkdirSync } from 'node:fs'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const base = process.argv[2] || 'http://127.0.0.1:8599/'
const outDir = process.argv[3] || '/tmp/arkiv-shot-date'
mkdirSync(outDir, { recursive: true })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const problems = []
const report = {}

const b = await puppeteer.launch({
  executablePath: CHROME, headless: true,
  args: ['--no-sandbox', '--hide-scrollbars', '--force-color-profile=srgb'],
})

// Read the sidebar + header as the user sees them. `.pool` scrolls as one column, so
// "clipped" means content extends past its scrollHeight, and "collided" means the
// Storage block overlaps the last day row rather than flowing after it.
const probe = () => {
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null)
  const pool = document.querySelector('.pool')
  const storage = document.querySelector('.storage')
  const dayRows = [...document.querySelectorAll('.dayrow')]
  const last = dayRows[dayRows.length - 1]
  const sr = storage ? storage.getBoundingClientRect() : null
  const lr = last ? last.getBoundingClientRect() : null
  return {
    years: [...document.querySelectorAll('.yearrow')].map((r) => txt(r)),
    days: dayRows.map((r) => txt(r)),
    activeYear: txt(document.querySelector('.yearrow.activeyear')),
    activeDay: txt(document.querySelector('.dayrow.activeday')),
    more: txt(document.querySelector('.daymore')),
    header: txt(document.querySelector('.toolrow .proj')),
    cards: document.querySelectorAll('.card').length,
    // The count the clicked day row itself advertises, and the library total from the
    // Projects row — both read from the sidebar, independently of the header.
    sidebarDayCount: (() => {
      const active = document.querySelector('.dayrow.activeday')
      const n = active && /(\d+)\s*$/.exec(active.textContent.replace(/\s+/g, ' ').trim())
      return n ? Number(n[1]) : null
    })(),
    libraryTotal: (() => {
      const n = /(\d+)\s*$/.exec((document.querySelector('.projrow') || {}).textContent?.replace(/\s+/g, ' ').trim() || '')
      return n ? Number(n[1]) : null
    })(),
    // Layout facts
    poolScrollH: pool ? pool.scrollHeight : 0,
    poolClientH: pool ? pool.clientHeight : 0,
    poolOverflowsViewport: pool ? pool.scrollWidth > pool.clientWidth + 1 : false,
    storageVisible: !!sr && sr.height > 0,
    storageBelowLastDay: sr && lr ? sr.top >= lr.bottom - 1 : null,
  }
}

for (const width of [1440, 900]) {
  const p = await b.newPage()
  const errs = []
  p.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text())
  })
  p.on('pageerror', (e) => errs.push(String(e)))
  await p.setViewport({ width, height: 900, deviceScaleFactor: 1 })
  await p.goto(base + '#/main-live', { waitUntil: 'networkidle0', timeout: 30000 })
  await sleep(1500)

  const initial = await p.evaluate(probe)
  if (!initial.years.length) problems.push(`${width}: no Shoot date rows rendered`)
  if (initial.days.length) problems.push(`${width}: days visible before any year was opened`)
  await p.screenshot({ path: `${outDir}/${width}-01-collapsed.png`, fullPage: false })

  // Open 2025 — the year with 34 shoot days, i.e. past DAY_CAP.
  await p.evaluate(() => {
    const row = [...document.querySelectorAll('.yearrow')].find((r) => r.textContent.includes('2025'))
    row.click()
  })
  await sleep(1800)
  const opened = await p.evaluate(probe)
  await p.screenshot({ path: `${outDir}/${width}-02-year-open.png`, fullPage: false })

  if (opened.days.length !== 20) problems.push(`${width}: expected DAY_CAP=20 day rows, got ${opened.days.length}`)
  if (!/更多/.test(opened.more || '')) problems.push(`${width}: no 更多 affordance for the capped days`)
  // The sidebar row for 2025 says 54, and the fixture is small enough that all 54 are
  // on one page — so the header must agree with the row that produced the view. This
  // number is deliberately hardcoded to the fixture rather than derived from the page,
  // because deriving it from what is rendered is what makes such a check vacuous.
  if (!/54 \/ 62 items/.test(opened.header || '')) {
    problems.push(`${width}: header did not reconcile with the sidebar — got "${opened.header}"`)
  }
  if (opened.storageBelowLastDay === false) problems.push(`${width}: Storage footer overlaps the day list`)
  if (opened.poolOverflowsViewport) problems.push(`${width}: sidebar overflows horizontally`)

  // Expand the rest, the worst case for column height.
  await p.evaluate(() => document.querySelector('.daymore').click())
  await sleep(600)
  const expanded = await p.evaluate(probe)
  await p.screenshot({ path: `${outDir}/${width}-03-days-expanded.png`, fullPage: false })
  if (expanded.days.length !== 34) problems.push(`${width}: expected all 34 days, got ${expanded.days.length}`)
  if (expanded.storageBelowLastDay === false) problems.push(`${width}: Storage footer overlaps the expanded day list`)

  // Scroll the sidebar to the bottom — Storage must be reachable, not clipped away.
  const reachedBottom = await p.evaluate(() => {
    const pool = document.querySelector('.pool')
    pool.scrollTop = pool.scrollHeight
    const sr = document.querySelector('.storage').getBoundingClientRect()
    const pr = pool.getBoundingClientRect()
    return sr.bottom <= pr.bottom + 2 && sr.top >= pr.top - 2
  })
  if (!reachedBottom) problems.push(`${width}: Storage footer not reachable by scrolling the sidebar`)
  await p.screenshot({ path: `${outDir}/${width}-04-scrolled-bottom.png`, fullPage: false })

  // Pick a single day.
  await p.evaluate(() => {
    const pool = document.querySelector('.pool'); pool.scrollTop = 0
    document.querySelector('.dayrow').click()
  })
  await sleep(1800)
  const dayPicked = await p.evaluate(probe)
  await p.screenshot({ path: `${outDir}/${width}-05-day-picked.png`, fullPage: false })
  if (!dayPicked.activeDay) problems.push(`${width}: picking a day left no active row`)
  if (dayPicked.cards >= opened.cards) {
    problems.push(`${width}: a day did not narrow the grid (${opened.cards} → ${dayPicked.cards})`)
  }
  const m = /(\d+) \/ (\d+) items/.exec(dayPicked.header || '')
  if (!m) problems.push(`${width}: header lost its filtered count on the day view`)
  else {
    // The header reports what is rendered, so numerator==cards is true by
    // construction and worth nothing on its own. What is worth checking is that both
    // agree with the SIDEBAR row that was clicked — three independent paths to the
    // same number — and that the denominator stayed the library, not the subset.
    if (Number(m[1]) !== dayPicked.sidebarDayCount) {
      problems.push(`${width}: day row says ${dayPicked.sidebarDayCount}, header says ${m[1]}`)
    }
    if (Number(m[1]) !== dayPicked.cards) {
      problems.push(`${width}: header says ${m[1]} but ${dayPicked.cards} cards are shown`)
    }
    if (Number(m[2]) !== dayPicked.libraryTotal) {
      problems.push(`${width}: denominator ${m[2]} is not the library total ${dayPicked.libraryTotal}`)
    }
  }

  // The audit finding this exists to catch: a client-side filter narrows the grid
  // without changing the server total, so a header reading the server total ends up
  // contradicting the cards beneath it. Back to the whole year first (54 rows
  // server-side), then stack a rating filter on top.
  await p.evaluate(() => {
    document.querySelector('.dayrow.activeday').click() // release the day, keep 2025
  })
  await sleep(1500)
  await p.evaluate(() => {
    const row = [...document.querySelectorAll('.poolrow')].find((r) => r.textContent.includes('Rated good'))
    row.click()
  })
  await sleep(900)
  const withRating = await p.evaluate(probe)
  await p.screenshot({ path: `${outDir}/${width}-06-year-plus-rating.png`, fullPage: false })
  if (withRating.cards >= 54 || withRating.cards === 0) {
    // Guards the guard: if the rating filter removed nothing, the assertion below
    // would pass without the contradiction ever being possible.
    problems.push(`${width}: rating filter did not narrow the year (${withRating.cards} cards) — check is vacuous`)
  }
  const m2 = /(\d+) \/ (\d+) items/.exec(withRating.header || '')
  if (!m2) problems.push(`${width}: header lost its count once a rating filter was added`)
  else if (Number(m2[1]) !== withRating.cards) {
    problems.push(`${width}: with a client-side filter the header says ${m2[1]} but ${withRating.cards} cards render`)
  }

  if (errs.length) problems.push(`${width}: console errors ${JSON.stringify(errs)}`)
  report[width] = { initial, opened, expanded, dayPicked, withRating, errs }
  await p.close()
}

await b.close()
console.log(JSON.stringify({ problems, report }, null, 2))
process.exit(problems.length ? 1 : 0)
