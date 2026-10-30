// Sidebar-only fetches (tier line, …) must never hold the media pool hostage.
// /api/entitlements once hung for minutes on a library whose project DB sat on
// an external volume, and because it was awaited in the same Promise.all as
// the pool, the whole page sat on "loading…". A sidecar resolves to null on
// error OR after `ms`, aborting the request so it does not linger.
export function fetchSidecar(fn, ms = 5000) {
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), ms)
  return Promise.resolve()
    .then(() => fn({ signal: ac.signal }))
    .catch(() => null)
    .finally(() => clearTimeout(timer))
}
