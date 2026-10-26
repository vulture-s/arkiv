import test from 'node:test'
import assert from 'node:assert/strict'
import { rateMany } from '../src/lib/bulkRating.js'

test('rates every id with the same value', async () => {
  const calls = []
  const r = await rateMany([1, 2, 3], 'good', async (id, v) => { calls.push([id, v]) })
  assert.deepEqual(calls.sort(), [[1, 'good'], [2, 'good'], [3, 'good']])
  assert.deepEqual(r.ok.sort(), [1, 2, 3])
  assert.deepEqual(r.failed, [])
})

test('one failure does not stop the rest, and names the failed id', async () => {
  const r = await rateMany([1, 2, 3, 4], 'ng', async (id) => { if (id === 2) throw new Error('403') })
  assert.deepEqual(r.ok.sort(), [1, 3, 4])
  assert.deepEqual(r.failed, [{ id: 2, error: '403' }])
})

test('null clears (passed through, not dropped)', async () => {
  const seen = []
  await rateMany([7], null, async (_id, v) => { seen.push(v) })
  assert.deepEqual(seen, [null])
})

test('concurrency is bounded', async () => {
  let live = 0, peak = 0
  await rateMany([...Array(20).keys()], 'good', async () => {
    live++; peak = Math.max(peak, live)
    await new Promise((r) => setTimeout(r, 2))
    live--
  }, { concurrency: 4 })
  assert.equal(peak, 4)
})
