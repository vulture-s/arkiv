// i18n coverage gate (#581): every $t()/tr() key has an English entry, and no
// unwrapped Chinese is added to the live UI (per-file ratchet, only allowed to
// go down — tests/i18n-baseline.json). Design mock routes are out of scope.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'

import { translate, resolveLocale } from '../src/lib/i18n.js'
import en, { FRAGMENTS } from '../src/lib/i18n/en/index.js'
import { liveFiles, scan, scanText } from './i18nScan.mjs'

const BASELINE_URL = new URL('./i18n-baseline.json', import.meta.url)
const baseline = JSON.parse(readFileSync(BASELINE_URL, 'utf8'))
const CJK = /[㐀-鿿]/
const placeholders = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

test('translate: zh-TW is the source, en looks up, missing falls back, params fill', () => {
  assert.equal(translate('zh-TW', '重設'), '重設')
  assert.equal(translate('en', '重設'), 'Reset')
  assert.equal(translate('en', '字典裡沒有的字'), '字典裡沒有的字')
  assert.equal(translate('zh-TW', '共 {n} 支', { n: 3 }), '共 3 支')
  assert.equal(translate('en', '共 {n} 支', { x: 1 }), '共 {n} 支', 'unknown param left visible, not blanked')
})

test('outside a browser "system" resolves to the source language (node has navigator.language=en-US)', () => {
  assert.equal(resolveLocale('system'), 'zh-TW')
  assert.equal(resolveLocale('en'), 'en')
})

test('fragments: no conflicting duplicates; values are English with the same placeholders', () => {
  const seen = {}
  for (const [name, frag] of Object.entries(FRAGMENTS)) {
    for (const [k, v] of Object.entries(frag)) {
      if (k in seen) assert.equal(v, seen[k].v, `「${k}」 in ${name} disagrees with ${seen[k].name}`)
      seen[k] = { v, name }
      assert.ok(typeof v === 'string' && v.trim(), `empty translation for 「${k}」 (${name})`)
      assert.ok(!CJK.test(v), `untranslated Chinese in en value for 「${k}」 (${name}): ${v}`)
      assert.deepEqual(placeholders(v), placeholders(k), `placeholder mismatch for 「${k}」 (${name})`)
    }
  }
})

test('scanner: sees unwrapped Chinese, skips comments/wrapped/ignored — and a comment mentioning /* does not hide the rest', () => {
  const src = [
    "// routes live under /_design/* (mock artboards)",
    "<b>{$t('已包')}</b>",
    "<!-- 註解裡的中文 -->",
    "const x = '還沒包'",
    "/* 區塊",
    "   註解 */",
    "const y = tr('也包了', { n })",
    "const z = '刻意留中文' // i18n-ignore sample data",
    "<span>畫面上的字</span>",
  ].join('\n')
  const { keys, raw } = scanText(src)
  assert.deepEqual(keys.map((k) => k.key), ['已包', '也包了'])
  assert.deepEqual(raw.map((r) => r.line), [4, 9])
})

test('every wrapped key is a plain literal with an English entry', () => {
  const missing = []
  for (const f of liveFiles()) {
    for (const { key, line, q } of scan(f).keys) {
      if (q === '`' && key.includes('${')) { missing.push(`${f}:${line} template literal key — use {param}`); continue }
      if (CJK.test(key) && !(key in en)) missing.push(`${f}:${line} 「${key}」`)
    }
  }
  assert.deepEqual(missing, [], `keys without English:\n${missing.join('\n')}`)
})

test('ratchet: unwrapped Chinese per live file never grows (and baseline stays tight)', () => {
  const now = {}
  for (const f of liveFiles()) { const n = scan(f).raw.length; if (n) now[f] = n }
  if (process.env.I18N_WRITE_BASELINE) writeFileSync(BASELINE_URL, JSON.stringify(now, null, 2) + '\n')
  const grew = []
  const loose = []
  for (const f of new Set([...Object.keys(now), ...Object.keys(baseline)])) {
    const n = now[f] || 0
    const b = baseline[f] || 0
    if (n > b) grew.push(`${f}: ${b} → ${n}\n    ${scan(f).raw.map((r) => `${r.line}: ${r.text}`).join('\n    ')}`)
    else if (n < b) loose.push(`${f}: baseline ${b}, now ${n}`)
  }
  assert.deepEqual(grew, [], `new unwrapped Chinese (wrap in $t()/tr(), or mark the line // i18n-ignore with a reason):\n${grew.join('\n')}`)
  assert.deepEqual(loose, [], `baseline is looser than reality — rerun with I18N_WRITE_BASELINE=1:\n${loose.join('\n')}`)
})
