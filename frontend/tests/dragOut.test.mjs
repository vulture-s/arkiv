// 12.6 drag-out helpers. node:test, no runner dependency.
import test from 'node:test'
import assert from 'node:assert/strict'

import { canDragOut, sourceKey, ready, startDragOut } from '../src/lib/dragOut.js'

test('outside the desktop shell nothing drags natively', () => {
  assert.equal(canDragOut(), false)
  assert.equal(startDragOut(1, null, null), false)
})

test('cache key: same clip + window collide, different windows do not', () => {
  assert.equal(sourceKey(7, null), '7||')
  assert.equal(sourceKey(7, { in_s: 2, out_s: 4 }), sourceKey(7, { in_s: 2.001, out_s: 4.004 }))
  assert.notEqual(sourceKey(7, { in_s: 2, out_s: 4 }), sourceKey(7, { in_s: 2, out_s: 5 }))
  assert.equal(sourceKey(7, { in_s: 0, out_s: undefined }), '7|0.00|')
})

test('nothing is ready before prepare', () => {
  assert.equal(ready(99, null), null)
})
