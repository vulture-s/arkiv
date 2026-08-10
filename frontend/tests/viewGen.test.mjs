// node --test. No test runner dependency: Node 20 ships node:test, and CI already
// installs Node 20 for the frontend build job.
//
// These do NOT assert that a counter counts — that would be a tautology dressed as a
// test. Each case replays one of the interleavings that actually reaches MainLive's
// grid state, with the awaits resolved by hand so the ordering is deterministic
// instead of timing-dependent.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createViewGen } from '../src/lib/viewGen.js'

// A stand-in for the shared grid state the four writers fight over.
function grid() {
  return { view: 'initial', items: [] }
}

test('a search that lands after the user picked a collection does not repaint the grid', () => {
  const g = createViewGen()
  const state = grid()

  // runSearch() starts and awaits /api/media?q=
  const searchToken = g.begin()

  // ...the user clicks a Smart Collection, which writes synchronously.
  const collectionToken = g.begin()
  g.apply(collectionToken, () => {
    state.view = 'collection'
    state.items = ['c1', 'c2']
  })

  // ...and only now does the search response arrive.
  const applied = g.apply(searchToken, () => {
    state.view = 'search'
    state.items = ['s1']
  })

  assert.equal(applied, false, 'the superseded search must not write')
  assert.equal(state.view, 'collection')
  assert.deepEqual(state.items, ['c1', 'c2'])
})

test('a collection picked while load() is in flight survives load() resolving', () => {
  const g = createViewGen()
  const state = grid()

  // load() clears activeCollection and awaits checkReady() + Promise.all([...]).
  const loadToken = g.begin()

  const collectionToken = g.begin()
  g.apply(collectionToken, () => {
    state.view = 'collection'
  })

  // load()'s two await points both have to be guarded, not just the last one: the
  // readyState write is as capable of contradicting the new view as the items write.
  assert.equal(g.isCurrent(loadToken), false, 'guard after checkReady()')
  assert.equal(g.apply(loadToken, () => { state.view = 'library' }), false)
  assert.equal(state.view, 'collection')
})

test('out-of-order search responses leave the newest query showing', () => {
  const g = createViewGen()
  const state = grid()

  const slow = g.begin() // 黑膠
  const fast = g.begin() // 唱針

  // The second request resolves first (smaller result set, warm cache — ordinary).
  assert.equal(g.apply(fast, () => { state.items = ['唱針結果'] }), true)
  // The first one finally lands. Before the guard this repainted the grid with the
  // previous query's results while the search box still read 唱針.
  assert.equal(g.apply(slow, () => { state.items = ['黑膠結果'] }), false)

  assert.deepEqual(state.items, ['唱針結果'])
})

test('a failing stale request does not blow away the view that replaced it', () => {
  const g = createViewGen()
  const state = grid()

  const searchToken = g.begin()
  const collectionToken = g.begin()
  g.apply(collectionToken, () => { state.view = 'collection' })

  // The catch block needs the same guard as the success path. An error banner from a
  // request nobody is waiting for is a false report about the view on screen.
  assert.equal(g.apply(searchToken, () => { state.view = 'error' }), false)
  assert.equal(state.view, 'collection')
})

test('loadMore extends the current view and is dropped when the view changed', () => {
  const g = createViewGen()
  const state = grid()

  const viewToken = g.begin()
  g.apply(viewToken, () => { state.items = ['a', 'b'] })

  // loadMore() reads `current` rather than calling begin() — paging is not a new view,
  // so it must not invalidate anything.
  const pageToken = g.current
  assert.equal(pageToken, viewToken, 'paging must not claim a new generation')
  assert.equal(g.apply(pageToken, () => { state.items = [...state.items, 'c'] }), true)
  assert.deepEqual(state.items, ['a', 'b', 'c'])

  // Now page again, but the user switches view before the page lands.
  const stalePage = g.current
  g.begin()
  assert.equal(g.apply(stalePage, () => { state.items = [...state.items, 'd'] }), false)
  assert.deepEqual(state.items, ['a', 'b', 'c'], 'old-view rows must not append to a new view')
})

test('a token stays valid across awaits when nothing else claims the grid', async () => {
  const g = createViewGen()
  const token = g.begin()
  await new Promise((r) => setTimeout(r, 1))
  assert.equal(g.isCurrent(token), true, 'the uncontended case must still write')
})
