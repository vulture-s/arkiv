// Correction-dictionary editor: a failed load must not look like an empty
// dictionary — node:test.
//
// Audit 2026-10-09 K1: SettingsLive did `catch { rules = [] }` with no message,
// and saveVocab PUTs the on-screen list over the WHOLE file. A backend blip
// while the page opened → empty editor → add one rule, save → every existing
// rule gone.
import test from 'node:test'
import assert from 'node:assert/strict'

import { vocabLoadState } from '../src/lib/vocabLoad.js'

test('request failure blocks saving and says why', () => {
  const s = vocabLoadState(null, new Error('arkiv API 503 on /api/corrections'))
  assert.deepEqual(s.rules, [])
  assert.equal(s.canSave, false)
  assert.match(s.msg, /讀取失敗/)
  assert.match(s.msg, /503/)
})

test('backend load_error is shown; saving stays allowed (server keeps a .bak)', () => {
  const s = vocabLoadState({ rules: [], load_error: 'corrections.json 無法讀取（JSONDecodeError: …）' }, null)
  assert.equal(s.canSave, true)
  assert.match(s.msg, /無法讀取/)
  assert.match(s.msg, /\.bak/)
  assert.match(s.msg, /\.corrupt-/)
})

test('clean load: rules through, no message', () => {
  const s = vocabLoadState({ rules: [{ from: 'a', to: 'b' }], load_error: null }, null)
  assert.equal(s.canSave, true)
  assert.equal(s.msg, '')
  assert.equal(s.rules.length, 1)
})
