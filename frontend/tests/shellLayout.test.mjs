import { test } from 'node:test'
import assert from 'node:assert/strict'
import { layoutClass, shellLayout, togglePanel, BREAKPOINTS } from '../src/lib/shellLayout.js'

test('breakpoint edges', () => {
  assert.equal(layoutClass(BREAKPOINTS.wide), 'wide')
  assert.equal(layoutClass(BREAKPOINTS.wide - 1), 'regular')
  assert.equal(layoutClass(BREAKPOINTS.regular), 'regular')
  assert.equal(layoutClass(BREAKPOINTS.regular - 1), 'compact')
  assert.equal(layoutClass(BREAKPOINTS.compact), 'compact')
  assert.equal(layoutClass(BREAKPOINTS.compact - 1), 'narrow')
})

test('unknown width falls back to the old fixed layout', () => {
  const s = shellLayout(0)
  assert.equal(s.layout, 'regular')
  assert.equal(s.poolCol, 220)
  assert.equal(s.inspCol, 400)
})

test('1400px default = unchanged 220 | 1fr | 400 shell', () => {
  const s = shellLayout(1400)
  assert.deepEqual([s.poolMode, s.poolCol, s.inspCol], ['column', 220, 400])
})

test('window minimum (900px) leaves room for 2+ card columns', () => {
  const s = shellLayout(900)
  assert.equal(s.poolMode, 'hidden')
  const center = 900 - s.poolCol - s.inspCol
  // grid card min 198px + 22px padding each side + 1px gaps
  assert.ok(center - 44 >= 2 * 198 + 1, `center ${center}px fits <2 cards`)
})

test('narrow: opening the pool floats it instead of taking a column', () => {
  const s = shellLayout(1000, { pool: 'open' })
  assert.equal(s.poolMode, 'overlay')
  assert.equal(s.poolCol, 0)
})

test('user can hide either panel at any width', () => {
  const s = shellLayout(1800, { pool: 'closed', insp: 'closed' })
  assert.deepEqual([s.poolMode, s.poolCol, s.inspOpen, s.inspCol], ['hidden', 0, false, 0])
})

test('toggle flips the visible state, not the stored pref', () => {
  assert.equal(togglePanel(true), 'closed')
  assert.equal(togglePanel(false), 'open')
})
