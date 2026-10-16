// Transcript 「選字 → 校正」 — the pure half of the inspector's correction flow.
//
// The inspector turns a DOM selection into plain facts (which segment each end of
// the selection sits in, the selected string, that segment's text) and hands them
// here; everything that decides *what gets sent* to POST /api/corrections/learn
// lives in this file so it can be tested without a DOM.
//
// Contract with the backend (corrections.learn): `from` must be non-empty and
// differ from `to`; the rule is upserted on `from`. A global rule rewrites EVERY
// occurrence of `from` in the project, so the `from` we send must be exactly the
// characters the user selected inside one segment — never text spliced across two
// segments (no stored segment contains that string, so it would be a rule that
// can never fire on the text the user was looking at).

export const MAX_FROM = 120
export const MAX_TO = 200
const CROSS = '選取跨了兩段以上 —— 一次只能校正同一段裡的字'

// sel: { startSeg, endSeg, text, segText }
//   startSeg / endSeg — segment index of each end of the selection, or null when
//                       that end is outside any transcript text node (timecode
//                       column, another panel…)
//   text              — window.getSelection().toString()
//   segText           — the text of segment startSeg as rendered
// → { ok: true, from, seg } | { ok: false, reason }   (reason is user-facing)
export function selectionToFrom(sel) {
  if (!sel) return { ok: false, reason: 'empty' }
  const raw = typeof sel.text === 'string' ? sel.text : ''
  if (!raw.trim()) return { ok: false, reason: 'empty' }
  const { startSeg, endSeg } = sel
  if (startSeg == null || endSeg == null) {
    // One end landed outside the text spans. If the selection spans a line
    // break it ran into another segment; otherwise it grabbed the timecode.
    if (/[\r\n]/.test(raw.trim())) {
      return { ok: false, reason: CROSS }
    }
    return { ok: false, reason: '請只選取逐字稿文字（不要包含時間碼）' }
  }
  if (startSeg !== endSeg) {
    return { ok: false, reason: CROSS }
  }
  // Selections pick up stray whitespace/newlines at the edges (double-click on a
  // word followed by a space, triple-click a line). Trim them; anything inside
  // is kept verbatim because the rule matches literally.
  const from = raw.replace(/^\s+|\s+$/g, '')
  if (/[\r\n]/.test(from)) {
    return { ok: false, reason: CROSS }
  }
  if (typeof sel.segText === 'string' && !sel.segText.includes(from)) {
    // Defensive: the DOM said one segment but the string isn't in it (e.g. the
    // segment re-rendered under the selection). Sending it would teach a rule
    // that matches nothing the user can see.
    return { ok: false, reason: '選取的文字不在這一段裡，請重新選取' }
  }
  if (from.length > MAX_FROM) {
    return { ok: false, reason: `選取太長（${from.length} 字）—— 校正請選一個詞或一小段，最多 ${MAX_FROM} 字` }
  }
  return { ok: true, from, seg: startSeg }
}

// → { ok: true, from, to } | { ok: false, reason }
export function validateCorrection(from, to) {
  const f = typeof from === 'string' ? from.trim() : ''
  const t = typeof to === 'string' ? to.trim() : ''
  if (!f) return { ok: false, reason: '沒有要校正的文字' }
  if (!t) return { ok: false, reason: '請輸入正確寫法' }
  if (t === f) return { ok: false, reason: '正確寫法跟原文一樣' }
  if (t.length > MAX_TO) return { ok: false, reason: `正確寫法太長（最多 ${MAX_TO} 字）` }
  return { ok: true, from: f, to: t }
}

// Toast text for a successful learn response.
// res: { rule:{from,to}, created, hotword, applied?:{media_updated,total_hits} }
export function describeLearn(res, applyRequested) {
  const r = (res && res.rule) || {}
  const parts = [`已學會「${r.from}」→「${r.to}」${res && res.created === false ? '（更新既有規則）' : ''}`]
  if (res && res.hotword) parts.push('已加入下次轉錄的熱詞')
  if (applyRequested) {
    const a = res && res.applied
    if (!a) parts.push('（未回報改寫結果）')
    else if (!a.media_updated) parts.push('本專案逐字稿沒有命中，未改寫')
    else parts.push(`已改寫 ${a.media_updated} 支素材、${a.total_hits} 處（可在 設定 → 批次套用 還原）`)
  } else {
    parts.push('未改寫既有逐字稿')
  }
  return parts.join('；')
}
