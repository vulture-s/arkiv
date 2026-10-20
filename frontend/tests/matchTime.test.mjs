// Search hit → open the clip at the matched second. node:test, no runner dep.
import test from 'node:test'
import assert from 'node:assert/strict'

import { parseStartParam, resultHref, fmtMatchTime, pendingSeek } from '../src/lib/matchTime.js'

test('t param: only finite, non-negative seconds are accepted', () => {
  assert.equal(parseStartParam('9.5'), 9.5)
  assert.equal(parseStartParam('0'), 0)
  for (const bad of [null, '', '-3', 'abc', 'Infinity', 'NaN']) {
    assert.equal(parseStartParam(bad), null, String(bad))
  }
})

test('href carries t only when the hit has a time (0 is a real time)', () => {
  assert.equal(resultHref(7, 12.25), '#/main-live?sel=7&t=12.25')
  assert.equal(resultHref(7, 0), '#/main-live?sel=7&t=0')
  assert.equal(resultHref(7, null), '#/main-live?sel=7')
  assert.equal(resultHref(7, undefined), '#/main-live?sel=7')
  assert.equal(resultHref(7, NaN), '#/main-live?sel=7')
})

test('time label switches to H:MM:SS past an hour', () => {
  assert.equal(fmtMatchTime(9.5), '00:09')
  assert.equal(fmtMatchTime(83), '01:23')
  assert.equal(fmtMatchTime(3725), '1:02:05')
})

test('seek applies once, and only to the deep-linked clip', () => {
  const s = { id: 7, t: 42 }
  const first = pendingSeek(s, 7, null)
  assert.deepEqual(first, { t: 42, key: '7@42' })
  assert.equal(pendingSeek(s, 7, first.key), null)      // metadata reload: no re-seek
  assert.equal(pendingSeek(s, 8, null), null)           // user moved to another clip
  assert.equal(pendingSeek(null, 7, null), null)
  assert.deepEqual(pendingSeek({ id: 7, t: 50 }, 7, first.key), { t: 50, key: '7@50' })  // new link
})
