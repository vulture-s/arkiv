// node --test. #572: an empty person list must not trigger another fetch
// (src/lib/identityNames.js). Before the fix a clip selected in a library with no
// people yet re-fetched /api/identities/names ~390×/s.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createIdentityNamesLoader } from '../src/lib/identityNames.js'

function counting(answer) {
  const calls = { n: 0 }
  const fetchNames = async () => {
    calls.n += 1
    if (answer instanceof Error) throw answer
    return answer
  }
  return { calls, fetchNames }
}

test('empty answer: repeated ensure() still fetches exactly once', async () => {
  const { calls, fetchNames } = counting([])
  let got = null
  const loader = createIdentityNamesLoader(fetchNames, (n) => { got = n })
  // What the reactive block does every time it re-runs.
  for (let i = 0; i < 50; i++) await loader.ensure()
  assert.equal(calls.n, 1)
  assert.deepEqual(got, [])
})

test('ensure() called again before the first answer lands does not double-fetch', async () => {
  const { calls, fetchNames } = counting([{ name: 'A', count: 1 }])
  const loader = createIdentityNamesLoader(fetchNames, () => {})
  await Promise.all([loader.ensure(), loader.ensure(), loader.ensure()])
  assert.equal(calls.n, 1)
})

test('a failed fetch is not retried by ensure()', async () => {
  const { calls, fetchNames } = counting(new Error('offline'))
  const loader = createIdentityNamesLoader(fetchNames, () => assert.fail('no names on failure'))
  for (let i = 0; i < 10; i++) await loader.ensure()
  assert.equal(calls.n, 1)
})

test('refresh() fetches again (after an add/remove)', async () => {
  const { calls, fetchNames } = counting([])
  const loader = createIdentityNamesLoader(fetchNames, () => {})
  await loader.ensure()
  await loader.refresh()
  await loader.ensure()
  assert.equal(calls.n, 2)
})

test('MainLive: the reactive line that loads names does not read the names it assigns', () => {
  // The seam #572 broke: a `$:` block that both reads identityNames and (through
  // the fetch) assigns it re-runs itself. Find every reactive line that drives
  // the loader and check it depends on nothing the loader writes.
  const src = readFileSync(new URL('../src/routes/MainLive.svelte', import.meta.url), 'utf8')
  const lines = src.split('\n').filter((l) => /^\s*\$:/.test(l) && l.includes('identityNamesLoader'))
  assert.ok(lines.length >= 1, 'no reactive line drives identityNamesLoader — wiring changed?')
  for (const l of lines) {
    assert.ok(!/\bidentityNames\b/.test(l), `reactive line reads identityNames: ${l.trim()}`)
  }
})
