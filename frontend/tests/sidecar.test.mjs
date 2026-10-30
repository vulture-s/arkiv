import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fetchSidecar } from '../src/lib/sidecar.js'

// A request that never answers unless aborted — the shape /api/entitlements had.
const hang = ({ signal }) => new Promise((_, reject) => {
  signal.addEventListener('abort', () => reject(new Error('aborted')))
})

test('a hung sidecar resolves to null after its timeout and aborts the request', async () => {
  let sig
  const t0 = Date.now()
  const r = await fetchSidecar((o) => { sig = o.signal; return hang(o) }, 50)
  assert.equal(r, null)
  assert.equal(sig.aborted, true)
  assert.ok(Date.now() - t0 < 1000)
})

test('a sidecar passes through a value and nulls an error', async () => {
  assert.deepEqual(await fetchSidecar(async () => ({ pro: true }), 50), { pro: true })
  assert.equal(await fetchSidecar(async () => { throw new Error('x') }, 50), null)
  assert.equal(await fetchSidecar(() => { throw new Error('sync') }, 50), null)
})

test('the pool load does not await entitlements', () => {
  const src = readFileSync(new URL('../src/routes/MainLive.svelte', import.meta.url), 'utf8')
  const body = src.slice(src.indexOf('async function load()'), src.indexOf('async function loadMore()'))
  const all = body.slice(body.indexOf('await Promise.all(['), body.indexOf('])', body.indexOf('await Promise.all([')))
  assert.ok(all.length > 0, 'pool Promise.all not found')
  assert.ok(!all.includes('getEntitlements'), 'entitlements is back inside the pool Promise.all')
  assert.match(body, /fetchSidecar\(api\.getEntitlements\)/)
})
