// UI language (#581) — gettext-style: the zh-TW source string IS the key.
//
// Why the Chinese string and not a symbolic key ('settings.theme'): the UI was
// written in Chinese inline, so keeping the source text in the component keeps
// the code readable, keeps the diff to "wrap it in $t()", and a missing English
// entry degrades to the original Chinese instead of a raw key. The coverage test
// (tests/i18n.test.mjs) is what guarantees every wrapped string has an English
// entry and that no new unwrapped Chinese creeps back in.
//
//   components:  {$t('已移至回收桶 · {name}', { name })}
//   plain .js:   tr('已移至回收桶 · {name}', { name })   (resolved at call time)
import { writable, derived, get } from 'svelte/store'
import en from './i18n/en/index.js'

const LOCALE_KEY = 'arkiv.locale'
export const LOCALES = ['system', 'zh-TW', 'en']
const DICTS = { en }

function readLocale() {
  try {
    const v = localStorage.getItem(LOCALE_KEY)
    return LOCALES.includes(v) ? v : 'system'
  } catch {
    return 'system'
  }
}

// Only a real browser/WebView decides "system". Node (node:test) also has a
// `navigator` (en-US), which would silently flip every lib/*.js message the
// existing tests pin in Chinese — so outside a window the source language wins.
function systemLocale() {
  if (typeof window === 'undefined') return 'zh-TW'
  try {
    const l = (navigator.languages && navigator.languages[0]) || navigator.language || ''
    return /^zh/i.test(l) ? 'zh-TW' : 'en'
  } catch {
    return 'zh-TW'
  }
}

export function resolveLocale(pref) {
  return pref === 'system' ? systemLocale() : pref
}

// 'system' | 'zh-TW' | 'en' — what the user picked in Settings
export const localePref = writable(readLocale())
localePref.subscribe((v) => {
  try { localStorage.setItem(LOCALE_KEY, v) } catch { /* private mode / quota */ }
})

// 'zh-TW' | 'en' — what is actually shown
export const locale = derived(localePref, resolveLocale)
locale.subscribe((l) => {
  try { document.documentElement.lang = l } catch { /* no DOM (node:test) */ }
})

export function translate(loc, s, params) {
  let out = s
  if (loc !== 'zh-TW') {
    const d = DICTS[loc]
    if (d && Object.prototype.hasOwnProperty.call(d, s)) out = d[s]
  }
  if (params) {
    out = out.replace(/\{(\w+)\}/g, (m, k) => (Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m))
  }
  return out
}

// Reactive translator for components: `$t('…')` re-renders when the locale changes.
export const t = derived(locale, (l) => (s, params) => translate(l, s, params))

// Non-reactive, for plain .js modules (toast text, error messages) — resolved
// when the message is built, which is when the user reads it.
export function tr(s, params) {
  return translate(get(locale), s, params)
}

// Backend API text (#634). The backend keeps sending the formatted zh-TW text
// (old clients, MCP callers and contract tests see no change); when the text
// had values in it, it ALSO sends the template as `<field>_key` and the values
// as `<field>_params` (apimsg.py). A fixed sentence is looked up as-is, a
// template is translated then filled. Param values are user data (a project
// name, a path, an OS reason) and are shown VERBATIM — a project literally
// named 匯出 must not turn into "Export". Only the params the backend lists in
// `<field>_tr_params` (words it chose itself, e.g. the action verb) are looked up.
export function trApi(text, key, params, trParams) {
  if (typeof key === 'string' && key) {
    let p = params && typeof params === 'object' ? { ...params } : undefined
    if (p && Array.isArray(trParams)) {
      for (const k of trParams) if (typeof p[k] === 'string') p[k] = tr(p[k])
    }
    return tr(key, p)
  }
  return typeof text === 'string' ? tr(text) : text
}

// `apiText(res)` → res.message in the UI language; `apiText(body, 'detail')`
// for an error body. Non-objects pass through.
export function apiText(body, field = 'message') {
  if (!body || typeof body !== 'object') return body
  return trApi(body[field], body[field + '_key'], body[field + '_params'], body[field + '_tr_params'])
}

// The human sentence of a thrown ApiError, in the UI language: the backend's
// own detail (plain, keyed, or a structured {code, message}) when there is one,
// else the error's message. Replaces the raw `e.body?.detail || e.message`.
export function apiErrorText(e) {
  const b = e && e.body
  if (b && typeof b === 'object') {
    const d = b.detail
    if (typeof d === 'string' && d) return apiText(b, 'detail')
    if (d && typeof d === 'object' && typeof d.message === 'string' && d.message) return tr(d.message)
  }
  return (e && e.message) || String(e)
}
