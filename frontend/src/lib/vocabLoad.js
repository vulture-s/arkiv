// Correction-dictionary load → what the editor may show and whether it may save.
//
// Audit 2026-10-09 K1: a failed GET used to become `rules = []` with no
// message, and Save PUTs the on-screen list over the WHOLE dictionary — so a
// backend blip at page-open turned "add one rule" into "delete every rule".
//   res: GET /api/corrections body ({rules, load_error}) or null
//   err: the thrown error, or null
// → { rules, canSave, msg }
export function vocabLoadState(res, err) {
  if (err) {
    return {
      rules: [],
      canSave: false,
      msg: `字典讀取失敗，為避免覆蓋既有規則，暫停儲存 — 重新整理後再試（${err.message || err}）`,
    }
  }
  const rules = (res && res.rules) || []
  if (res && res.load_error) {
    return {
      rules,
      canSave: true,
      msg: `⚠ ${res.load_error} — 畫面上不是原本的規則；儲存會改寫字典，舊檔會留成 corrections.json.bak`,
    }
  }
  return { rules, canSave: true, msg: '' }
}
