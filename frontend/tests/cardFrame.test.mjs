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

test('list thumbnails start on the 22px line: no checkbox column ahead of them', () => {
  const live = readFileSync(new URL('../src/routes/MainLive.svelte', import.meta.url), 'utf8')
  const table = live.match(/<table class="medialist"[^>]*>([\s\S]*?)<\/table>/)
  assert.ok(table, 'medialist table not found')
  const firstTh = table[1].match(/<th\b[^>]*>/)
  const firstTd = table[1].match(/<td\b[^>]*>/)
  assert.match(firstTh[0], /class="c-thumb"/)
  assert.match(firstTd[0], /class="c-thumb"/)
  // the pick box rides inside the thumbnail cell instead
  assert.match(table[1], /<td class="c-thumb">\s*<input[^>]*class="lpick"/)
})

test('topbar rules sit on the same side of each boundary as the body columns below', () => {
  // The body draws the boundaries as the pool's RIGHT border and the inspector's
  // LEFT border. When .search drew them instead (its left/right borders), each
  // topbar rule landed 1px on the other side and the lines never met.
  const css = topbar.replace(/\/\*[\s\S]*?\*\//g, '')
  const block = (sel) => (css.match(new RegExp(`\\.${sel}\\s*\\{([^}]*)\\}`)) || [])[1] || ''
  assert.doesNotMatch(block('search'), /border(-left|-right)?\s*:/)
  assert.match(block('logo'), /border-right:\s*1px solid var\(--rule\)/)
  assert.match(block('actions'), /border-left:\s*1px solid var\(--rule\)/)
})
