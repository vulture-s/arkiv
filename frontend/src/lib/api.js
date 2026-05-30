// arkiv API client — thin fetch layer over the FastAPI backend (server.py).
// Dev: Vite proxies /api + /thumbnails → http://localhost:8501 (vite.config.js).
// Prod/Tauri: set VITE_API_URL to the backend origin.
// Loopback (127.0.0.1) is token-free (ARKIV_TRUST_LOOPBACK); remote needs a
// Bearer token — pass via setToken().
//
// Shapes confirmed against a live empty DB (2026-05-30):
//   /api/stats    → {total, with_transcript, with_thumb, total_duration_s,
//                    total_size_mb, langs:{}, rating:{good,ng,review,unrated}, top_tags:[]}
//   /api/projects → {projects:[], total}
//   /api/media    → {items:[], total, search:bool}
//   /api/tags     → []

const BASE = import.meta.env?.VITE_API_URL ?? ''

let _token = null
export function setToken(t) {
  _token = t || null
}

class ApiError extends Error {
  constructor(status, path, body) {
    super(`arkiv API ${status} on ${path}`)
    this.status = status
    this.path = path
    this.body = body
  }
}

async function req(path, { method = 'GET', body, signal } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (_token) headers['Authorization'] = `Bearer ${_token}`
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  })
  if (!res.ok) {
    let detail = null
    try { detail = await res.json() } catch { /* non-json */ }
    throw new ApiError(res.status, path, detail)
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

const qs = (params = {}) => {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    u.set(k, Array.isArray(v) ? v.join(',') : String(v))
  }
  const s = u.toString()
  return s ? `?${s}` : ''
}

// ---- reads ----
export const getStats = (opts) => req('/api/stats', opts)
export const getProjects = (opts) => req('/api/projects', opts)
export const getProjectsHealth = (opts) => req('/api/projects/health', opts)
export const getTags = (opts) => req('/api/tags', opts)

// /api/media?limit&offset&projects&tag&rating  → {items, total, search}
export const getMedia = (params = {}, opts) => req(`/api/media${qs(params)}`, opts)
export const getMediaDetail = (id, opts) => req(`/api/media/${id}`, opts)
export const getWaveform = (id, opts) => req(`/api/media/${id}/waveform`, opts)
export const getScenes = (id, opts) => req(`/api/media/${id}/scenes`, opts)
export const getMediaTags = (id, opts) => req(`/api/media/${id}/tags`, opts)

// /api/search/all?q&projects&tag
export const search = (q, params = {}, opts) =>
  req(`/api/search/all${qs({ q, ...params })}`, opts)

// ---- asset URLs (no fetch — for <img>/<video src>) ----
export const thumbUrl = (id) => `${BASE}/api/media/${id}/thumb`
export const streamUrl = (id) => `${BASE}/api/stream/${id}`

// ---- writes ----
export const setRating = (id, rating, opts) =>
  req(`/api/media/${id}/rating`, { method: 'PATCH', body: { rating }, ...opts })

export { ApiError }
