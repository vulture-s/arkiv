// 精選集「複製進專案」stream reporting — node:test.
//
// Audit 2026-10-09 K1: an index pass skipped because another ingest held the
// slot ({"type":"index","status":"busy"}) was logged as 「▸ 索引完成 (code
// undefined)」 and the run toasted 「複製完成」; a crashed ingest (code≠0), a
// refused project registration, and a stream that ended without `done` were
// equally invisible. The destination project then shows nothing.
import test from 'node:test'
import assert from 'node:assert/strict'

import { createCopyTracker } from '../src/lib/binCopy.js'

const run = (events) => {
  const t = createCopyTracker()
  const lines = events.map((e) => t.handle(e))
  return { t, lines, outcome: t.outcome() }
}

test('busy index pass is NOT reported as 索引完成, and the run is an error', () => {
  const { lines, outcome } = run([
    { type: 'copy', file: 'a.mov', done: 1, total: 1 },
    { type: 'index', status: 'busy', error: '已有匯入任務進行中，已複製檔案但略過索引；請稍後手動 ingest' },
    { type: 'done', summary: { copied: 1, skipped: [], dest: 'P2', mode: 'copy', index_skipped_busy: true } },
  ])
  assert.ok(!lines.some((l) => l && /索引完成/.test(l)), lines.join('\n'))
  assert.ok(lines.some((l) => l && /略過索引/.test(l)))
  assert.equal(outcome.kind, 'error')
  assert.match(outcome.msg, /未索引|略過索引/)
})

test('ingest exit code != 0 makes the run an error', () => {
  const { lines, outcome } = run([
    { type: 'index', status: 'start', files: 2 },
    { type: 'index', status: 'done', code: 1 },
    { type: 'done', summary: { copied: 2, skipped: [], dest: 'P2', mode: 'reference', index_skipped_busy: false } },
  ])
  assert.ok(lines.some((l) => l && /✗/.test(l) && /code 1/.test(l)))
  assert.equal(outcome.kind, 'error')
})

test('registration refusal makes the run an error', () => {
  const { outcome } = run([
    { type: 'index', status: 'done', code: 0 },
    { type: 'registered', error: 'The free tier allows 3 projects' },
    { type: 'done', summary: { copied: 1, skipped: [], dest: 'P2', mode: 'copy', index_skipped_busy: false } },
  ])
  assert.equal(outcome.kind, 'error')
  assert.match(outcome.msg, /註冊/)
})

test('stream that ends without done is an error, not silence', () => {
  const { outcome } = run([{ type: 'copy', file: 'a.mov', done: 1, total: 3 }])
  assert.equal(outcome.kind, 'error')
  assert.match(outcome.msg, /中斷/)
})

test('clean run is ok and mentions skipped count', () => {
  const { lines, outcome } = run([
    { type: 'index', status: 'start', files: 1 },
    { type: 'index', status: 'done', code: 0 },
    { type: 'done', summary: { copied: 1, skipped: [{ project_name: 'A', media_id: '2', status: 'file_missing' }], dest: 'P2', mode: 'copy', index_skipped_busy: false } },
  ])
  assert.ok(lines.some((l) => l && /索引完成/.test(l)))
  assert.equal(outcome.kind, 'ok')
  assert.match(outcome.msg, /略過 1/)
})
