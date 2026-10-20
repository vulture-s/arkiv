// Settings → Projects first-run guidance — node:test, no runner dependency.
//
// The bug these pin (2026-10-20 Windows first-run walkthrough): a new user took
// the registry's "add project" form for "start using arkiv here" and registered
// a raw footage folder. The page now (a) points an empty library at + INGEST and
// (b) tells the user when what they registered is not an arkiv library.
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  ownMediaCount, needsIngestHint, healthLabel, addedMessage, addErrorMessage,
} from '../src/lib/projectsFirstRun.js'
import { ApiError } from '../src/lib/api.js'

test('empty library → ingest hint', () => {
  assert.equal(ownMediaCount({ total: 0 }, null), 0)
  assert.equal(needsIngestHint({ total: 0 }, null), true)
})

test('library with the user\'s own media → no hint', () => {
  assert.equal(needsIngestHint({ total: 12 }, { loaded: false, media_ids: [] }), false)
})

test('only the auto-seeded sample clips still counts as not started', () => {
  // The server seeds a fresh library with the bundled sample, so total > 0 alone
  // would hide the hint from exactly the user it is for.
  const sample = { loaded: true, media_ids: [1, 2, 3] }
  assert.equal(ownMediaCount({ total: 3 }, sample), 0)
  assert.equal(needsIngestHint({ total: 3 }, sample), true)
  assert.equal(needsIngestHint({ total: 4 }, sample), false)
})

test('unknown stats never shows the hint (no guessing)', () => {
  assert.equal(ownMediaCount(null, null), null)
  assert.equal(needsIngestHint(null, null), false)
  assert.equal(needsIngestHint({}, null), false)
  assert.equal(needsIngestHint({ total: undefined }, { loaded: true, media_ids: [1] }), false)
})

test('health codes read as words, unknown codes pass through', () => {
  assert.equal(healthLabel('ok'), '正常')
  assert.equal(healthLabel('db_missing'), '不是 arkiv 素材庫')
  assert.equal(healthLabel(undefined), '—')
  assert.equal(healthLabel('weird_new_code'), 'weird_new_code')
})

test('registering a plain folder says it is not a library and points at INGEST', () => {
  const m = addedMessage('nas', 'db_missing')
  assert.match(m, /\.arkiv\//)
  assert.match(m, /INGEST/)
  assert.equal(addedMessage('nas', 'ok'), '已登記 nas')
})

test('add errors: backend detail, not the ApiError wrapper', () => {
  const notFound = new ApiError(400, '/api/projects', { detail: 'project path not found: Z:\\x' })
  assert.match(addErrorMessage(notFound, 'x'), /找不到這個資料夾/)
  const dup = new ApiError(400, '/api/projects', { detail: 'project path already registered: /a' })
  assert.equal(addErrorMessage(dup, 'x'), '這個資料夾已經登記過了。')
  const clash = new ApiError(409, '/api/projects', { detail: 'project name already exists: x' })
  assert.match(addErrorMessage(clash, 'x'), /「x」/)
  const cap = new ApiError(403, '/api/projects', {
    detail: { code: 'project_limit', message: 'The free tier allows 3 projects.' },
  })
  assert.equal(addErrorMessage(cap, 'x'), 'The free tier allows 3 projects.')
  const other = new ApiError(500, '/api/projects', { detail: 'boom' })
  assert.equal(addErrorMessage(other, 'x'), '加入失敗：boom')
  assert.ok(!addErrorMessage(other, 'x').includes('arkiv API'))
})
