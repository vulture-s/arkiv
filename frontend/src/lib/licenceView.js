// What Settings → 授權 says about the tier, from /api/entitlements. Kept out of
// the component so the wording for each state is testable without a DOM.
export function describeTier(ent) {
  if (!ent) return { label: '—', detail: '讀不到授權狀態（後端沒有回應）' }
  if (ent.pro && ent.licence) {
    const l = ent.licence
    return { label: 'Pro', detail: `授權給 ${l.licensee} · ${l.key} · ${l.issued}` }
  }
  if (ent.pro) return { label: 'Pro', detail: '由已安裝的 Pro 外掛授權' }
  if (ent.grandfathered) {
    return { label: '早期使用者', detail: '這台電腦在免費額度實施前就在使用：無限專案與跨專案搜尋永久保留，不需要授權檔' }
  }
  return { label: '免費版', detail: `最多 ${ent.free_project_limit} 個專案（目前 ${ent.projects_used} 個）；跨專案搜尋需要 Pro` }
}

// Read a picked file as a licence record. Rejects with a sentence a person can
// act on — a JSON parse error message is not one.
export async function readLicenceFile(file) {
  if (!file) throw new Error('沒有選擇檔案')
  if (file.size > 16 * 1024) throw new Error('檔案太大，不是 arkiv 授權檔')
  const text = await file.text()
  try { return JSON.parse(text) } catch { throw new Error('這個檔案不是 arkiv 授權檔（不是 JSON）') }
}

// The backend's reasons are written for support (English, exact); the buyer
// gets the same reason as a next step. Unknown reasons pass through verbatim
// rather than being guessed at.
const REASONS = [
  [/not signed/, '這份授權檔沒有簽章。2026-10-08 以前發出的授權需要重新發給你，請回信索取新檔'],
  [/signature does not match/, '簽章不符，檔案可能被修改過。請改用收到的原始檔案'],
  [/unknown key/, '認不得簽這份授權的金鑰。請先把 arkiv 更新到最新版再匯入'],
  [/withdrawn/, '這份授權已被撤銷'],
  [/not a licence record|missing|malformed|unsupported/, '這個檔案不是 arkiv 授權檔'],
  [/too large/, '檔案太大，不是 arkiv 授權檔'],
]
export function rejectReason(e) {
  const raw = e?.code === 'licence_rejected' ? e.body?.detail?.message : null
  if (!raw) return e?.message || String(e)
  const hit = REASONS.find(([re]) => re.test(raw))
  return hit ? hit[1] : raw
}
