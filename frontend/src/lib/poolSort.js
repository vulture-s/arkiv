// Media pool sort order. Keys are the backend's db.SORT_MAP keys — the list is
// paginated server-side, so sorting only the loaded page would be wrong.
// (tests/test_db.py checks every key here exists in SORT_MAP.)
export const POOL_SORTS = [
  ['date', '匯入 新→舊'],
  ['date_old', '匯入 舊→新'],
  ['shot_new', '拍攝 新→舊'],
  ['shot_old', '拍攝 舊→新'],
  ['name', '檔名 A→Z'],
  ['name_desc', '檔名 Z→A'],
  ['duration', '時長 長→短'],
  ['duration_asc', '時長 短→長'],
  ['size', '容量 大→小'],
  ['size_asc', '容量 小→大'],
  ['rating', '評分'],
]
export const DEFAULT_POOL_SORT = 'date'
const KEY = 'arkiv.poolsort'
const VALID = new Set(POOL_SORTS.map(([k]) => k))

// List-view column headers → their sorts, first = the direction a first click
// picks (newest / largest / longest first; names A→Z). A column with one entry
// only turns on.
export const COLUMN_SORTS = {
  name: ['name', 'name_desc'],
  rating: ['rating'],
  duration: ['duration', 'duration_asc'],
  size: ['size', 'size_asc'],
  shot: ['shot_new', 'shot_old'],
  date: ['date', 'date_old'],
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
