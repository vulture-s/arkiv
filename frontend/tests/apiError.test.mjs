// ApiError message folding — node:test, no runner dependency (CI already has Node 20).
//
// The free-tier gate made the backend send STRUCTURED 403 details
// ({code, message}) for the first time. Every other endpoint sends a plain
// string, so the existing fold path had never met an object it was supposed to
// read rather than dump — and the fallback it did have (JSON.stringify) turns a
// refusal into raw JSON in a toast. These pin the two halves apart: the human
// sentence goes in the message, the code stays machine-readable.
import test from 'node:test'
import assert from 'node:assert/strict'

import { ApiError } from '../src/lib/api.js'

test('structured detail shows its message, not its JSON', () => {
  const err = new ApiError(403, '/api/projects', {
    detail: {
      code: 'project_limit',
      message: 'The free tier allows 3 projects and this installation already has 3.',
    },
  })
  assert.match(err.message, /The free tier allows 3 projects/)
  // The failure this guards: the user reading braces and quotes instead of a sentence.
  assert.ok(!err.message.includes('{'), `raw JSON leaked into: ${err.message}`)
  assert.ok(!err.message.includes('"code"'), `raw JSON leaked into: ${err.message}`)
})

test('structured detail keeps the code for callers to branch on', () => {
  const err = new ApiError(403, '/api/bins/x/items', {
    detail: { code: 'cross_project', message: 'Cross-project collections are Pro.' },
  })
  assert.equal(err.code, 'cross_project')
  assert.equal(err.status, 403)
})

test('plain string details still fold exactly as before', () => {
  // Regression guard: the overwhelming majority of endpoints send a string, and
  // the structured branch must not have changed what they render.
  const err = new ApiError(404, '/api/media/9', { detail: '找不到媒體檔案' })
  assert.match(err.message, /找不到媒體檔案/)
  assert.equal(err.code, null)
})

test('an object detail without a message is still not swallowed', () => {
  // Not every dict detail is an entitlement refusal — FastAPI's own validation
  // errors are lists/dicts too. Showing their JSON is ugly but showing NOTHING
  // would be worse, so the stringify fallback has to survive.
  const err = new ApiError(422, '/api/search/query', {
    detail: [{ loc: ['body', 'field'], msg: 'field required' }],
  })
  assert.match(err.message, /field required/)
  assert.equal(err.code, null)
})

test('a body with no detail at all degrades to the bare status line', () => {
  const err = new ApiError(500, '/api/stats', null)
  assert.equal(err.message, 'arkiv API 500 on /api/stats')
  assert.equal(err.code, null)
})
