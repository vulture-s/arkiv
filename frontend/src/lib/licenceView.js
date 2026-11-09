// What Settings → 授權 says about the tier, from /api/entitlements. Kept out of
// the component so the wording for each state is testable without a DOM.
import { tr } from './i18n.js'

export function describeTier(ent) {
  if (!ent) return { label: '—', detail: tr('讀不到授權狀態（後端沒有回應）') }
  if (ent.pro && ent.licence) {
    const l = ent.licence
    return { label: 'Pro', detail: tr('授權給 {licensee} · {key} · {issued}', { licensee: l.licensee, key: l.key, issued: l.issued }) }
  }
  if (ent.pro) return { label: 'Pro', detail: tr('由已安裝的 Pro 外掛授權') }
  if (ent.grandfathered) {
    return { label: tr('早期使用者'), detail: tr('這台電腦在免費額度實施前就在使用：無限專案與跨專案搜尋永久保留，不需要授權檔') }
  }
  return { label: tr('免費版'), detail: tr('最多 {limit} 個專案（目前 {used} 個）；跨專案搜尋需要 Pro', { limit: ent.free_project_limit, used: ent.projects_used }) }
}

// Read a picked file as a licence record. Rejects with a sentence a person can
// act on — a JSON parse error message is not one.
export async function readLicenceFile(file) {
  if (!file) throw new Error(tr('沒有選擇檔案'))
  if (file.size > 16 * 1024) throw new Error(tr('檔案太大，不是 arkiv 授權檔'))
  const text = await file.text()
  try { return JSON.parse(text) } catch { throw new Error(tr('這個檔案不是 arkiv 授權檔（不是 JSON）')) }
}

// The backend's reasons are written for support (English, exact); the buyer
// gets the same reason as a next step. Unknown reasons pass through verbatim
// rather than being guessed at. Each reason is a thunk so it is translated when
// the message is shown, not when the module loads.
const REASONS = [
  [/not signed/, () => tr('這份授權檔沒有簽章。2026-10-08 以前發出的授權需要重新發給你，請回信索取新檔')],
  [/signature does not match/, () => tr('簽章不符，檔案可能被修改過。請改用收到的原始檔案')],
  [/unknown key/, () => tr('認不得簽這份授權的金鑰。請先把 arkiv 更新到最新版再匯入')],
  [/withdrawn/, () => tr('這份授權已被撤銷')],
  [/not a licence record|missing|malformed|unsupported/, () => tr('這個檔案不是 arkiv 授權檔')],
  [/too large/, () => tr('檔案太大，不是 arkiv 授權檔')],
]
export function rejectReason(e) {
  const raw = e?.code === 'licence_rejected' ? e.body?.detail?.message : null
  if (!raw) return e?.message || String(e)
  const hit = REASONS.find(([re]) => re.test(raw))
  return hit ? hit[1]() : raw
}
