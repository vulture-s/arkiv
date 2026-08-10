// A monotonic generation counter for "which view is the grid supposed to be showing".
//
// MainLive has four independent writers to the same grid state (items/total/
// moreParams/selectedId/state): load(), runSearch(), loadIds() and the synchronous
// onCollectionClick(). Three of them await the network before writing, so the write
// that lands last wins — which is not the same as the view the user asked for last.
// Clicking a collection while a search is in flight left the sidebar highlighting the
// collection while the grid repainted with the search results a moment later.
//
// The fix is a token: a path that replaces the view calls begin() and carries its token
// across every await, and applies its results only while that token is still current.
// A path that EXTENDS the current view (loadMore) reads current instead of calling
// begin(), so its append is dropped if the view changed underneath it rather than
// stapling the old view's rows onto the new one.
export function createViewGen() {
  let gen = 0
  return {
    // Claim the grid for a new view. Everything in flight is now stale.
    begin() {
      return ++gen
    },
    // Read the current token WITHOUT claiming — for work that extends the current view.
    get current() {
      return gen
    },
    isCurrent(token) {
      return token === gen
    },
    // Run `fn` only if `token` still owns the grid; report whether it did. Returning a
    // boolean (rather than just skipping) lets callers keep `finally` bookkeeping —
    // resetting a spinner is correct even for a superseded request.
    apply(token, fn) {
      if (token !== gen) return false
      fn()
      return true
    },
  }
}
