// Pins two layout seams that a screenshot caught and a unit test can't see:
// the selected card's frame and corner ticks, and the top bar's column rule.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const card = readFileSync(new URL('../src/lib/MediaCard.svelte', import.meta.url), 'utf8')
const topbar = readFileSync(new URL('../src/lib/TopBar.svelte', import.meta.url), 'utf8')

test('selected card frame is an overlay on the gutter, not an inset shadow', () => {
  // An inset shadow is painted under .thumbarea, so the frame vanished over the
  // thumbnail and doubled up with the 1px grid gutter beside the meta row.
  assert.doesNotMatch(card, /\.card\.selected\s*\{[^}]*box-shadow/)
  assert.match(card, /\.card\.selected::after\s*\{[^}]*inset:\s*-1px[^}]*border:\s*1px solid var\(--invert\)/)
})

test('corner ticks hang off the card, outside the clipped thumbnail', () => {
  const thumbarea = card.match(/<div class="thumbarea">([\s\S]*?)\n  <\/div>/)
  assert.ok(thumbarea, 'thumbarea block not found')
  assert.doesNotMatch(thumbarea[1], /CornerTick/)
  assert.match(card, /<\/div>\n  \{#if selected\}\n    <CornerTick/)
})

test('top bar carries no padding of its own, so its column rule meets the inspector edge', () => {
  const rule = topbar.match(/\.topbar\s*\{([^}]*)\}/)
  assert.ok(rule, '.topbar rule not found')
  assert.match(rule[1], /grid-template-columns:\s*220px 1fr var\(--inspector-w\)/)
  assert.doesNotMatch(rule[1].replace(/\/\*[\s\S]*?\*\//g, ''), /padding(-[a-z]+)?\s*:/)
})
