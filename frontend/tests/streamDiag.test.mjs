// A <video> error is not evidence about the codec — node:test, no runner dep.
//
// #428 gave `/api/stream` a 409 {need_proxy} for codecs no browser decodes, and
// the Inspector rendered its "build a proxy" panel straight off `on:error`. But
// that event fires for every load failure, so an expired token and a deleted
// file both arrived on screen as "此編碼瀏覽器播不了" plus a button offering to
// transcode. The user is sent to fix the one thing that is not wrong.
//
// These pin the branches apart, including the one that must NOT change: when the
// endpoint serves the bytes and the player still fails, it really is the codec.
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  diagnosePlaybackFailure, playbackFailureMessage,
  CODEC, FORBIDDEN, MISSING, SERVER, UNREACHABLE,
} from '../src/lib/streamDiag.js'

const reply = (status, body = null, ok = status >= 200 && status < 300) => async () => ({
  status,
  ok,
  json: async () => {
    if (body === null) throw new Error('no body')
    return body
  },
})

test('a 409 keeps the codec panel and shows the endpoint\'s own reason', async () => {
  const d = await diagnosePlaybackFailure('/api/stream/1',
    reply(409, { need_proxy: true, reason: 'dnxhd is not playable in a browser' }))
  assert.equal(d.kind, CODEC)
  assert.equal(d.canProxy, true)
  assert.match(playbackFailureMessage(d), /dnxhd/)
})

test('a 401 is not a codec problem and offers no proxy', async () => {
  const d = await diagnosePlaybackFailure('/api/stream/1', reply(401))
  assert.equal(d.kind, FORBIDDEN)
  assert.equal(d.canProxy, false)
  // The misdirection is the bug: the old panel told this user to transcode.
  assert.ok(!playbackFailureMessage(d).includes('編碼瀏覽器播不了'),
    playbackFailureMessage(d))
})

test('a 403 lands in the same place as a 401', async () => {
  const d = await diagnosePlaybackFailure('/api/stream/1', reply(403))
  assert.equal(d.kind, FORBIDDEN)
  assert.equal(d.canProxy, false)
})

test('a 404 says the file is gone, not that it is unplayable', async () => {
  const d = await diagnosePlaybackFailure('/api/stream/1', reply(404))
  assert.equal(d.kind, MISSING)
  assert.equal(d.canProxy, false)
  assert.match(playbackFailureMessage(d), /找不到/)
})

test('a backend that never answers is unreachable, not unplayable', async () => {
  const d = await diagnosePlaybackFailure('/api/stream/1', async () => {
    throw new TypeError('Failed to fetch')
  })
  assert.equal(d.kind, UNREACHABLE)
  assert.equal(d.status, 0)
  assert.equal(d.canProxy, false)
})

test('a 500 is reported as the server\'s error, with its status', async () => {
  const d = await diagnosePlaybackFailure('/api/stream/1', reply(500))
  assert.equal(d.kind, SERVER)
  assert.equal(d.canProxy, false)
  assert.match(playbackFailureMessage(d), /500/)
})

test('bytes served but playback still failed IS the codec — do not regress this', async () => {
  // The case the panel was built for (#420). Widening the diagnosis must not
  // take the proxy offer away from the only situation where it helps.
  const d = await diagnosePlaybackFailure('/api/stream/1', reply(206, null, true))
  assert.equal(d.kind, CODEC)
  assert.equal(d.canProxy, true)
})

test('a 409 whose body will not parse is still a refusal, just a quieter one', async () => {
  const d = await diagnosePlaybackFailure('/api/stream/1', reply(409))
  assert.equal(d.kind, CODEC)
  // No body means no claim that a proxy helps — the button is the endpoint's to
  // offer, and it did not.
  assert.equal(d.canProxy, false)
})

test('a 409 that does not claim need_proxy gets no proxy button', async () => {
  // Guards the difference between "the endpoint refused" and "a proxy fixes it".
  const d = await diagnosePlaybackFailure('/api/stream/1',
    reply(409, { need_proxy: false, reason: 'source is offline' }))
  assert.equal(d.kind, CODEC)
  assert.equal(d.canProxy, false)
})

test('the probe asks for one byte, or it downloads the whole original', async () => {
  // Load-bearing: /api/stream serves the ORIGINAL for a playable codec. Without
  // Range this diagnostic pulls a 4K file down to ask a question about it.
  let seen = null
  await diagnosePlaybackFailure('/api/stream/1', async (_url, opts) => {
    seen = opts
    return { status: 404, ok: false, json: async () => { throw new Error('x') } }
  })
  assert.equal(seen.headers.Range, 'bytes=0-0')
  assert.equal(seen.cache, 'no-store')
})
