// Why a <video> failed — asked of the endpoint instead of assumed.
//
// `<video on:error>` reports that loading failed and nothing else: there is no
// status, no body, no distinction between "this codec has no decoder" and "your
// token expired". #428 added a 409 {need_proxy} for unplayable codecs, and the
// Inspector rendered its "build a proxy" panel on the bare error event — so a
// 401, a 404, or a backend that is simply down all arrived on screen as
//
//     此編碼瀏覽器播不了  ·  [建立 proxy 後可播放]
//
// which sends the user to transcode a file they cannot read, to fix a problem a
// proxy cannot fix. The misdirection is the cost, not the wrong wording.
//
// The status is knowable: `streamUrl()` carries its token in the query string
// (a <video src> cannot send headers), so the same URL can be fetched from here
// with the same credentials the player just used.
//
// 🔴 `Range: bytes=0-0` is load-bearing, not tidiness. `/api/stream` answers with
// Starlette's FileResponse, which honours Range — measured: 206, one byte,
// `Content-Range: bytes 0-0/4096`. Without the header this diagnostic downloads
// the whole original, and on a 4K source that is worse than the bug. A backend
// test pins the range behaviour so swapping the response class cannot silently
// turn this into a full download.

export const CODEC = 'codec'            // the file is there and readable; the browser cannot decode it
export const FORBIDDEN = 'forbidden'    // 401/403 — auth, not encoding
export const MISSING = 'missing'        // 404 — the row or the file is gone
export const SERVER = 'server'          // 5xx and anything else the server said
export const UNREACHABLE = 'unreachable' // the request never got an answer

/**
 * Ask `/api/stream` what actually went wrong.
 *
 * @param {string} url      the exact src the player used (token already in it)
 * @param {Function} doFetch injectable for tests
 * @returns {Promise<{kind: string, status: number, reason: string|null, canProxy: boolean}>}
 */
export async function diagnosePlaybackFailure(url, doFetch = fetch) {
  let res
  try {
    res = await doFetch(url, { headers: { Range: 'bytes=0-0' }, cache: 'no-store' })
  } catch {
    // Network-level: the backend stopped, the NAS went away, the laptop slept.
    return { kind: UNREACHABLE, status: 0, reason: null, canProxy: false }
  }

  const status = res.status

  if (status === 409) {
    // The gate refused on purpose and said why. Show ITS words, not ours — the
    // reason names the codec, and "dnxhd" is more use than "此編碼".
    let body = null
    try { body = await res.json() } catch { /* a 409 without a body is still a 409 */ }
    return {
      kind: CODEC,
      status,
      reason: (body && body.reason) || null,
      // `need_proxy` is the endpoint's own claim. Honour it rather than assuming
      // every 409 is proxyable: a future refusal that a proxy cannot fix would
      // otherwise inherit the button.
      canProxy: !!(body && body.need_proxy),
    }
  }

  if (status === 401 || status === 403) {
    return { kind: FORBIDDEN, status, reason: null, canProxy: false }
  }

  if (status === 404) {
    return { kind: MISSING, status, reason: null, canProxy: false }
  }

  if (res.ok) {
    // The server handed over bytes and the player still could not play them, so
    // this really is a decode problem — the gate let something through. This is
    // the one branch where the original message was right all along, and it has
    // to stay: it is the case the panel was built for.
    return { kind: CODEC, status, reason: null, canProxy: true }
  }

  return { kind: SERVER, status, reason: null, canProxy: false }
}

/** One line for the panel. Keeps the 409 wording the UI already shipped. */
export function playbackFailureMessage(diag) {
  if (!diag) return '播放失敗'
  switch (diag.kind) {
    case CODEC:
      return diag.reason ? `此編碼瀏覽器播不了（${diag.reason}）` : '此編碼瀏覽器播不了'
    case FORBIDDEN:
      return '沒有權限讀取這個檔案 —— 這不是編碼問題'
    case MISSING:
      return '找不到這個檔案 —— 它可能被移走或刪掉了'
    case UNREACHABLE:
      return '連不上伺服器，所以讀不到這個檔案'
    default:
      return `伺服器回報錯誤（${diag.status}）`
  }
}
