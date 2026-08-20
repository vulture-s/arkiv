// Svelte 5 遷移的渲染驗證。斷言的是「元件真的掛載、路由真的換得動」——
// 那正是 build/svelte-check/node:test 三個綠燈都沒有在看的東西。
import puppeteer from 'puppeteer-core'
import { mkdirSync } from 'node:fs'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const BASE = process.argv[2] || 'http://127.0.0.1:8599'
const OUT = process.argv[3] || '/tmp/svelte5'
mkdirSync(OUT, { recursive: true })

const ROUTES = ['/', '/settings', '/bins', '/offload', '/_design/gallery']
const WIDTHS = [1440, 900]
const problems = []

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: true,
  args: ['--no-sandbox', '--hide-scrollbars', '--force-color-profile=srgb'],
})

for (const width of WIDTHS) {
  for (const route of ROUTES) {
    const page = await browser.newPage()
    await page.setViewport({ width, height: 900, deviceScaleFactor: 1 })
    const errs = []
    page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
    page.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message))

    await page.goto(`${BASE}/#${route}`, { waitUntil: 'networkidle2', timeout: 30000 })
    await new Promise((r) => setTimeout(r, 900))

    const probe = await page.evaluate(() => {
      const app = document.getElementById('app')
      return {
        mounted: !!app && app.childElementCount > 0,
        elements: app ? app.querySelectorAll('*').length : 0,
        text: (document.body.innerText || '').trim().slice(0, 90).replace(/\s+/g, ' '),
      }
    })

    const tag = `${width}-${route.replace(/\W+/g, '_') || 'root'}`
    await page.screenshot({ path: `${OUT}/${tag}.png` })

    const bad = []
    if (!probe.mounted) bad.push('NOT MOUNTED')
    if (probe.elements < 10) bad.push(`only ${probe.elements} elements`)
    // 忽略沒有 token 造成的 401/資源載入雜訊，那不是 Svelte 5 的事
    const real = errs.filter((e) => !/401|Failed to load resource|favicon/i.test(e))
    if (real.length) bad.push(`console: ${real.slice(0, 2).join(' | ')}`)

    console.log(
      `${bad.length ? 'FAIL' : 'ok  '} ${String(width).padEnd(5)} ${route.padEnd(18)} ` +
      `els=${String(probe.elements).padEnd(4)} "${probe.text}"` + (bad.length ? `  <<< ${bad.join('; ')}` : '')
    )
    if (bad.length) problems.push(`${width}${route}: ${bad.join('; ')}`)
    await page.close()
  }
}

await browser.close()
console.log(problems.length ? `\n${problems.length} PROBLEM(S)` : '\nALL ROUTES MOUNTED AND RENDERED')
process.exit(problems.length ? 1 : 0)
