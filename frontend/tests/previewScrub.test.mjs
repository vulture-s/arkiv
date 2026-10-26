import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// The LUT canvas sits over the <video> and hides its native control bar, scrubber
// included — so for any clip with a colour profile the waveform was the only way to
// move the playhead. Inspector is a .svelte file, so this reads the source.
const src = readFileSync(new URL('../src/lib/Inspector.svelte', import.meta.url), 'utf8')

test('the LUT canvas block carries its own draggable scrub bar', () => {
  const block = src.match(/\{#if lutShown\}([\s\S]*?)\{\/if\}/)
  assert.ok(block, 'lutShown block not found')
  assert.match(block[1], /class="lutcanvas"/)
  assert.match(block[1], /class="scrubtrack"/, 'canvas covers the native scrubber but ships no replacement')
  for (const ev of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel']) {
    assert.match(block[1], new RegExp(`on:${ev}=`), `scrub track does not handle ${ev}`)
  }
})

test('scrubbing seeks without starting playback', () => {
  // seekTo() plays — right for a transcript click, wrong for a drag: every pointer
  // move would kick playback and the frame under the pointer would run away.
  const fn = (name) => {
    const m = src.match(new RegExp(`function ${name}\\(([\\s\\S]*?)\\n  \\}`))
    assert.ok(m, `${name} not found`)
    return m[1]
  }
  for (const name of ['scrubAt', 'onScrubDown', 'onScrubMove']) {
    assert.doesNotMatch(fn(name), /seekTo\(|\.play\(/, `${name} starts playback`)
  }
  assert.match(fn('scrubAt'), /playerEl\.currentTime =/)
})
