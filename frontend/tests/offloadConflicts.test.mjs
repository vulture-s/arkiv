// Same-name conflict on offload → the rename dialog (Hevin 2026-10-09 23:14).
//
// The run used to end with an "EXISTS" tag buried in the recent-files list and a
// red "exit 1" — easy to read past, and nothing told the DIT the card was NOT
// fully backed up. Now a `needs_rename` done event must open the dialog by
// itself, and the card must not read as done / formattable until every
// conflict is renamed-and-verified. These pin the decisions the Svelte view
// makes off the stream (node:test, no runner dependency).
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  conflictsFromDone, shouldOpenRenameDialog, cardStatus, canFormat,
  checkNewName, applyResolveResult, statusLine,
} from '../src/lib/offloadConflicts.js'

const item = (over = {}) => ({
  dst: '/Volumes/B1', rel: 'CLIP/C0001.MP4', source: '/Volumes/Untitled/CLIP/C0001.MP4',
  card: 'Untitled', suggested_name: 'C0001 (2).MP4', suggested_rel: 'CLIP/C0001 (2).MP4',
  existing: { size: 10, mtime: '2026-10-08T01:00:00+00:00', hash_prefix: 'aaaaaaaaaaaa' },
  incoming: { size: 12, mtime: '2026-10-09T01:00:00+00:00', hash_prefix: null },
  ...over,
})

const doneNeedsRename = {
  type: 'done', code: 3, status: 'needs_rename',
  summary: { '/Volumes/B1': { status: 'needs_rename', verified_files: 1, failed_files: 0, needs_rename: [item()] } },
  needs_rename: [item()],
}

test('a needs_rename done event opens the dialog — not just an EXISTS tag', () => {
  assert.equal(shouldOpenRenameDialog(doneNeedsRename), true)
  const rows = conflictsFromDone(doneNeedsRename)
  assert.equal(rows.length, 1)
  assert.equal(rows[0].name, 'C0001 (2).MP4', 'the input is prefilled with the suggestion')
  assert.equal(rows[0].state, 'pending')
})

test('older done events without the top-level list still open it (from summary)', () => {
  const ev = { ...doneNeedsRename, needs_rename: undefined, status: undefined }
  assert.equal(shouldOpenRenameDialog(ev), true)
  assert.equal(conflictsFromDone(ev).length, 1)
})

test('a clean run does not open the dialog and is the only formattable state', () => {
  const ok = { type: 'done', code: 0, status: 'done', summary: { '/Volumes/B1': { status: 'done', needs_rename: [] } } }
  assert.equal(shouldOpenRenameDialog(ok), false)
  assert.equal(cardStatus(ok), 'done')
  assert.equal(canFormat(cardStatus(ok)), true)
  for (const s of ['needs_rename', 'incomplete', 'failed', 'running', undefined]) {
    assert.equal(canFormat(s), false, `${s} must never read as formattable`)
  }
})

test('card status is never done while a conflict is pending, even with code 0', () => {
  assert.equal(cardStatus(doneNeedsRename), 'needs_rename')
  // defensive: a stale/odd code 0 with a pending conflict is still not done
  assert.equal(cardStatus({ ...doneNeedsRename, code: 0, status: undefined }), 'needs_rename')
  assert.equal(cardStatus({ type: 'done', code: 1, summary: {} }), 'failed')
})

test('the status line warns not to format until resolved', () => {
  assert.match(statusLine('needs_rename', 1), /不可格式化|請勿格式化/)
  assert.match(statusLine('incomplete', 1), /未備份/)
  assert.match(statusLine('done', 0), /完成/)
})

test('client-side name check mirrors the server (traversal, separators, reserved)', () => {
  for (const bad of ['', '   ', '../x.MP4', 'a/b.MP4', 'a\\b.MP4', '..', '.hidden', 'x.partial', ' lead.MP4']) {
    assert.notEqual(checkNewName(bad), '', `${JSON.stringify(bad)} should be rejected`)
  }
  assert.equal(checkNewName('C0001_cardB.MP4'), '')
  assert.equal(checkNewName('C0001 (2).MP4'), '')
})

test('resolve results drive the rows and the card status', () => {
  const rows = conflictsFromDone(doneNeedsRename)
  const second = item({ rel: 'CLIP/C0003.MP4', suggested_name: 'C0003 (2).MP4' })
  rows.push({ ...second, name: second.suggested_name, state: 'pending' })

  const partial = applyResolveResult(rows, rows[0], {
    status: 'needs_rename', file: { status: 'verified', dst_rel: 'CLIP/C0001 (2).MP4' }, summary: {},
  })
  assert.equal(partial.rows[0].state, 'renamed')
  assert.equal(partial.rows[1].state, 'pending')
  assert.equal(partial.status, 'needs_rename')

  const skipped = applyResolveResult(partial.rows, partial.rows[1], {
    status: 'incomplete', file: { status: 'skipped_unbacked' }, summary: {},
  })
  assert.equal(skipped.rows[1].state, 'skipped')
  assert.equal(skipped.status, 'incomplete')
  assert.equal(canFormat(skipped.status), false)
})
