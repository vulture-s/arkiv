// Transcript 選字 → 校正 — node:test.
//
// The `from` sent to /api/corrections/learn becomes a GLOBAL replace rule across
// the whole project, so these pin that it is exactly what the user selected inside
// ONE segment: trimmed at the edges, never spliced across segments, never longer
// than a phrase.
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  selectionToFrom, validateCorrection, describeLearn, MAX_FROM,
} from '../src/lib/transcriptCorrect.js'

const SEG = '我們從上海一路騎到拉薩，第十七天。'

test('single-segment selection → from is the selected text', () => {
  const r = selectionToFrom({ startSeg: 2, endSeg: 2, text: '拉薩', segText: SEG })
  assert.deepEqual(r, { ok: true, from: '拉薩', seg: 2 })
})

test('edge whitespace (double-click / triple-click) is trimmed', () => {
  const r = selectionToFrom({ startSeg: 0, endSeg: 0, text: '  上海\n', segText: SEG })
  assert.equal(r.ok, true)
  assert.equal(r.from, '上海')
})

test('cross-segment selection is refused, not spliced', () => {
  const r = selectionToFrom({ startSeg: 1, endSeg: 2, text: '第十七天。\n中間', segText: SEG })
  assert.equal(r.ok, false)
  assert.match(r.reason, /跨了兩段/)
})

test('an inner newline is treated as cross-segment even if the DOM said one segment', () => {
  const r = selectionToFrom({ startSeg: 1, endSeg: 1, text: '天。\n中間', segText: SEG })
  assert.equal(r.ok, false)
  assert.match(r.reason, /跨了兩段/)
})

test('an end outside transcript text (timecode column) is refused', () => {
  const r = selectionToFrom({ startSeg: null, endSeg: 0, text: '00:05 我們', segText: SEG })
  assert.equal(r.ok, false)
  assert.match(r.reason, /時間碼/)
})

test('empty / whitespace-only selection → reason "empty" (no popover, no error)', () => {
  assert.deepEqual(selectionToFrom({ startSeg: 0, endSeg: 0, text: '   ', segText: SEG }), { ok: false, reason: 'empty' })
  assert.deepEqual(selectionToFrom({ startSeg: 0, endSeg: 0, text: '', segText: SEG }), { ok: false, reason: 'empty' })
  assert.deepEqual(selectionToFrom(null), { ok: false, reason: 'empty' })
})

test('text not actually in the segment is refused (stale selection)', () => {
  const r = selectionToFrom({ startSeg: 0, endSeg: 0, text: '格爾木', segText: SEG })
  assert.equal(r.ok, false)
})

test('over-long selection is refused with its length', () => {
  const long = '字'.repeat(MAX_FROM + 1)
  const r = selectionToFrom({ startSeg: 0, endSeg: 0, text: long, segText: long })
  assert.equal(r.ok, false)
  assert.match(r.reason, new RegExp(String(MAX_FROM + 1)))
})

test('validateCorrection: trims, rejects empty and identical', () => {
  assert.deepEqual(validateCorrection('李東煙', ' 李多慧 '), { ok: true, from: '李東煙', to: '李多慧' })
  assert.equal(validateCorrection('李東煙', '   ').ok, false)
  assert.equal(validateCorrection('李東煙', '李東煙').ok, false)
  assert.equal(validateCorrection('', '李多慧').ok, false)
  assert.equal(validateCorrection('a', 'x'.repeat(201)).ok, false)
})

test('describeLearn: applied → names the rule and how many clips changed', () => {
  const msg = describeLearn({
    rule: { from: '李東煙', to: '李多慧' }, created: true, hotword: true,
    applied: { media_updated: 3, total_hits: 7, backup: 'recorrect-x.json' },
  }, true)
  assert.match(msg, /「李東煙」→「李多慧」/)
  assert.match(msg, /熱詞/)
  assert.match(msg, /3 支素材、7 處/)
})

test('describeLearn: applied but zero hits says so; update says 更新', () => {
  const msg = describeLearn({
    rule: { from: 'a', to: 'b' }, created: false, hotword: false,
    applied: { media_updated: 0, total_hits: 0, backup: null },
  }, true)
  assert.match(msg, /更新既有規則/)
  assert.match(msg, /沒有命中/)
  assert.doesNotMatch(msg, /熱詞/)
})

test('describeLearn: not applied says existing transcripts were left alone', () => {
  const msg = describeLearn({ rule: { from: 'a', to: 'b' }, created: true, hotword: false }, false)
  assert.match(msg, /未改寫既有逐字稿/)
})

test('one end unresolved but the text spans a line break → cross-segment reason', () => {
  const r = selectionToFrom({ startSeg: 0, endSeg: null, text: '來賓是李東煙。\n00:04\n她剛', segText: SEG })
  assert.equal(r.ok, false)
  assert.match(r.reason, /跨了兩段/)
})
