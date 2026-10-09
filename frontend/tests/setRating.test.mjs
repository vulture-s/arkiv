// Rating PATCH must not carry a note it was not asked to write — node:test.
//
// Audit 2026-10-09 K1: the backend is PATCH (omitted = untouched, explicit null =
// clear; routers/media.py _sent). The inspector rate buttons sent
// `note: detailLive?.rating_note ?? null`, i.e. an explicit null whenever the
// clip's detail had not loaded yet (or failed to) — wiping a DIT note that
// camera_report prints into the deliverable.
import test from 'node:test'
import assert from 'node:assert/strict'

import { setRating } from '../src/lib/api.js'

function captureFetch() {
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  return calls
}

test('rating without a note omits the note field entirely', async () => {
  const calls = captureFetch()
  await setRating(5, 'good')
  const body = JSON.parse(calls[0].init.body)
  assert.deepEqual(body, { rating: 'good' })
  assert.ok(!('note' in body), 'note must be omitted, not sent as null')
})

test('an explicit note (including null to clear) is still sent', async () => {
  const calls = captureFetch()
  await setRating(5, 'ng', 'soft focus')
  assert.deepEqual(JSON.parse(calls[0].init.body), { rating: 'ng', note: 'soft focus' })
  await setRating(5, 'ng', null)
  assert.deepEqual(JSON.parse(calls[1].init.body), { rating: 'ng', note: null })
})
