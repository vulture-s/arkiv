// Single source of truth for the displayed app version. Bump here on release.
// The label had drifted to a stale v0.9.2 across a dozen files while the app
// shipped v0.10.0, because every route hardcoded its own copy — importing this
// const keeps the version from going stale per-file again.
export const VERSION = 'v0.10.0'
