// Desktop library switching (#561).
//
// The desktop app used to have exactly one library (app_local_data_dir/arkiv).
// Switching means restarting the backend on another root, which only the Tauri
// shell can do — so the commands live in main.rs (`library_state`,
// `switch_library`) and are reached through `window.__TAURI__.core.invoke`, the
// same global pickFolder.js uses. In a plain browser none of this exists and the
// sidebar stays the read-only list it always was.

/** True only inside the desktop shell. */
export function canSwitchLibrary() {
  return typeof window !== 'undefined' && !!window.__TAURI__?.core?.invoke
}

/** {active, default} absolute paths, or null outside the desktop shell. */
export async function libraryState() {
  if (!canSwitchLibrary()) return null
  return window.__TAURI__.core.invoke('library_state')
}

/** Restart the backend on `path`. On success the shell reloads the window onto
 *  the new backend, so the promise may never resolve; on refusal it rejects with
 *  a human-readable reason (busy, missing folder, new backend did not start). */
export async function switchLibrary(path) {
  if (!canSwitchLibrary()) throw new Error('只有桌面 App 能切換素材庫')
  return window.__TAURI__.core.invoke('switch_library', { path })
}

const JOB_LABELS = {
  ingest: '匯入',
  offload: '卸載（DIT 拷卡）',
  embed_rebuild: '重建語意索引',
  retranscribe: '重新轉錄',
  proxy_build: '產生 proxy',
  editor_proxy_build: '產生剪輯 proxy',
  sample_seed: '載入範例片',
}

/** /api/library/busy → the sentence shown instead of switching. null = not busy. */
export function busyMessage(busy) {
  if (!busy || !busy.busy) return null
  const names = (busy.jobs || []).map((j) => JOB_LABELS[j] || j)
  return `${names.join('、') || '有工作'}還在進行，等它完成再切換素材庫（切換會重啟後端，進行中的工作會被中斷）。`
}

const norm = (p) => String(p || '').replace(/[\\/]+$/, '').toLowerCase()

/** Last path segment, for both separators. */
export function baseName(path) {
  const parts = String(path || '').split(/[\\/]+/).filter(Boolean)
  return parts.length ? parts[parts.length - 1] : '素材庫'
}

/** `base`, or `base 2`, `base 3`… — the registry refuses duplicate names (409). */
export function uniqueName(base, existing) {
  const taken = new Set((existing || []).map((n) => String(n)))
  if (!taken.has(base)) return base
  for (let i = 2; ; i++) if (!taken.has(`${base} ${i}`)) return `${base} ${i}`
}

/**
 * Sidebar rows. Registry entries come first as before; `is_current` is the
 * backend's answer to "which one is open" (it holds PROJECT_ROOT, we don't).
 * In the desktop shell the built-in library also gets a row when the registry
 * does not list it — otherwise switching away from it leaves no way back.
 */
export function libraryRows({ registryProjects, stats, projectName, libState }) {
  if (!stats) return null
  const open = { id: 'cur', name: projectName, count: stats.total, active: true, path: libState?.active ?? null }
  const reg = registryProjects || []
  const rows = reg.map((p) => ({
    id: `reg:${p.name}`,
    name: p.name,
    count: p.is_current ? stats.total : null,
    active: !!p.is_current,
    path: p.path || null,
  }))
  const out = rows.some((r) => r.active) ? rows : [open, ...rows]
  if (libState?.default) {
    const known = out.some((r) => r.path && norm(r.path) === norm(libState.default))
    const openIsDefault = norm(libState.active) === norm(libState.default)
    if (!known && !openIsDefault) {
      out.push({ id: 'builtin', name: '內建素材庫', count: null, active: false, path: libState.default })
    }
  }
  return out
}
