// Search → player handoff: a search hit can carry where in the clip it matched
// (`match_start_s` from /api/media), the result row links to
// #/main-live?sel=<id>&t=<sec>, and the Inspector seeks there once the player
// has metadata. Kept out of the .svelte files so the decisions are testable.

// Seconds from a `t` hash param, or null. Rejects anything that would make the
// player seek somewhere arbitrary (negative, NaN, Infinity, junk).
export function parseStartParam(raw) {
  if (raw == null || raw === '') return null
  const t = Number(raw)
  return Number.isFinite(t) && t >= 0 ? t : null
}

// Result-row href; the time is only appended when there is one.
export function resultHref(id, matchStart) {
  const base = `#/main-live?sel=${id}`
  return typeof matchStart === 'number' && Number.isFinite(matchStart) && matchStart >= 0
    ? `${base}&t=${matchStart}`
    : base
}

// MM:SS, or H:MM:SS past an hour (a 90-minute interview is ordinary footage).
export function fmtMatchTime(sec) {
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60
  const p = (n) => String(n).padStart(2, '0')
  return h ? `${h}:${p(m)}:${p(ss)}` : `${p(m)}:${p(ss)}`
}

// Which seek (if any) to apply for the clip now in the player. `startAt` is
// {id, t}; `applied` is the key of the last seek done, so a seek happens once per
// deep-link — not again on every metadata reload, and never on a different clip.
export function pendingSeek(startAt, mediaId, applied) {
  if (!startAt || mediaId == null || startAt.id !== mediaId) return null
  const key = `${startAt.id}@${startAt.t}`
  return key === applied ? null : { t: startAt.t, key }
}
