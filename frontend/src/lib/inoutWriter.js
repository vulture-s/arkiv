// Debounced writer for the Inspector's IN/OUT marks.
//
// Waveform drags fire many updates per second, so writes are coalesced. The bug
// this exists to prevent (audit N1): the old inline `setTimeout(() =>
// onInOut(inSec, outSec), 350)` read the callback AND the values when the timer
// FIRED. The Inspector is one instance reused across clips, so marking clip A and
// switching to B inside 350 ms wrote B's freshly reset (null) marks to B — erasing
// B's stored range — and A's mark was never saved.
//
// Here the clip id and the values are bound when the write is SCHEDULED, and the
// caller flushes on clip switch so a pending write lands on the clip it was made
// on. The callback receives (inS, outS, id).
export function createInOutWriter(delay = 350, setTimer = setTimeout, clearTimer = clearTimeout) {
  let pending = null
  let timer = null
  function fire() {
    timer = null
    const p = pending
    pending = null
    if (p && typeof p.fn === 'function') p.fn(p.inS, p.outS, p.id)
  }
  return {
    schedule(fn, id, inS, outS) {
      if (timer != null) clearTimer(timer)
      pending = { fn, id, inS, outS }
      timer = setTimer(fire, delay)
    },
    flush() {
      if (timer != null) clearTimer(timer)
      fire()
    },
  }
}
