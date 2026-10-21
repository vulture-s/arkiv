// #561 desktop library switching — pure helpers. node:test, no runner dependency.
import test from 'node:test'
import assert from 'node:assert/strict'

import { busyMessage, uniqueName, baseName, libraryRows, canSwitchLibrary } from '../src/lib/librarySwitch.js'

const stats = { total: 12 }

test('outside the desktop shell nothing can switch', () => {
  assert.equal(canSwitchLibrary(), false)
})

test('busy message names the jobs; idle → null', () => {
  assert.equal(busyMessage({ busy: false, jobs: [] }), null)
  assert.equal(busyMessage(null), null)
  assert.match(busyMessage({ busy: true, jobs: ['ingest', 'offload'] }), /^匯入、卸載/)
})

test('unique names skip taken ones', () => {
  assert.equal(uniqueName('Ray-Ban', []), 'Ray-Ban')
  assert.equal(uniqueName('Ray-Ban', ['Ray-Ban', 'Ray-Ban 2']), 'Ray-Ban 3')
})

test('base name works for both separators', () => {
  assert.equal(baseName('/Volumes/home/Ray-Ban/'), 'Ray-Ban')
  assert.equal(baseName('C:\\Users\\user\\Videos\\trips'), 'trips')
})

test('browser (no shell state): same rows as before #561', () => {
  const rows = libraryRows({ registryProjects: [{ name: 'A', path: '/a', is_current: false }], stats, projectName: 'arkiv', libState: null })
  assert.deepEqual(rows.map((r) => [r.id, r.active]), [['cur', true], ['reg:A', false]])
})

test('desktop on another library: built-in library gets a way-back row', () => {
  const rows = libraryRows({
    registryProjects: [{ name: 'Ray-Ban', path: '/v/rayban', is_current: true }],
    stats, projectName: 'x', libState: { active: '/v/rayban', default: '/data/arkiv' },
  })
  assert.deepEqual(rows.map((r) => [r.name, r.active, r.path]), [['Ray-Ban', true, '/v/rayban'], ['內建素材庫', false, '/data/arkiv']])
})

test('desktop on the built-in library: no duplicate row, open row carries its path', () => {
  const rows = libraryRows({
    registryProjects: [{ name: 'Ray-Ban', path: '/v/rayban', is_current: false }],
    stats, projectName: 'arkiv', libState: { active: '/data/arkiv/', default: '/data/arkiv' },
  })
  assert.deepEqual(rows.map((r) => [r.id, r.path]), [['cur', '/data/arkiv/'], ['reg:Ray-Ban', '/v/rayban']])
})

test('a registered built-in library is not listed twice', () => {
  const rows = libraryRows({
    registryProjects: [{ name: '內建', path: '/data/arkiv', is_current: false }, { name: 'B', path: '/b', is_current: true }],
    stats, projectName: 'x', libState: { active: '/b', default: '/data/arkiv' },
  })
  assert.equal(rows.length, 2)
})
