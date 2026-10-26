import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Rating is decided while watching, so the buttons sit right under the preview —
// not at the bottom past transcript / scenes / vision / reprocess. Inspector is a
// .svelte file, so this reads the source order of the markup.
const src = readFileSync(new URL('../src/lib/Inspector.svelte', import.meta.url), 'utf8')
const markup = src.slice(src.indexOf('<aside class="inspector"'))

test('rate buttons come right after the preview, before Metadata', () => {
  const preview = markup.indexOf('<div class="preview"')
  const rate = markup.indexOf('class="ratebtns"')
  const meta = markup.indexOf('>Metadata</Eyebrow>')
  assert.ok(preview >= 0 && rate >= 0 && meta >= 0)
  assert.ok(preview < rate && rate < meta, `order preview=${preview} rate=${rate} metadata=${meta}`)
  assert.equal(markup.split('class="ratebtns"').length - 1, 1, 'rate buttons rendered twice')
})

test('export / reveal / delete follow Rate under the preview, not the panel bottom', () => {
  const rate = markup.indexOf('class="ratebtns"')
  const meta = markup.indexOf('>Metadata</Eyebrow>')
  for (const marker of ['<div class="exports">', 'class="ak-btn reveal"', 'class="ak-btn del"']) {
    const at = markup.indexOf(marker)
    assert.ok(at > rate && at < meta, `${marker} is not between Rate and Metadata`)
  }
})

test('export row wraps instead of running off the inspector edge', () => {
  // 字幕 MARKER overflowed the 340px column by ~87px with no way to reach it.
  assert.match(src, /\.exports \{[^}]*flex-wrap: wrap/)
})

test('every 3-column shell reads the inspector width from one token', () => {
  // TopBar's search rule must line up with the body column; three literals drift.
  const css = readFileSync(new URL('../src/app.css', import.meta.url), 'utf8')
  assert.match(css, /--inspector-w:\s*\d+px/)
  for (const f of ['../src/lib/MainShell.svelte', '../src/routes/MainLive.svelte', '../src/lib/TopBar.svelte']) {
    const s = readFileSync(new URL(f, import.meta.url), 'utf8')
    assert.match(s, /grid-template-columns: 220px 1fr var\(--inspector-w\)/, f)
  }
})
