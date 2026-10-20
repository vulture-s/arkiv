// node --test. The IN/OUT marks are written through a 350 ms debounce. Before
// audit N1 the pending write read `onInOut`, `inSec` and `outSec` when the timer
// FIRED — so marking A and switching to B inside 350 ms wrote B's (reset, null)
// marks onto B, erasing B's stored range, and dropped A's mark. These replay that
// interleaving with a hand-driven clock.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createInOutWriter } from '../src/lib/inoutWriter.js'

function clock() {
  let next = 1
  const timers = new Map()
  return {
    set: (fn) => { const id = next++; timers.set(id, fn); return id },
    clear: (id) => { timers.delete(id) },
    runAll: () => { const fns = [...timers.values()]; timers.clear(); fns.forEach((f) => f()) },
    pending: () => timers.size,
  }
}

test('switching clip inside the debounce writes the OLD clip its own marks, never the new one', () => {
  const c = clock()
  const writes = []
  const save = (inS, outS, id) => writes.push({ id, inS, outS })
  const w = createInOutWriter(350, c.set, c.clear)
  w.schedule(save, 'A', 1.5, 9.25) // mark on A
  w.flush()                         // clip switch → flush before hydrating B
  c.runAll()                        // nothing left to fire
  assert.deepEqual(writes, [{ id: 'A', inS: 1.5, outS: 9.25 }])
})

test('rapid marks on one clip coalesce into the last value', () => {
  const c = clock()
  const writes = []
  const w = createInOutWriter(350, c.set, c.clear)
  const save = (inS, outS, id) => writes.push({ id, inS, outS })
  w.schedule(save, 7, 1, null)
  w.schedule(save, 7, 1, 4)
  w.schedule(save, 7, 2, 4)
  assert.equal(c.pending(), 1)
  c.runAll()
  assert.deepEqual(writes, [{ id: 7, inS: 2, outS: 4 }])
})

test('the scheduled write keeps the id and values it was scheduled with', () => {
  const c = clock()
  const writes = []
  const w = createInOutWriter(350, c.set, c.clear)
  let current = { id: 'A', inS: 3, outS: 5 }
  w.schedule((i, o, id) => writes.push({ id, i, o }), current.id, current.inS, current.outS)
  current = { id: 'B', inS: null, outS: null } // what the component holds after the switch
  c.runAll()
  assert.deepEqual(writes, [{ id: 'A', i: 3, o: 5 }])
})

test('flush with nothing pending is a no-op; a missing callback is ignored', () => {
  const c = clock()
  const w = createInOutWriter(350, c.set, c.clear)
  w.flush()
  w.schedule(null, 1, 0, 1)
  c.runAll()
  w.flush()
  assert.equal(c.pending(), 0)
})
