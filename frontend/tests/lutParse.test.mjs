import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseCube } from '../src/lib/lutGL.js'

const cube2 = `# comment
TITLE "t"
LUT_3D_SIZE 2
DOMAIN_MIN 0.0 0.0 0.0
DOMAIN_MAX 1.0 1.0 1.0
0 0 0
1 0 0
0 1 0
1 1 0
0 0 1
1 0 1
0 1 1
1 1 1
`

test('parseCube reads a 3D cube into RGBA, R fastest', () => {
  const { size, rgba } = parseCube(cube2)
  assert.equal(size, 2)
  assert.equal(rgba.length, 8 * 4)
  assert.deepEqual([...rgba.slice(4, 8)], [1, 0, 0, 1]) // index 1 = R step
  assert.deepEqual([...rgba.slice(28, 32)], [1, 1, 1, 1])
})

test('parseCube accepts CRLF line endings', () => {
  assert.equal(parseCube(cube2.replace(/\n/g, '\r\n')).size, 2)
})

test('parseCube rejects 1D, missing size, and short data', () => {
  assert.throws(() => parseCube('LUT_1D_SIZE 2\n0 0 0\n1 1 1\n'), /1D/)
  assert.throws(() => parseCube('0 0 0\n'), /LUT_3D_SIZE/)
  assert.throws(() => parseCube('LUT_3D_SIZE 2\n0 0 0\n'), /expected 8/)
  assert.throws(() => parseCube('LUT_3D_SIZE 2\n0 0 x\n'), /bad LUT row/)
})
