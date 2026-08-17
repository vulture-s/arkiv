// node --test. Same rule as viewGen.test.mjs: no case asserts that a mapper maps.
// Each one replays a way the shoot-year facet has a real path to being wrong —
// clips that fall out of reach, or a filter that quietly stops applying.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { shotYearRows, mediaParams, UNKNOWN_SHOT_YEAR, DAY_CAP } from '../src/lib/shotYear.js'

// Shaped like GET /api/media/facets/shoot-date on the real 62-clip library.
const REAL = {
  years: [
    { year: '2026', count: 1, days: [{ date: '2026-01-04', count: 1 }] },
    {
      year: '2025',
      count: 54,
      days: [{ date: '2025-10-03', count: 30 }, { date: '2025-10-02', count: 24 }],
    },
    { year: '2022', count: 1, days: [{ date: '2022-01-01', count: 1 }] },
  ],
  unknown: 6,
  total: 62,
}

test('the undated clips get a row of their own, after the years', () => {
  const rows = shotYearRows(REAL)
  assert.deepEqual(rows.map((r) => r.year), ['2026', '2025', '2022', UNKNOWN_SHOT_YEAR])
  // Drop this row and those 6 clips are unreachable from the sidebar the moment any
  // year is picked, while the visible counts silently stop summing to the library.
  assert.equal(rows.at(-1).count, 6)
  assert.equal(rows.reduce((n, r) => n + r.count, 0), REAL.total)
})

test('server order is preserved — newest first, no client-side re-sort', () => {
  // The years arrive as strings. Any comparator applied here would be a second
  // opinion about ordering that the backend already settled, and a lexical one on
  // the raw creation_date is exactly the ':' > '-' trap #291 documents.
  assert.deepEqual(shotYearRows(REAL).map((r) => r.label), ['2026', '2025', '2022', '未知日期'])
})

test('a library where nothing carries a shoot date hides the section', () => {
  // One bucket holding everything is the shape that ruled processed_at out of the
  // backend facet: it is a control that cannot narrow anything.
  assert.equal(shotYearRows({ years: [], unknown: 62, total: 62 }), null)
  assert.equal(shotYearRows(null), null)
})

test('no undated clips means no undated row', () => {
  const rows = shotYearRows({ years: [{ year: '2025', count: 12 }], unknown: 0, total: 12 })
  assert.deepEqual(rows.map((r) => r.year), ['2025'])
})

test('the day list keeps what it sends separate from what it shows', () => {
  const y2025 = shotYearRows(REAL).find((r) => r.year === '2025')
  // Trimming the year out of the label is a display choice for a 220px column. The
  // value that goes back to the server has to stay the full ISO day, or the request
  // asks for "10-03" and the grid comes back empty.
  assert.deepEqual(y2025.days.map((d) => d.label), ['10-03', '10-02'])
  assert.deepEqual(y2025.days.map((d) => d.date), ['2025-10-03', '2025-10-02'])
  assert.equal(y2025.days.reduce((n, d) => n + d.count, 0), y2025.count)
})

test('the undated bucket has no days to drill into', () => {
  const unknown = shotYearRows(REAL).find((r) => r.year === UNKNOWN_SHOT_YEAR)
  // Not an oversight — a clip with no readable date has no day, so an empty list is
  // the honest answer. The sidebar keys its caret off this, so a stray day here would
  // render an expandable row that expands to nothing.
  assert.deepEqual(unknown.days, [])
})

test('a year with more days than the cap still yields every day to the caller', () => {
  // The cap is the sidebar's business (it slices for display and offers 更多).
  // Dropping days here instead would make them unreachable no matter what the user
  // clicks — the same mistake as omitting the unknown bucket.
  const many = Array.from({ length: DAY_CAP + 7 }, (_, i) => ({
    date: `2025-03-${String(i + 1).padStart(2, '0')}`, count: 1,
  }))
  const rows = shotYearRows({ years: [{ year: '2025', count: many.length, days: many }], unknown: 0 })
  assert.equal(rows[0].days.length, DAY_CAP + 7)
})

test('the picked year reaches browse, search AND the next page', () => {
  // H8 / H14 in miniature. /api/media honours shot_year on all three of its server
  // branches; the way that protection gets thrown away on the client is a params
  // object assembled separately per call site, so that one of them forgets.
  const PAGE = 500
  const year = '2025'

  const browse = mediaParams({ limit: PAGE, shotYear: year })
  assert.equal(browse.shot_year, year)

  // The user types into the search box with the year still selected.
  const search = mediaParams({ limit: PAGE, query: '訪談', shotYear: year })
  assert.equal(search.shot_year, year, 'the year must survive the first keystroke')
  assert.equal(search.q, '訪談')

  // ...and then pages that filtered search.
  const nextPage = mediaParams({ limit: PAGE, offset: PAGE, query: '訪談', shotYear: year })
  assert.equal(nextPage.shot_year, year, 'page 2 must be the same pool as page 1')
  assert.equal(nextPage.offset, PAGE)
})

test('the undated bucket is a filter value, not a label', () => {
  // It is sent to the backend as-is and must match db.UNKNOWN_SHOT_YEAR. Pinned on
  // both sides — tests/test_shoot_date_facet.py asserts this file agrees with the
  // Python constant, because a typo here clicks through to an empty grid while each
  // half still reads correctly on its own.
  assert.equal(UNKNOWN_SHOT_YEAR, 'unknown')
  assert.equal(mediaParams({ shotYear: UNKNOWN_SHOT_YEAR }).shot_year, 'unknown')
})

test('the picked day reaches browse, search AND the next page', () => {
  const PAGE = 500
  const day = '2025-10-03'
  assert.equal(mediaParams({ limit: PAGE, shotDate: day }).shot_date, day)
  assert.equal(
    mediaParams({ limit: PAGE, query: '訪談', shotDate: day }).shot_date, day,
    'the day must survive the first keystroke',
  )
  assert.equal(
    mediaParams({ limit: PAGE, offset: PAGE, shotDate: day }).shot_date, day,
    'page 2 must be the same pool as page 1',
  )
})

test('picking a day sends the day alone, not the day and its year', () => {
  // The sidebar keeps the parent year highlighted while a day is open, so both pieces
  // of state are set at once. The backend ANDs whatever it is given — so shipping a
  // stale year next to a fresh day (say 2025 + 2026-01-04) returns nothing at all.
  const p = mediaParams({ limit: 500, shotYear: '2025', shotDate: '2025-10-03' })
  assert.equal(p.shot_date, '2025-10-03')
  assert.equal('shot_year' in p, false)
})

test('every /api/media request in MainLive is built through mediaParams with shotArgs', () => {
  // The cases above prove the BUILDER keeps the filter. They cannot prove the call
  // sites use it — an audit pointed out that they all keep passing if MainLive drops
  // `...shotArgs()` from one of its three requests, which is the exact H8/H14 shape
  // one layer up. MainLive is a .svelte file, so this reads the source instead of
  // importing it: crude, but it fails on the thing that would actually break.
  const src = readFileSync(new URL('../src/routes/MainLive.svelte', import.meta.url), 'utf8')

  const calls = [...src.matchAll(/api\.getMedia\(([^\n]*)/g)].map((m) => m[1])
  assert.ok(calls.length >= 3, `expected the browse/search/page requests, found ${calls.length}`)

  for (const call of calls) {
    // ?ids= is a deep link to an explicit set of clips, not a view of the library —
    // a shoot filter on top of it would narrow a list the user asked for by id.
    if (call.includes('ids')) continue
    // Paging re-issues the params the originating request already built, so it
    // inherits the filter rather than rebuilding it; the moreParams check below is
    // what holds that end up.
    if (call.includes('moreParams')) continue
    assert.match(call, /mediaParams\(/, `raw params bypass the builder: ${call}`)
    assert.match(call, /\.\.\.shotArgs\(\)/, `request drops the shoot filter: ${call}`)
  }

  // moreParams is the pagination request, re-issued later with a new offset — same
  // requirement, different assignment shape.
  const pageParams = [...src.matchAll(/moreParams = ([^\n]*)/g)].map((m) => m[1])
    .filter((s) => s.includes('mediaParams'))
  assert.ok(pageParams.length >= 2, 'expected the browse and search pagination params')
  for (const p of pageParams) {
    assert.match(p, /\.\.\.shotArgs\(\)/, `page 2 would leave the filter behind: ${p}`)
  }
})

test('clearing the year drops the key instead of sending an empty one', () => {
  // `shot_year=` with no value would reach the backend as a falsy string and be
  // ignored, which happens to work — but only by accident, and not on the branch
  // that compares it against UNKNOWN_SHOT_YEAR first.
  assert.equal('shot_year' in mediaParams({ limit: 500, shotYear: null }), false)
  assert.equal('shot_date' in mediaParams({ limit: 500, shotDate: null }), false)
  assert.equal('q' in mediaParams({ limit: 500, query: '   ' }), false)
})
