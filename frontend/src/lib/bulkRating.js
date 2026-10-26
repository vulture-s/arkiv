// Batch rating for the multi-select bar. There is no bulk endpoint: each clip goes
// through the same PATCH /api/media/{id}/rating as the inspector buttons, so the
// note-preserving PATCH contract (setRating.test.mjs) holds for batches too.
//
// Settles every id — one failure must not stop the rest, and the caller needs the
// exact failed ids to revert those rows (and only those) and say how many failed.
export async function rateMany(ids, backendVal, setRating, { concurrency = 6 } = {}) {
  const ok = []
  const failed = []
  let next = 0
  async function worker() {
    while (next < ids.length) {
      const id = ids[next++]
      try {
        await setRating(id, backendVal)
        ok.push(id)
      } catch (e) {
        failed.push({ id, error: e && e.message ? e.message : String(e) })
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, ids.length) }, worker))
  return { ok, failed }
}
