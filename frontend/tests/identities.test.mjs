// node --test. A6 person annotations: the Inspector's range / autocomplete /
// keyboard rules (src/lib/identities.js).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { identityRange, suggestNames, formatIdentityRange, nameKeyStep } from '../src/lib/identities.js'

test('IN/OUT → range; nothing marked → whole clip', () => {
  assert.deepEqual(identityRange(null, null, 30), {})
  assert.deepEqual(identityRange(2.5, 8, 30), { start_s: 2.5, end_s: 8 })
  assert.deepEqual(identityRange(0, 8, 30), { start_s: 0, end_s: 8 }) // IN at 0 is a real mark
  assert.deepEqual(identityRange(5, null, 30), { start_s: 5, end_s: 30 })
  assert.deepEqual(identityRange(null, 7, 30), { start_s: 0, end_s: 7 })
})

test('unusable marks fall back to the whole clip, never a bad range', () => {
  assert.deepEqual(identityRange(5, null, null), {}) // IN only, duration unknown
  assert.deepEqual(identityRange(8, 8, 30), {})
  assert.deepEqual(identityRange(9, 3, 30), {})
  assert.deepEqual(identityRange(NaN, 3, 30), { start_s: 0, end_s: 3 })
  assert.deepEqual(identityRange(-1, -2, 30), {})
})

test('suggestions: substring, case-insensitive, prefix first, then usage', () => {
  const names = [
    { name: 'Alice Chen', count: 1 },
    { name: 'Chen Bo', count: 5 },
    { name: '陳小明', count: 2 },
    { name: 'alicia', count: 3 },
  ]
  assert.deepEqual(suggestNames(names, 'chen'), ['Chen Bo', 'Alice Chen'])
  assert.deepEqual(suggestNames(names, 'ALI'), ['alicia', 'Alice Chen'])
  assert.deepEqual(suggestNames(names, '小明'), ['陳小明'])
  assert.deepEqual(suggestNames(names, ''), ['Chen Bo', 'alicia', '陳小明', 'Alice Chen'])
  // already on this clip / exactly what is typed → not suggested
  assert.deepEqual(suggestNames(names, 'chen', ['chen bo']), ['Alice Chen'])
  assert.deepEqual(suggestNames(names, 'alicia'), [])
  assert.deepEqual(suggestNames(null, 'x'), [])
})

test('range labels', () => {
  assert.equal(formatIdentityRange({ start_s: null, end_s: null }), '整支片')
  assert.equal(formatIdentityRange({}), '整支片')
  assert.equal(formatIdentityRange({ start_s: 12.7, end_s: 75 }), '00:12–01:15')
  assert.equal(formatIdentityRange({ start_s: 3600, end_s: 3661 }), '1:00:00–1:01:01')
})

test('keys: Enter submits typed or highlighted, Esc cancels, arrows wrap', () => {
  const sug = ['A', 'B']
  assert.deepEqual(nameKeyStep('Enter', ' 小明 ', sug, -1), { action: 'submit', highlight: -1, name: '小明' })
  assert.deepEqual(nameKeyStep('Enter', 'x', sug, 1), { action: 'submit', highlight: -1, name: 'B' })
  assert.deepEqual(nameKeyStep('Enter', '  ', [], -1), { action: null, highlight: -1 })
  assert.deepEqual(nameKeyStep('Escape', 'x', sug, 0), { action: 'cancel', highlight: -1 })
  assert.deepEqual(nameKeyStep('ArrowDown', '', sug, -1), { action: 'move', highlight: 0 })
  assert.deepEqual(nameKeyStep('ArrowDown', '', sug, 1), { action: 'move', highlight: 0 })
  assert.deepEqual(nameKeyStep('ArrowUp', '', sug, -1), { action: 'move', highlight: 1 })
  assert.deepEqual(nameKeyStep('ArrowDown', '', [], -1), { action: null, highlight: -1 })
  assert.deepEqual(nameKeyStep('a', 'a', sug, 0), { action: null, highlight: 0 })
})
