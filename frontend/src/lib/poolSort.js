// Media pool sort order. Keys are the backend's db.SORT_MAP keys — the list is
// paginated server-side, so sorting only the loaded page would be wrong.
export const POOL_SORTS = [
  ['date', '匯入 新→舊'],
  ['date_old', '匯入 舊→新'],
  ['shot_new', '拍攝 新→舊'],
  ['shot_old', '拍攝 舊→新'],
  ['name', '檔名 A→Z'],
  ['duration', '長度 長→短'],
  ['size', '大小 大→小'],
  ['rating', '評分'],
]
export const DEFAULT_POOL_SORT = 'date'
const KEY = 'arkiv.poolsort'
const VALID = new Set(POOL_SORTS.map(([k]) => k))

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
