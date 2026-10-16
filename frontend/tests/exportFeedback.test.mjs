// Export feedback helpers (B11): size formatting, toast text, single-flight guard.
import test from 'node:test'
import assert from 'node:assert/strict'

import { formatBytes, downloadedMsg, createExportGuard } from '../src/lib/exportFeedback.js'

test('formatBytes', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(1023), '1023 B')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(150 * 1024), '150 KB')
  assert.equal(formatBytes(5 * 1024 * 1024), '5.0 MB')
  assert.equal(formatBytes(undefined), null)
  assert.equal(formatBytes(-1), null)
})

test('downloadedMsg names file and size; flags empty files', () => {
  assert.equal(downloadedMsg('a.edl', 1536), '已下載 a.edl（1.5 KB）')
  assert.equal(downloadedMsg('a.edl', 1536, '時間軸 3 支'), '已下載 a.edl（1.5 KB） · 時間軸 3 支')
  assert.equal(downloadedMsg('a.edl', 0), '已下載 a.edl（0 B · 空檔案）')
  assert.equal(downloadedMsg('a.edl', undefined), '已下載 a.edl')
})

test('guard: single-flight, reports busy key, clears on throw', async () => {
  const seen = []
  const g = createExportGuard((b) => seen.push(b))
  let release
  const first = g.run('edl', () => new Promise((r) => { release = r }))
  assert.equal(g.busy, 'edl')
  assert.equal(await g.run('srt', () => { throw new Error('must not run') }), false)
  release()
  assert.equal(await first, true)
  assert.equal(g.busy, null)
  await assert.rejects(g.run('csv', () => { throw new Error('boom') }), /boom/)
  assert.equal(g.busy, null)
  assert.deepEqual(seen, ['edl', null, 'csv', null])
})
