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
    // API responses are live data (reachability status, search hits, project
    // health) — never let the browser serve a heuristically-cached copy, or a
    // re-opened 精選集 shows stale reachability and the whole point (surfacing a
    // moved/offline source) is defeated.
    cache: 'no-store',
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
// ---- project registry mutations (projects_write; token-free on loopback) ----
// POST /api/projects {name, path, tags} → project dict (409 if name exists).
export const addProject = (body, opts) => req('/api/projects', { method: 'POST', body, ...opts })
// DELETE /api/projects/{name} → removed project dict (404 if unknown).
export const deleteProject = (name, opts) => req(`/api/projects/${encodeURIComponent(name)}`, { method: 'DELETE', ...opts })
// POST /api/projects/sync → {projects, total} (refresh last_indexed_at from DB).
export const syncProjects = (opts) => req('/api/projects/sync', { method: 'POST', ...opts })
// ---- cross-library 精選集 (bins): persistent named selections spanning projects ----
// A bin references clips by (project_name, media_id) — the identity that survives
// federation's path sanitization. GET /api/bins/{id} returns each item with a
// reachability `status` (ok | project_unregistered | db_missing | nas_unmounted |
// row_missing | file_missing | …) — never an absolute path (Phase 16.2).
export const getBins = (opts) => req('/api/bins', opts)
export const createBin = (name, opts) => req('/api/bins', { method: 'POST', body: { name }, ...opts })
export const getBin = (id, opts) => req(`/api/bins/${encodeURIComponent(id)}`, opts)
export const renameBin = (id, name, opts) =>
  req(`/api/bins/${encodeURIComponent(id)}`, { method: 'PATCH', body: { name }, ...opts })
export const deleteBin = (id, opts) =>
  req(`/api/bins/${encodeURIComponent(id)}`, { method: 'DELETE', ...opts })
// items = [{project_name, media_id, filename?}] — deduped server-side by (project,media).
export const addBinItems = (id, items, opts) =>
  req(`/api/bins/${encodeURIComponent(id)}/items`, { method: 'POST', body: { items }, ...opts })
export const removeBinItem = (id, project_name, media_id, opts) =>
  req(`/api/bins/${encodeURIComponent(id)}/items`, { method: 'DELETE', body: { project_name, media_id }, ...opts })

export const getTags = (opts) => req('/api/tags', opts)
// /api/collections → {collections:[{key,title,category,count,items:[{id,filename,thumb,score}]}], total}
export const getCollections = (opts) => req('/api/collections', opts)

// POST /api/chat {prompt, conversation_id?, project_scope?} →
//   {conversation_id, assistant_text, scene_ids[], intent, tokens_used, latency_ms}
// Requires chat_write scope (token-free on loopback).
export const chat = (prompt, conversationId = null, opts) =>
  req('/api/chat', { method: 'POST', body: { prompt, conversation_id: conversationId }, ...opts })

// Chat history. Requires chat_read (token-free on loopback; remote sees only its
// own conversations).
// GET /api/chat/conversations?limit → {conversations:[{id,title,project_scope_json,created_at,updated_at}]}
export const listConversations = (limit = 50, opts) =>
  req(`/api/chat/conversations${qs({ limit })}`, opts)
// GET /api/chat/history/{id}?limit → {conversation:{…}, messages:[{role,content,intent,scene_ids_json,…}]}
export const getChatHistory = (id, limit = 200, opts) =>
  req(`/api/chat/history/${id}${qs({ limit })}`, opts)

// POST /api/ingest/scan {path} — quick scan, no processing. Returns
// {total, new, manifest:{video,audio,unsupported,total_size_mb}, files:[…]}.
// Powers the setup dialog's MANIFEST panel. Requires ingest_write.
export const scanMedia = (path, opts) =>
  req('/api/ingest/scan', { method: 'POST', body: { path }, ...opts })

// GET /api/ingest/engines (brick 4) — real transcription picker options:
// {whisper_modes:[{mode,name}], default_mode, languages:[{code,label}]}.
export const getIngestEngines = (opts) => req('/api/ingest/engines', opts)

// ---- correction dictionary (Phase 9.6) ----
// One per-project dictionary, two paths: pre-rules feed the Whisper hotword
// list; post-rules batch-rewrite stored transcripts (recorrect).
// GET → {rules:[{from,to,scope,pre,post}]}
export const getCorrections = (opts) => req('/api/corrections', opts)
// PUT replaces the whole dictionary → {ok, rules, count}
export const putCorrections = (rules, opts) =>
  req('/api/corrections', { method: 'PUT', body: { rules }, ...opts })
// POST recorrect, dry-run by default — preview only, writes nothing.
// → {dry_run:true, rules:[{from,to,scope,hits}], media_affected, total_hits, affected:[…]}
export const recorrectPreview = (opts) =>
  req('/api/recorrect', { method: 'POST', ...opts })
// Apply (dry_run=0); rebuild=1 chains the embedding rebuild.
// → {dry_run:false, media_updated, total_hits, backup, embed_rebuild_started}
export const recorrectApply = (rebuild = false, opts) =>
  req(`/api/recorrect${qs({ dry_run: 0, rebuild: rebuild ? 1 : 0 })}`, { method: 'POST', ...opts })
// GET → {backups:[name]} (newest first)
export const getRecorrectBackups = (opts) => req('/api/recorrect/backups', opts)
// POST restore from a backup (latest if name omitted) → {restored, backup}
export const recorrectRevert = (backup = null, opts) =>
  req('/api/recorrect/revert', { method: 'POST', body: { backup }, ...opts })

// Batch retranscribe (2a, Phase 9.6d) — re-run Whisper across the whole project
// so new hotwords take effect. Long-running + single-flight; poll status.
// → {queued} | 409 if already running
export const retranscribeAll = (backup = true, language = null, opts) =>
  req('/api/retranscribe-all', { method: 'POST', body: { backup, language }, ...opts })
// → {total, done, failed, current, running, backup}
export const retranscribeAllStatus = (opts) => req('/api/retranscribe-all/status', opts)

// ---- per-language transcripts (Phase 9.7 G2) ----
// → {active_lang, transcripts:[{lang, transcript, segments_json, words_json, active}]}
export const getTranscripts = (id, opts) => req(`/api/media/${id}/transcripts`, opts)
// Make an archived language the active (indexed/exported) transcript → {ok, active_lang}
export const activateTranscript = (id, lang, opts) =>
  req(`/api/media/${id}/transcript/activate`, { method: 'POST', body: { lang }, ...opts })

// POST /api/ingest/ws {path, limit, …options} — trigger ingest with WS progress.
// `options` forwards the engine flags the setup dialog exposes (skip_vision,
// refresh, recursive, max_failures, skip_failed, no_embed); omitted keys keep
// the backend defaults. Requires ingest_write (token-free on loopback; the dev
// proxy injects the header). Goes through req() so a setToken() token is attached
// in direct remote deployments — a raw fetch here would 401 once auth was required.
export const ingestWs = (path, limit = 0, options = {}, opts) =>
  req('/api/ingest/ws', { method: 'POST', body: { path, limit, ...options }, ...opts })

// POST /api/embed/rebuild — drop + rebuild the ChromaDB semantic index from all
// media (runs in the background). Requires ingest_write (token-free on loopback).
// → {message, queued}
export const rebuildEmbedIndex = (opts) =>
  req('/api/embed/rebuild', { method: 'POST', ...opts })

// ---- DIT offload (card → backup) ----
// POST /api/offload/preview {src, organize?, include_heic?, limit?} → read-only
// layout {src, count, organize, files:[{source, rel, size_mb}]}. videos_write.
export const offloadPreview = (body, opts) =>
  req('/api/offload/preview', { method: 'POST', body, ...opts })

// POST /api/offload {src, dst:[…], organize?, include_heic?} → ndjson stream of
// {type:"dst_start"|"file"|"done", …}. req() can't stream, so return the raw
// Response for the caller to read line-by-line; auth header attached when set
// (token-free on loopback). Throws ApiError on a non-OK status (e.g. bad path).
export async function offloadRun(body, { signal } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (_token) headers['Authorization'] = `Bearer ${_token}`
  const res = await fetch(`${BASE}/api/offload`, {
    method: 'POST', headers, body: JSON.stringify(body), signal,
  })
  if (!res.ok) {
    let detail = null
    try { detail = await res.json() } catch { /* non-json */ }
    throw new ApiError(res.status, '/api/offload', detail)
  }
  return res
}

// /api/media?limit&offset&projects&tag&rating  → {items, total, search}
export const getMedia = (params = {}, opts) => req(`/api/media${qs(params)}`, opts)
export const getMediaDetail = (id, opts) => req(`/api/media/${id}`, opts)
export const getWaveform = (id, opts) => req(`/api/media/${id}/waveform`, opts)
export const getScenes = (id, opts) => req(`/api/media/${id}/scenes`, opts)
export const getMediaTags = (id, opts) => req(`/api/media/${id}/tags`, opts)

// /api/search/all?q&projects&tag
export const search = (q, params = {}, opts) =>
  req(`/api/search/all${qs({ q, ...params })}`, opts)

// G6 — structured query (typed conditions, AND/OR, optional semantic leg)
export const structuredQuery = (body, opts) =>
  req('/api/search/query', { method: 'POST', body, ...opts })

// G5② — persisted settings (curated overrides; default ← global ← project)
export const getSettings = (scope = 'global', opts) =>
  req(`/api/settings${qs({ scope })}`, opts)
export const putSettings = (values, scope = 'global', opts) =>
  req('/api/settings', { method: 'PUT', body: { scope, values }, ...opts })
export const resetSetting = (key, scope = 'global', opts) =>
  req(`/api/settings/${encodeURIComponent(key)}${qs({ scope })}`, { method: 'DELETE', ...opts })

// ---- asset URLs (no fetch — for <img>/<video src>) ----
// Thumbnails are served by a static mount at /thumbnails/<basename>, NOT a
// per-id route. /api/media items carry `thumbnail_path` (absolute fs path);
// derive the served URL from its basename. Verified: /thumbnails/C3742.jpg → 200.
// Split on both / and \ so Windows paths (C:\…\thumbnails\foo.jpg) extract the basename.
// /thumbnails now requires videos_read (audit M12). An <img src> can't send an
// Authorization header, so carry the token as ?token= when set — no-op on loopback
// (token unset), same as streamUrl(). Without this, remote token deployments 401.
export const thumbUrlFromPath = (thumbnailPath) =>
  thumbnailPath ? appendToken(`${BASE}/thumbnails/${thumbnailPath.split(/[/\\]/).pop()}`) : null
// Append ?token= to a URL when a token is set — for WebSocket / media-asset URLs
// that can't send an Authorization header. No-op on loopback / behind the dev
// proxy (token unset there).
export const appendToken = (url) =>
  _token ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(_token)}` : url
// /api/stream now requires videos_read. A <video src> can't send an Authorization
// header, so when a token is set (direct remote deployment) carry it as ?token=.
export const streamUrl = (id) => appendToken(`${BASE}/api/stream/${id}`)
// EDL/FCPXML/SRT/VTT/CSV export download URL for a clip (optional in/out trim).
export const exportUrl = (id, fmt) => `${BASE}/api/media/${id}/export/${fmt}`
// Batch timeline export: lay several clips end-to-end on one timeline.
// ids = array of media ids (order preserved). fmt ∈ edl|srt|fcpxml.
export const exportTimelineUrl = (ids, fmt) =>
  `${BASE}/api/export/timeline/${fmt}?ids=${ids.join(',')}`

// Authenticated file download. A plain <a href> can't carry the Bearer token,
// so when a token is set (non-loopback backend without a proxy that injects it)
// the export endpoints would 401 (Codex review P2). Fetch with auth → blob →
// save. Works in every mode: loopback (token-free), proxy (proxy injects), and
// direct backend + setToken (this path adds the header).
export async function downloadFile(path, filename) {
  const headers = {}
  if (_token) headers['Authorization'] = `Bearer ${_token}`
  const res = await fetch(`${BASE}${path}`, { headers })
  if (!res.ok) {
    let detail = null
    try { detail = await res.json() } catch { /* non-json */ }
    throw new ApiError(res.status, path, detail)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Defer revoke: in WKWebView (Tauri) the download starts asynchronously, so
  // revoking immediately can invalidate the blob before WebKit reads it →
  // empty/failed saves (Codex review P2). A short delay lets the click settle.
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}
// Path-only builders (no BASE) for downloadFile, which prepends BASE itself.
export const exportPath = (id, fmt) => `/api/media/${id}/export/${fmt}`
export const exportTimelinePath = (ids, fmt) =>
  `/api/export/timeline/${fmt}?ids=${ids.join(',')}`

// ---- chapters / remotion / reveal-in-finder / cache / analytics ----
// /api/media/{id}/chapters?format=youtube|ffmetadata → {chapters: text, count}
export const getChapters = (id, format = 'youtube', opts) =>
  req(`/api/media/${id}/chapters?format=${format}`, opts)
// /api/media/{id}/remotion-props → word-level caption props (JSON)
export const getRemotionProps = (id, opts) => req(`/api/media/${id}/remotion-props`, opts)
// POST /api/open-file {path, reveal} → reveal in Finder/Explorer (reveal=true) or open
export const openFile = (path, reveal = true, opts) =>
  req('/api/open-file', { method: 'POST', body: { path, reveal }, ...opts })
// /api/cache/info → {caches:{...}}; POST /api/cache/clear?target=app|thumbnails|chromadb|waveforms|all
export const cacheInfo = (opts) => req('/api/cache/info', opts)
export const clearCache = (target = 'app', opts) =>
  req(`/api/cache/clear?target=${target}`, { method: 'POST', ...opts })
// analytics breakdowns
export const durationByLang = (opts) => req('/api/duration-by-lang', opts)
export const sizeByExt = (opts) => req('/api/size-by-ext', opts)

// Save an in-memory string as a downloaded file (chapters text / remotion json).
export function downloadText(text, filename, mime = 'text/plain') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}
// DaVinci Resolve metadata CSV (File → Import Metadata from CSV). ids optional
// (CSV of media ids) → batch-scoped; omitted/empty → whole library.
export const metadataCsvPath = (ids = null) =>
  `/api/export/metadata-csv${ids && ids.length ? `?ids=${ids.join(',')}` : ''}`

// ---- writes ----
// note: backend PATCH writes BOTH rating + rating_note, so an omitted note
// clears any existing note. Always pass the current note through to preserve it.
export const setRating = (id, rating, note = null, opts) =>
  req(`/api/media/${id}/rating`, { method: 'PATCH', body: { rating, note }, ...opts })

// ---- tag editing ----
// POST /api/media/{id}/tags {name, source:'manual'} → {ok, tags:[{id,name,source}]}
// Backend forces source='manual' for user-added tags (only vision mints 'auto').
// Requires videos_write (token-free on loopback). Returns the full updated tag list.
export const addTag = (id, name, opts) =>
  req(`/api/media/${id}/tags`, { method: 'POST', body: { name }, ...opts })
// DELETE /api/media/{id}/tags/{name} → {ok, tags:[...]}. Removes by name (any
// source). encodeURIComponent so spaces / CJK tag names survive the path.
export const removeTag = (id, name, opts) =>
  req(`/api/media/${id}/tags/${encodeURIComponent(name)}`, { method: 'DELETE', ...opts })

// ---- per-clip re-processing (all SYNCHRONOUS — the request blocks until done,
// so callers must show a busy state; reingest can run up to ~10 min) ----
// POST /api/media/{id}/retranscribe {language} → {ok, transcript_length, language}
export const retranscribe = (id, language = 'zh', opts) =>
  req(`/api/media/${id}/retranscribe`, { method: 'POST', body: { language }, ...opts })
// POST /api/media/{id}/retry-vision → {ok, patched, still_empty, total_frames, message?}
export const retryVision = (id, opts) =>
  req(`/api/media/${id}/retry-vision`, { method: 'POST', ...opts })
// POST /api/media/{id}/reingest → {ok, stdout, stderr}. 409 if an ingest is
// already running (single-flight guard); 504 on the 10-min server timeout.
export const reingest = (id, opts) =>
  req(`/api/media/${id}/reingest`, { method: 'POST', ...opts })

// ---- editing proxies ----
// GET /api/proxy/status → {total, proxied, size_mb}
export const getProxyStatus = (opts) => req('/api/proxy/status', opts)
// POST /api/proxy/build → {message, queued}. Queues proxy generation for every
// HEVC/ProRes without one; runs in the BACKGROUND (returns immediately, no
// completion signal — re-poll getProxyStatus). Requires ingest_write.
export const buildProxies = (opts) => req('/api/proxy/build', { method: 'POST', ...opts })
// POST /api/proxy/build/{id} → {message, queued, media_id, filename?}. Builds
// just one clip's proxy in the background.
export const buildProxyOne = (id, opts) =>
  req(`/api/proxy/build/${id}`, { method: 'POST', ...opts })

export { ApiError }
