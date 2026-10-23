// Library-wide person names for the Inspector's autocomplete — loaded once per page.
//
// #572: the old guard was "fetch while the list is empty". A library with no
// people yet answers [], the assignment re-ran the reactive block, the list was
// still empty, so it fetched again — 2026-10-21 measured ~390 requests/s for as
// long as a clip stayed selected, which starved the WebView (waveform drag,
// shortcuts and drag-out all stalled behind it). The loaded flag lives here, not
// in the component, so "already asked" never depends on what the answer was.
//   fetchNames: () => Promise<[{name, count}]>
//   onNames:    receives each successful answer (an empty list included)
// → { ensure, refresh }
//   ensure():  first call fetches, every later call is a no-op (call it as often as you like)
//   refresh(): fetch again — after an add/remove changed the names
export function createIdentityNamesLoader(fetchNames, onNames) {
  let loaded = false
  async function refresh() {
    loaded = true
    try {
      onNames(await fetchNames())
    } catch (e) {
      /* autocomplete is optional — a failed fetch is not retried until the next refresh */
    }
  }
  function ensure() {
    if (!loaded) return refresh()
    return Promise.resolve()
  }
  return { ensure, refresh }
}
