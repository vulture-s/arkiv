// Native folder picker, for the fields that hold a real path on disk.
//
// Reached through `window.__TAURI__` (tauri.conf.json sets `withGlobalTauri`)
// rather than an `@tauri-apps/*` npm import, for two reasons: the same SPA is
// served in a plain browser by server.py, where a desktop-only package would be
// dead weight in the bundle and would still have nothing to talk to; and the
// global is already there, so this costs no dependency at all.
// `capabilities/default.json` grants `dialog:default`, which covers open.

/** True only inside the desktop shell. Callers should hide their browse button
 *  when this is false — in a browser the field still accepts a typed path, so a
 *  button that can never open anything is worse than no button. */
export function canPickFolder() {
  return typeof window !== 'undefined' && !!window.__TAURI__?.dialog?.open
}

/**
 * Open the OS folder chooser (Explorer on Windows, Finder on macOS).
 * @param {string} [defaultPath] where to start; ignored when empty or invalid.
 * @returns {Promise<string|null>} the chosen path, or null if cancelled or if
 *   we are not in the desktop shell. Callers must treat null as "leave the
 *   field alone" — clearing a good path because someone hit Cancel is the
 *   obvious way to make this feel broken.
 */
export async function pickFolder(defaultPath) {
  if (!canPickFolder()) return null
  const picked = await window.__TAURI__.dialog.open({
    directory: true,
    multiple: false,
    ...(defaultPath ? { defaultPath } : {}),
  })
  // Cancel resolves to null. A single pick resolves to a string, but the plugin
  // returns an array under `multiple: true` — normalise both so flipping that
  // flag later cannot silently bind an array into a text field.
  return Array.isArray(picked) ? (picked[0] ?? null) : picked
}
