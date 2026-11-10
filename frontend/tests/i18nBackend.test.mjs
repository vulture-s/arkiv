// #634: backend API text reaches the English UI through the same dictionary.
// backend-keys.json is generated from the Python code (scripts/i18n_backend_keys.py;
// tests/test_i18n_backend_keys.py fails if it drifts), so "every key has English"
// here means every string the API can send has English.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { localePref, trApi, apiText, apiErrorText } from '../src/lib/i18n.js'
import en from '../src/lib/i18n/en/index.js'

const backend = JSON.parse(readFileSync(new URL('../src/lib/i18n/backend-keys.json', import.meta.url), 'utf8'))
const CJK = /[㐀-鿿]/
const placeholders = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

test('every backend key has an English entry with the same placeholders', () => {
  assert.ok(backend.keys.length > 50, 'backend-keys.json looks empty')
  const missing = backend.keys.filter((k) => !(k in en))
  assert.deepEqual(missing, [], `backend keys without English:\n${missing.join('\n')}`)
  for (const k of backend.keys) {
    assert.ok(!CJK.test(en[k]), `untranslated Chinese in en value for 「${k}」`)
    assert.deepEqual(placeholders(en[k]), placeholders(k), `placeholder mismatch for 「${k}」`)
  }
})

test('trApi / apiText: zh-TW unchanged, English via key+params, fixed text via itself, old bodies still work', () => {
  const body = {
    detail: '找不到媒體檔案：/Volumes/A/x.mov',
    detail_key: '找不到媒體檔案：{path}',
    detail_params: { path: '/Volumes/A/x.mov' },
  }
  localePref.set('zh-TW')
  assert.equal(apiText(body, 'detail'), '找不到媒體檔案：/Volumes/A/x.mov')
  localePref.set('en')
  try {
    assert.equal(apiText(body, 'detail'), 'Media file not found: /Volumes/A/x.mov')
    assert.equal(apiText({ detail: '找不到這支素材' }, 'detail'), 'Clip not found', 'fixed sentence needs no key')
    assert.equal(apiText({ message: '全部 proxy 已存在' }), 'All proxies already exist')
    assert.equal(apiText({ message: 'done', message_key: null }), 'done')
    assert.equal(apiText({ message: '字典裡沒有的後端句子' }), '字典裡沒有的後端句子', 'unknown text falls back to Chinese')
    assert.equal(
      trApi(null, '無法存取這個路徑（{reason}）。如果是網路磁碟，請先在{fm}開啟這個位置並登入，再回來{action}。',
        { reason: '拒絕存取。', fm: '檔案總管', action: '匯出' }, ['fm', 'action']),
      'Cannot access this path (拒絕存取。). If it is a network drive, open this location in File Explorer and sign in first, then come back and retry (Export).',
      'word params translate, OS reason stays verbatim',
    )
    assert.equal(apiText(undefined), undefined)
  } finally {
    localePref.set('zh-TW')
  }
})

test('user data in params is never translated — only the params the backend marks (#634 R1)', () => {
  localePref.set('en')
  try {
    // A destination project literally named 匯出 (bins copy, routers/bins.py).
    const body = { detail: '找不到目的專案：匯出', detail_key: '找不到目的專案：{dest}', detail_params: { dest: '匯出' } }
    assert.equal(apiText(body, 'detail'), 'Destination project not found: 匯出')
    // ...while a marked word param is translated and the OS reason is not.
    const pa = {
      detail: 'x',
      detail_key: '無法存取這個路徑（{reason}）。如果是網路磁碟，請先在{fm}開啟這個位置並登入，再回來{action}。',
      detail_params: { reason: '匯出', fm: '檔案總管', action: '加入' },
      detail_tr_params: ['fm', 'action'],
    }
    assert.equal(apiText(pa, 'detail'),
      'Cannot access this path (匯出). If it is a network drive, open this location in File Explorer and sign in first, then come back and retry (Add).')
  } finally {
    localePref.set('zh-TW')
  }
})

test('apiErrorText: plain / keyed / structured detail, and non-API errors (#634 R1)', () => {
  localePref.set('en')
  try {
    assert.equal(apiErrorText({ body: { detail: '來源路徑不存在' }, message: 'arkiv API 400 …' }), 'The source path does not exist')
    assert.equal(apiErrorText({ body: { detail: '非法路徑：../x', detail_key: '非法路徑：{path}', detail_params: { path: '../x' } } }), 'Invalid path: ../x')
    assert.equal(apiErrorText({ body: { detail: { code: 'x', message: '找不到' } } }), 'Not found')
    assert.equal(apiErrorText({ body: null, message: 'network down' }), 'network down')
    assert.equal(apiErrorText('boom'), 'boom')
  } finally {
    localePref.set('zh-TW')
  }
})
