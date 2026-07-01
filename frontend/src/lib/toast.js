// Ephemeral toast notifications — a tiny global store the export handlers push
// to so a download's success/failure is actually surfaced. Before this, export
// failures were assigned to an unshown `err` var (or console.error) and success
// had no feedback at all (B11). Mounted once by ToastHost in App.svelte.
import { writable } from 'svelte/store'

// [{ id, kind: 'ok' | 'error', msg }]
export const toasts = writable([])

let _seq = 0
const DEFAULT_MS = 3500

export function dismissToast(id) {
  toasts.update((list) => list.filter((t) => t.id !== id))
}

// kind: 'ok' (default) | 'error'. ms<=0 keeps it until dismissed. Errors linger
// longer since they carry something the user needs to read.
export function pushToast(msg, kind = 'ok', ms = kind === 'error' ? 6000 : DEFAULT_MS) {
  const id = ++_seq
  toasts.update((list) => [...list, { id, kind, msg }])
  if (ms > 0) setTimeout(() => dismissToast(id), ms)
  return id
}
