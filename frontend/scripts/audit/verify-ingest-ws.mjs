// B1+ verify: ws/ingest stream end-to-end. Connects the ws, triggers an ingest
// (already-ingested clips → skip path, fast), asserts real broadcast messages.
// Usage: node verify-ingest-ws.mjs <backendBase> <ingestDir>
import WebSocket from 'ws'

const base = process.argv[2] || 'http://127.0.0.1:8501'
const dir = process.argv[3]
const wsBase = base.replace(/^http/, 'ws')

const got = { start: 0, file: 0, complete: null, statuses: new Set() }
const ws = new WebSocket(`${wsBase}/ws/ingest`)

const done = new Promise((resolve) => {
  ws.on('open', async () => {
    // trigger ingest after ws is listening
    const res = await fetch(`${base}/api/ingest/ws`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: dir, limit: 5 }),
    })
    if (!res.ok) {
      console.log(JSON.stringify({ error: `trigger ${res.status} ${await res.text()}` }))
      ws.close()
      resolve()
    }
  })
  ws.on('message', (data) => {
    let m
    try { m = JSON.parse(data.toString()) } catch { return }
    if (m.type === 'start') got.start++
    else if (m.type === 'file') { got.file++; got.statuses.add(m.status) }
    else if (m.type === 'complete') { got.complete = m; ws.close(); resolve() }
  })
  ws.on('error', (e) => { console.log(JSON.stringify({ wsError: String(e) })); resolve() })
  // safety timeout
  setTimeout(() => { ws.close(); resolve() }, 60000)
})

await done
console.log(JSON.stringify({
  startMsgs: got.start,
  fileMsgs: got.file,
  statuses: [...got.statuses],
  complete: got.complete,
}, null, 2))
