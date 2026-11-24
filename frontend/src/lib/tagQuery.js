// Sidebar tag click → an exact filter, not a ranked search.
//
// The click used to put the bare tag name in the search box, so the backend
// treated it as free text: semantic + lexical search over everything, ranked —
// the grid kept listing (nearly) the whole library, which read as "the tag did
// nothing". `tag:<name>` is parsed by search_syntax.py into the same
// `tag contains` condition the Query Builder uses, so the grid shows exactly
// the clips carrying that tag.

/** The search-box query that filters to one tag. */
export function tagQuery(name) {
  const n = String(name ?? '').trim()
  if (!n) return ''
  // search_syntax: key:"two words" for values with whitespace. The grammar has
  // no escape for a literal quote, so drop any (no tag can be matched with one).
  return /\s/.test(n) || n.includes('"') ? `tag:"${n.replace(/"/g, '')}"` : `tag:${n}`
}

/** The tag a query is filtering to, when the whole query is one tag filter. */
export function activeTagOf(query) {
  const m = String(query ?? '').trim().match(/^tag:(?:"([^"]+)"|(\S+))$/i)
  return m ? (m[1] ?? m[2]) : null
}

/** Next query when a sidebar tag is clicked: select it, or clear if already on. */
export function nextTagQuery(current, name) {
  const on = activeTagOf(current)
  return on !== null && on.toLowerCase() === String(name).trim().toLowerCase() ? '' : tagQuery(name)
}
