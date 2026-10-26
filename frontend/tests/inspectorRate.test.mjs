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
