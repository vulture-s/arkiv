import { test } from 'node:test'
import assert from 'node:assert/strict'
import { tagQuery, activeTagOf, nextTagQuery } from '../src/lib/tagQuery.js'

test('plain tag → key:value', () => {
  assert.equal(tagQuery('黑膠唱片'), 'tag:黑膠唱片')
  assert.equal(tagQuery('  a-roll '), 'tag:a-roll')
})

test('tag with spaces is quoted, stray quotes dropped', () => {
  assert.equal(tagQuery('golden hour'), 'tag:"golden hour"')
  assert.equal(tagQuery('say "hi"'), 'tag:"say hi"')
})

test('empty name → empty query (no filter)', () => {
  assert.equal(tagQuery(''), '')
  assert.equal(tagQuery(null), '')
})

test('activeTagOf reads back exactly what tagQuery wrote', () => {
  for (const n of ['室內', 'golden hour', 'a-roll']) assert.equal(activeTagOf(tagQuery(n)), n)
})

test('activeTagOf ignores mixed queries', () => {
  assert.equal(activeTagOf('tag:室內 camera:a7s3'), null)
  assert.equal(activeTagOf('室內'), null)
})

test('clicking the active tag again clears the filter', () => {
  assert.equal(nextTagQuery('tag:室內', '室內'), '')
  assert.equal(nextTagQuery('tag:A-Roll', 'a-roll'), '')
  assert.equal(nextTagQuery('tag:室內', '人'), 'tag:人')
  assert.equal(nextTagQuery('some text', '人'), 'tag:人')
})
