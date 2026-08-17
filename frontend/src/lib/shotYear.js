// Browse footage by when it was SHOT — the client half of #291's facet.
//
// Lives outside the component so the things that actually break are testable: the
// `unknown` bucket surviving into the sidebar, the day list keeping the value it
// filters on separate from the value it displays, and the selection reaching EVERY
// /api/media call rather than only the browse one.

// Must match db.UNKNOWN_SHOT_YEAR. Named on both sides for the same reason: the
// facet's bucket key and the filter's value are one string travelling in opposite
// directions, and a typo in either makes the bucket click through to an empty grid
// while both halves still look correct in isolation.
export const UNKNOWN_SHOT_YEAR = 'unknown'
export const UNKNOWN_SHOT_YEAR_LABEL = '未知日期'

// A year can hold hundreds of shoot days. Uncapped, expanding one pushes Storage off
// the bottom of the sidebar — the same problem the tag cloud and the collection list
// already solved, so reuse their answer rather than invent a third one.
export const DAY_CAP = 20

/**
 * /api/media/facets/shoot-date payload → sidebar rows, or null to hide the section.
 *
 * @param {{years?: {year: string|number, count: number, days?: {date: string, count: number}[]}[], unknown?: number}} facets
 * @returns {{year: string, count: number, label: string, days: {date: string, count: number, label: string}[]}[] | null}
 */
export function shotYearRows(facets) {
  const years = (facets && facets.years) || []
  // No dated clips at all → one bucket holding the entire library, which is the
  // same "single bucket answers nothing" shape that ruled processed_at out of the
  // backend facet. Hide the section rather than ship a row nobody can use.
  if (!years.length) return null
  const rows = years.map((y) => ({
    year: String(y.year),
    count: y.count,
    label: String(y.year),
    days: (y.days || []).map((d) => ({
      date: d.date,
      count: d.count,
      // Nested under its year, the year digits are noise in a 220px column — but
      // only the LABEL loses them. `date` stays the full ISO day, because that is
      // what goes back to the server.
      label: String(d.date).slice(5),
    })),
  }))
  const unknown = (facets && facets.unknown) || 0
  // Appended, never sorted in among the years: it is not a point on the timeline.
  // Omitting it is worse than untidy — those clips become unreachable from the
  // sidebar the moment any year is picked, and the counts stop reconciling with
  // the library total, which reads as "there is nothing there".
  if (unknown > 0) {
    rows.push({
      year: UNKNOWN_SHOT_YEAR,
      count: unknown,
      label: UNKNOWN_SHOT_YEAR_LABEL,
      days: [], // undated clips have no day to drill into, by definition
    })
  }
  return rows
}

/**
 * The /api/media query for the current view.
 *
 * One builder for all three callers (browse, search, next page) on purpose.
 * /api/media honours the shoot filters on each of its three server-side branches —
 * SQL list, semantic search, degraded text search — specifically so they cannot fall
 * off when the user types or when Ollama is down (audits H8 / H14). Assembling the
 * params per call site puts that same failure back one layer up: the year would hold
 * while browsing and vanish on the first keystroke.
 *
 * @param {{limit?: number, offset?: number, query?: string, shotYear?: string|null, shotDate?: string|null}} opts
 */
export function mediaParams({
  limit = null, offset = null, query = '', shotYear = null, shotDate = null,
} = {}) {
  const p = {}
  const q = (query || '').trim()
  if (q) p.q = q
  if (shotDate) {
    // A day already implies its year, and the sidebar keeps the parent year
    // highlighted while a day is picked. Sending both would be redundant at best
    // and self-contradictory at worst — the backend ANDs them, so a stale year
    // alongside a fresh day would return nothing at all.
    p.shot_date = shotDate
  } else if (shotYear) {
    p.shot_year = shotYear
  }
  if (limit != null) p.limit = limit
  if (offset != null) p.offset = offset
  return p
}
