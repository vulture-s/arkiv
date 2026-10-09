// Delete toast honesty — node:test.
//
// Audit 2026-10-09 K1: the delete toast read `r.external` / `r.filename`, fields
// the backend never sends ({ok, media_id, file_deleted, warning}). A clip outside
// PROJECT_ROOT (any NAS /Volumes library while ARKIV_MEDIA_ROOTS is unset) is a
// metadata-only delete — tags / rating / transcript gone, NOTHING in the recycle
// bin — and the toast said 「已移至回收桶」. These pin the toast to what happened.
import test from 'node:test'
import assert from 'node:assert/strict'

import { describeDelete } from '../src/lib/deleteOutcome.js'

test('single: a real trash move reads as recoverable', () => {
  const o = describeDelete('single', { ok: true, media_id: 7, file_deleted: true, warning: null }, 'A001.mov', [7])
  assert.equal(o.kind, 'ok')
  assert.match(o.msg, /回收桶/)
  assert.match(o.msg, /A001\.mov/)
  assert.deepEqual(o.removeIds, [7])
})

test('single: metadata-only delete must NOT claim the recycle bin', () => {
  const o = describeDelete('single', {
    ok: true, media_id: 1, file_deleted: false,
    warning: 'external path outside allowed roots: metadata-only, original kept',
  }, 'A001.mov', [1])
  assert.equal(o.kind, 'error')
  assert.ok(!/已移至回收桶/.test(o.msg), `claimed trash: ${o.msg}`)
  assert.match(o.msg, /未進回收桶/)
  assert.match(o.msg, /metadata-only/)
  assert.deepEqual(o.removeIds, [1]) // the row IS gone — the grid must not keep a ghost
})

test('single: failed trash move is surfaced, not reported as success', () => {
  const o = describeDelete('single', {
    ok: true, media_id: 3, file_deleted: false, warning: 'file move to trash failed; metadata removed',
  }, 'B.mov', [3])
  assert.equal(o.kind, 'error')
  assert.match(o.msg, /file move to trash failed/)
})

test('single: trashed but with a side warning still shows the warning', () => {
  const o = describeDelete('single', { ok: true, media_id: 4, file_deleted: true, warning: '精選集未能清除此素材（OSError）' }, 'C.mov', [4])
  assert.equal(o.kind, 'error')
  assert.match(o.msg, /精選集未能清除/)
})

test('bulk: not_trashed items are counted and flagged', () => {
  const o = describeDelete('bulk', {
    ok: true, deleted: [1, 2, 3], skipped: [9], errors: [],
    not_trashed: [{ media_id: 2, warning: 'external path outside allowed roots: metadata-only, original kept' }],
    warnings: [{ media_id: 2, warning: 'external path outside allowed roots: metadata-only, original kept' }],
  }, '4 支素材', [1, 2, 3, 9])
  assert.equal(o.kind, 'error')
  assert.match(o.msg, /已移至回收桶 2 支/) // 3 deleted, but only 2 are recoverable
  assert.match(o.msg, /1 支未進回收桶/)
  assert.deepEqual(o.removeIds.sort(), [1, 2, 3, 9])
})

test('bulk: hard errors keep their rows on screen', () => {
  const o = describeDelete('bulk', {
    ok: true, deleted: [1], skipped: [], errors: [{ media_id: 'x', error: 'bad id' }], not_trashed: [], warnings: [],
  }, '2 支素材', [1, 'x'])
  assert.equal(o.kind, 'error')
  assert.deepEqual(o.removeIds, [1])
})

test('bulk: clean run is ok', () => {
  const o = describeDelete('bulk', { ok: true, deleted: [1, 2], skipped: [], errors: [], not_trashed: [], warnings: [] }, '2 支素材', [1, 2])
  assert.equal(o.kind, 'ok')
  assert.match(o.msg, /已移至回收桶 2 支/)
})
