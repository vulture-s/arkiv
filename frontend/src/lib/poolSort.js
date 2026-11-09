// Media pool sort order. Keys are the backend's db.SORT_MAP keys — the list is
// paginated server-side, so sorting only the loaded page would be wrong.
// (tests/test_db.py checks every key here exists in SORT_MAP.)
// Labels are zh-TW i18n keys (#581); `t` only marks them for the coverage scan —
// the component renders them with $t(label) so they follow a locale switch.
const t = (s) => s
export const POOL_SORTS = [
  ['date', t('匯入 新→舊')],
  ['date_old', t('匯入 舊→新')],
  ['shot_new', t('拍攝 新→舊')],
  ['shot_old', t('拍攝 舊→新')],
  ['name', t('檔名 A→Z')],
  ['name_desc', t('檔名 Z→A')],
  ['duration', t('時長 長→短')],
  ['duration_asc', t('時長 短→長')],
  ['size', t('容量 大→小')],
  ['size_asc', t('容量 小→大')],
  ['rating', t('評分')],
]
export const DEFAULT_POOL_SORT = 'date'
const KEY = 'arkiv.poolsort'
const VALID = new Set(POOL_SORTS.map(([k]) => k))

// List-view column headers → their sorts, first = the direction a first click
// picks (newest / largest / longest first; names A→Z). A column with one entry
// only turns on.
export const COLUMN_SORTS = {
  name: ['name', t('name_desc')],
  rating: ['rating'],
  duration: ['duration', t('duration_asc')],
  size: ['size', t('size_asc')],
  shot: ['shot_new', t('shot_old')],
  date: ['date', t('date_old')],
}
// Which way each sort runs, for the header arrow.
const ASCENDING = new Set(['date_old', 'shot_old', 'name', 'duration_asc', 'size_asc'])

// Clicking a header: another column → its first direction; the active column →
// the other direction.
export function nextColumnSort(column, current) {
  const opts = COLUMN_SORTS[column]
  if (!opts) return current
  const i = opts.indexOf(current)
  return i < 0 ? opts[0] : opts[(i + 1) % opts.length]
}

// '↑' / '↓' when `column` is the active sort, '' otherwise.
export function columnArrow(column, current) {
  const opts = COLUMN_SORTS[column]
  if (!opts || !opts.includes(current)) return ''
  if (column === 'rating') return '↓'
  return ASCENDING.has(current) ? '↑' : '↓'
}

export function loadPoolSort(storage = globalThis.localStorage) {
  try {
    const v = storage && storage.getItem(KEY)
    return VALID.has(v) ? v : DEFAULT_POOL_SORT
  } catch { return DEFAULT_POOL_SORT /* private mode */ }
}

export function savePoolSort(v, storage = globalThis.localStorage) {
  if (!VALID.has(v)) return
  try { storage && storage.setItem(KEY, v) } catch { /* private mode / quota */ }
}
