// 9.8a first-run setup wizard — pure helpers. node:test, no runner dependency.
import test from 'node:test'
import assert from 'node:assert/strict'

import { shouldShowWizard, fmtBytes, pullLine, ffmpegHint, modelRows, blockers } from '../src/lib/setupWizard.js'

test('wizard only for an unfinished setup on a library with none of the user\'s media', () => {
  const sample = { loaded: true, media_ids: [1, 2, 3] }
  assert.equal(shouldShowWizard({ completed: false }, { total: 3 }, sample), true)
  assert.equal(shouldShowWizard({ completed: true }, { total: 0 }, null), false)
  assert.equal(shouldShowWizard({ completed: false }, { total: 40 }, sample), false, 'upgrading user with a full library')
  assert.equal(shouldShowWizard({ completed: false }, null, null), false, 'unknown totals → never on a guess')
  assert.equal(shouldShowWizard(null, { total: 0 }, null), false, 'older backend without /api/setup')
})

test('bytes and progress lines', () => {
  assert.equal(fmtBytes(0), '0 MB')
  assert.equal(fmtBytes(512 * 1024 ** 2), '512 MB')
  assert.equal(fmtBytes(1.5 * 1024 ** 3), '1.50 GB')
  assert.equal(pullLine({ status: 'downloading', total: 200, completed: 50 }), '25% · 0 MB / 0 MB')
  assert.equal(pullLine({ status: 'downloading', completed: 3 * 1024 ** 3 }), '已下載 3.00 GB')
  assert.equal(pullLine({ status: 'error', message: 'boom' }), '失敗：boom')
  assert.equal(pullLine({ status: 'done' }), '完成')
})

test('ffmpeg hint per OS', () => {
  assert.equal(ffmpegHint('Darwin 25.0.0'), 'brew install ffmpeg')
  assert.equal(ffmpegHint('Windows 11'), 'winget install ffmpeg')
})

test('model rows: whisper first, then ollama', () => {
  const rows = modelRows({
    whisper: { name: 'w', present: true, size_hint: '1.6 GB' },
    ollama: { models: [{ name: 'bge-m3', label: '語意搜尋', present: false, size_hint: '1.2 GB' }] },
  })
  assert.deepEqual(rows.map((r) => [r.name, r.present]), [['w', true], ['bge-m3', false]])
})

test('blockers: missing ffmpeg and an unusable transcription device', () => {
  assert.deepEqual(blockers({ ffmpeg: true, transcribe: { ok: 'true' } }), [])
  const b = blockers({ ffmpeg: false, hardware: { os: 'Windows 11' }, transcribe: { ok: 'false', note: '設成 cpu' } })
  assert.equal(b.length, 2)
  assert.match(b[0], /winget install ffmpeg/)
})
