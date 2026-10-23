// Inspector transport keys (Resolve / Premiere layout). node:test, no runner dependency.
import test from 'node:test'
import assert from 'node:assert/strict'

import { keyAction, shuttleNext } from '../src/lib/transport.js'

const k = (code, mods = {}) => keyAction({ code, ...mods })

test('core keys', () => {
  assert.equal(k('Space'), 'toggle')
  assert.equal(k('KeyI'), 'markIn')
  assert.equal(k('KeyO'), 'markOut')
  assert.equal(k('KeyI', { shiftKey: true }), 'gotoIn')
  assert.equal(k('KeyO', { shiftKey: true }), 'gotoOut')
  assert.equal(k('KeyX', { altKey: true }), 'clearInOut')
  assert.equal(k('KeyX'), null, 'plain X is not clear')
  assert.equal(k('ArrowLeft'), 'prevFrame')
  assert.equal(k('ArrowRight', { shiftKey: true }), 'fwd1s')
  assert.equal(k('Comma'), 'prevFrame')
  assert.equal(k('Home'), 'gotoStart')
  assert.equal(k('Slash'), 'playRange')
})

test('app and OS chords are never taken', () => {
  for (const code of ['KeyI', 'KeyJ', 'Space', 'ArrowLeft', 'KeyX']) {
    assert.equal(k(code, { metaKey: true }), null, code)
    assert.equal(k(code, { ctrlKey: true }), null, code)
  }
})

test('J/K/L shuttle', () => {
  assert.equal(shuttleNext(0, 'l'), 1)
  assert.equal(shuttleNext(1, 'l'), 2)
  assert.equal(shuttleNext(4, 'l'), 8)
  assert.equal(shuttleNext(8, 'l'), 8, 'tops out')
  assert.equal(shuttleNext(0, 'j'), -1)
  assert.equal(shuttleNext(-2, 'j'), -4)
  assert.equal(shuttleNext(4, 'j'), -1, 'direction change restarts at 1x')
  assert.equal(shuttleNext(-8, 'k'), 0)
})
