import { test } from 'node:test'
import assert from 'node:assert/strict'
import { POOL_SORTS, DEFAULT_POOL_SORT, loadPoolSort, savePoolSort } from '../src/lib/poolSort.js'
import { mediaParams } from '../src/lib/shotYear.js'

const mem = (init = {}) => {
  const m = new Map(Object.entries(init))
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) }
}

test('offers shoot-date sorts in both directions', () => {
  const keys = POOL_SORTS.map(([k]) => k)
  assert.ok(keys.includes('shot_new') && keys.includes('shot_old'))
  assert.equal(new Set(keys).size, keys.length)
})

test('round-trips the choice and rejects unknown / broken storage', () => {
  const s = mem()
  assert.equal(loadPoolSort(s), DEFAULT_POOL_SORT)
  savePoolSort('shot_old', s)
  assert.equal(loadPoolSort(s), 'shot_old')
  savePoolSort('not-a-sort', s)
  assert.equal(loadPoolSort(s), 'shot_old')
  assert.equal(loadPoolSort(mem({ 'arkiv.poolsort': 'bogus' })), DEFAULT_POOL_SORT)
  const throwing = { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } }
  assert.equal(loadPoolSort(throwing), DEFAULT_POOL_SORT)
  assert.doesNotThrow(() => savePoolSort('name', throwing))
})

test('mediaParams carries the sort to every page request', () => {
  assert.equal(mediaParams({ limit: 60, sort: 'shot_new' }).sort, 'shot_new')
  assert.equal(mediaParams({ limit: 60, offset: 60, sort: 'shot_old', shotYear: '2025' }).sort, 'shot_old')
  assert.ok(!('sort' in mediaParams({ limit: 60 })))
})

test('column header click picks a first direction, then toggles', async () => {
  const { nextColumnSort, columnArrow, COLUMN_SORTS } = await import('../src/lib/poolSort.js')
  const keys = new Set(POOL_SORTS.map(([k]) => k))
  for (const opts of Object.values(COLUMN_SORTS)) for (const k of opts) assert.ok(keys.has(k), k)
  assert.equal(nextColumnSort('shot', 'date'), 'shot_new')
  assert.equal(nextColumnSort('shot', 'shot_new'), 'shot_old')
  assert.equal(nextColumnSort('shot', 'shot_old'), 'shot_new')
  assert.equal(nextColumnSort('rating', 'rating'), 'rating')
  assert.equal(nextColumnSort('nope', 'size'), 'size')
  assert.equal(columnArrow('shot', 'shot_old'), '↑')
  assert.equal(columnArrow('shot', 'shot_new'), '↓')
  assert.equal(columnArrow('name', 'name'), '↑')
  assert.equal(columnArrow('date', 'shot_new'), '')
})
