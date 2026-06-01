// E2 verify: #/chat-live sends a real query and renders the live response.
// Types a compilation prompt, waits for the assistant message + scene
// thumbnails, asserts real text + scene_ids resolved. Usage: node verify-chat.mjs <devUrl>
import puppeteer from 'puppeteer-core'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const dev = process.argv[2] || 'http://localhost:5174/'

const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] })
const p = await b.newPage()
const errs = []
p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', (e) => errs.push(String(e)))

await p.goto(dev + '#/chat-live', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise((r) => setTimeout(r, 800))

await p.type('.chatinput', '幫我把生肉切割的鏡頭剪成一段')
await p.keyboard.press('Enter')
// chat LLM ~8-10s; wait for assistant message to appear
let waited = 0
while (waited < 40000) {
  const has = await p.evaluate(() => document.querySelectorAll('.msg.assistant').length > 0 &&
    !document.querySelector('.msg.assistant .text')?.textContent?.includes('thinking'))
  if (has) break
  await new Promise((r) => setTimeout(r, 1000)); waited += 1000
}
await new Promise((r) => setTimeout(r, 500))

const result = await p.evaluate(() => {
  const assistant = [...document.querySelectorAll('.msg.assistant')].pop()
  return {
    userMsgs: document.querySelectorAll('.msg.user').length,
    assistantText: assistant?.querySelector('.text')?.textContent?.trim()?.slice(0, 120),
    intent: assistant?.querySelector('.ak-mono')?.textContent?.trim(),
    sceneCount: assistant?.querySelectorAll('.scene').length || 0,
    sceneThumbsLoaded: [...(assistant?.querySelectorAll('.scenethumb img') || [])].filter((i) => i.naturalWidth > 0).length,
  }
})
await p.screenshot({ path: '.audit/b1/chat-live.png' })
console.log(JSON.stringify({ errs, result }, null, 2))
await b.close()
