import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeTier, readLicenceFile } from '../src/lib/licenceView.js'

const base = { pro: false, grandfathered: false, free_project_limit: 3, projects_used: 2, licence: null }

test('each tier state reads as what the backend decided', () => {
  assert.equal(describeTier(null).label, '—')
  assert.equal(describeTier(base).label, '免費版')
  assert.match(describeTier(base).detail, /最多 3 個專案（目前 2 個）/)
  assert.equal(describeTier({ ...base, grandfathered: true }).label, '早期使用者')
  const pro = describeTier({ ...base, pro: true, licence: { licensee: 'pen', key: 'VIP-001', issued: '2026-09-01' } })
  assert.equal(pro.label, 'Pro')
  assert.match(pro.detail, /pen · VIP-001 · 2026-09-01/)
  assert.equal(describeTier({ ...base, pro: true }).label, 'Pro') // add-on route, no file
})

const fileOf = (text, size = text.length) => ({ size, text: async () => text })

test('a picked file becomes a record, or a sentence a person can act on', async () => {
  assert.deepEqual(await readLicenceFile(fileOf('{"key":"K"}')), { key: 'K' })
  await assert.rejects(readLicenceFile(fileOf('not json')), /不是 arkiv 授權檔（不是 JSON）/)
  await assert.rejects(readLicenceFile(fileOf('{}', 20000)), /檔案太大/)
  await assert.rejects(readLicenceFile(null), /沒有選擇檔案/)
})

test('a rejection reads as a next step; unknown reasons and other errors pass through', async () => {
  const { rejectReason } = await import('../src/lib/licenceView.js')
  const rej = (message) => ({ code: 'licence_rejected', body: { detail: { code: 'licence_rejected', message } }, message: 'arkiv API 400 on /api/licence：' + message })
  assert.match(rejectReason(rej('this licence file is not signed; licences issued before 2026-10-08 need to be re-issued by the licensor')), /沒有簽章/)
  assert.match(rejectReason(rej('signature does not match')), /簽章不符/)
  assert.match(rejectReason(rej('licence was signed with an unknown key')), /更新到最新版/)
  assert.equal(rejectReason(rej('something new')), 'something new')
  assert.equal(rejectReason(new Error('這個檔案不是 arkiv 授權檔（不是 JSON）')), '這個檔案不是 arkiv 授權檔（不是 JSON）')
})
