"""Phase 11.2 — folder watcher core (stability/debounce, dedup, unmount)."""
import importlib

import watch


# ---- StabilityTracker (pure, clock-injected) ----

def test_not_ready_before_debounce():
    t = watch.StabilityTracker(debounce_s=5.0)
    assert t.observe("a.mp4", (100, 1000.0), now=0.0) is False   # first sight
    assert t.observe("a.mp4", (100, 1000.0), now=4.0) is False   # < 5s stable


def test_ready_after_stable_for_debounce():
    t = watch.StabilityTracker(debounce_s=5.0)
    t.observe("a.mp4", (100, 1000.0), now=0.0)
    assert t.observe("a.mp4", (100, 1000.0), now=5.0) is True


def test_changing_signature_resets_timer():
    t = watch.StabilityTracker(debounce_s=5.0)
    t.observe("a.mp4", (100, 1000.0), now=0.0)
    # still being written → size grew at t=4: timer restarts
    assert t.observe("a.mp4", (200, 1001.0), now=4.0) is False
    assert t.observe("a.mp4", (200, 1001.0), now=8.0) is False   # only 4s since change
    assert t.observe("a.mp4", (200, 1001.0), now=9.0) is True


def test_vanished_file_forgotten():
    t = watch.StabilityTracker(debounce_s=5.0)
    t.observe("a.mp4", (100, 1000.0), now=0.0)
    assert t.observe("a.mp4", None, now=6.0) is False  # gone → not ready
    assert "a.mp4" not in t.pending()


# ---- Watcher.tick (temp dir + fake clock + injected dispatch) ----

def _make_file(p, size=1000):
    p.write_bytes(b"x" * size)


def test_tick_dispatches_only_stable_files(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    f = root / "clip.mp4"
    _make_file(f)
    dispatched_calls = []
    w = watch.Watcher([root], debounce_s=5.0, dispatch=lambda p: dispatched_calls.append(p) or True)

    # first tick at t=0: file just seen → not yet stable
    assert w.tick(now=0.0) == []
    # second tick at t=5 with unchanged signature → dispatched
    out = w.tick(now=5.0)
    assert out == [f]
    assert dispatched_calls == [f]


def test_tick_skips_known_files(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    f = root / "clip.mp4"
    _make_file(f)
    w = watch.Watcher([root], debounce_s=0.0, dispatch=lambda p: True)
    w.known.add(str(f))   # already in DB
    assert w.tick(now=0.0) == []


def test_tick_dedups_after_dispatch(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    f = root / "clip.mp4"
    _make_file(f)
    calls = []
    w = watch.Watcher([root], debounce_s=0.0, dispatch=lambda p: calls.append(p) or True)
    # debounce 0 → ready on first sight
    assert w.tick(now=1.0) == [f]
    # second tick: already known → not dispatched again
    assert w.tick(now=2.0) == []
    assert len(calls) == 1


def test_failed_dispatch_not_marked_known(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    f = root / "clip.mp4"
    _make_file(f)
    w = watch.Watcher([root], debounce_s=0.0, dispatch=lambda p: False)  # ingest fails
    assert w.tick(now=1.0) == []          # dispatch ran but returned False
    assert str(f) not in w.known           # so it can be retried (after back-off)


def test_failing_file_backs_off_then_quarantines(tmp_path):
    """fable-audit round-5 #5: a permanently-failing file must NOT be re-dispatched
    every tick. It backs off exponentially and, after QUARANTINE_AFTER failures,
    stops retrying — until its signature changes (re-copied / fixed)."""
    root = tmp_path / "inbox"
    root.mkdir()
    f = root / "corrupt.mp4"
    _make_file(f, size=1000)
    calls = []
    w = watch.Watcher([root], debounce_s=0.0, dispatch=lambda p: calls.append(p) or False)
    assert w.QUARANTINE_AFTER == 3 and w.BASE_BACKOFF_S == 30.0  # pin the constants this test assumes

    w.tick(now=0.0)                 # fail #1 → back off until t=30
    assert len(calls) == 1
    w.tick(now=10.0)                # inside back-off → NOT retried
    assert len(calls) == 1
    w.tick(now=30.0)               # fail #2 → back off until t=90
    assert len(calls) == 2
    w.tick(now=90.0)               # fail #3 → now quarantined
    assert len(calls) == 3
    w.tick(now=100_000.0)          # quarantined: never retried, however long we wait
    assert len(calls) == 3

    # the file is re-copied (new signature) → quarantine clears, dispatched again
    _make_file(f, size=2000)
    w.tick(now=100_001.0)
    assert len(calls) == 4


def test_non_media_ignored(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    _make_file(root / "notes.txt")
    _make_file(root / "clip.mp4")
    seen = []
    w = watch.Watcher([root], debounce_s=0.0, dispatch=lambda p: seen.append(p.name) or True)
    w.tick(now=1.0)
    assert seen == ["clip.mp4"]


def test_roots_alive_detects_unmount(tmp_path):
    root = tmp_path / "card"
    root.mkdir()
    w = watch.Watcher([root])
    assert w.roots_alive() == [root]
    root.rmdir()  # "unmount"
    assert w.roots_alive() == []


def test_event_candidates_drained_by_tick(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    f = root / "clip.mp4"
    _make_file(f)
    calls = []
    w = watch.Watcher([root], debounce_s=0.0, dispatch=lambda p: calls.append(p) or True)
    w.note_event(f)               # simulate a watchdog on_created event
    w.note_event(root / "x.txt")  # non-media ignored at note time
    out = w.tick(now=1.0, candidates=[f])
    assert out == [f] and calls == [f]


# ---- #393: ingest exit 7 (reconcile mismatch) is logged and the watcher goes on ----

def _fake_result(rc, stdout="", stderr=""):
    import subprocess
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def test_ingest_file_reports_exit_7_with_the_reconcile_line(tmp_path, monkeypatch, capsys):
    out = ("Done. OK=1  skip=0  fail=0\n"
           "Reconcile: scanned=1  ingested=1  already-indexed=0  moved=0  failed=0"
           "  →  ⚠ 1 file(s) need attention\n"
           "  ⚠ counted as in the library but no DB row (1):\n"
           "    clip.mp4\n"
           "[exit 7] reconcile mismatch: 1 file(s) not accounted for\n")
    monkeypatch.setattr(watch, "run_tree", lambda cmd, timeout: _fake_result(7, stdout=out))

    ok = watch.ingest_file(tmp_path / "clip.mp4")

    printed = capsys.readouterr().out
    assert ok is False
    assert "exit 7" in printed and "reconcile mismatch" in printed
    assert "no DB row" in printed, "the reason must reach the watcher log, not just '✗ name:'"


def test_ingest_file_still_prefers_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watch, "run_tree",
                        lambda cmd, timeout: _fake_result(1, stdout="Reconcile: x", stderr="boom"))
    assert watch.ingest_file(tmp_path / "clip.mp4") is False
    printed = capsys.readouterr().out
    assert "exit 1" in printed and "boom" in printed


def test_watcher_keeps_going_after_an_exit_7(tmp_path, monkeypatch):
    """The real dispatch (ingest_file) against a fake ingest that answers 7 for
    one file and 0 for the other: the watcher must not stop, must not mark the
    mismatched file known, and must still ingest the other file."""
    root = tmp_path / "inbox"
    root.mkdir()
    lost, good = root / "lost.mp4", root / "good.mp4"
    _make_file(lost)
    _make_file(good)

    # Behaves like the real ingest in both shapes: `--dir <one>` and the B8
    # batch `--files a b`. Any run that includes lost.mp4 answers 7, and only
    # good.mp4 ever reaches the library.
    def fake_run(cmd, timeout):
        return _fake_result(7 if any(c.endswith("lost.mp4") for c in cmd) else 0)
    monkeypatch.setattr(watch, "run_tree", fake_run)
    monkeypatch.setattr(watch, "_path_in_library", lambda p: p.name == "good.mp4")

    w = watch.Watcher([root], debounce_s=0.0)       # default dispatch = ingest_file
    done = w.tick(now=1.0)

    assert done == [good]
    assert str(good) in w.known and str(lost) not in w.known
    assert str(lost) in w._failures                  # backed off, will be retried


# ---- B8: files ready together share one ingest run ----

def _batch_watcher(root, monkeypatch, rc_for=None, in_lib=None, **kw):
    """Watcher on the REAL dispatch path with run_tree faked. Records each ingest
    command; `rc_for(cmd)` picks the exit code."""
    calls = []

    def fake_run(cmd, timeout):
        calls.append((list(cmd), timeout))
        return _fake_result(rc_for(cmd) if rc_for else 0)
    monkeypatch.setattr(watch, "run_tree", fake_run)
    if in_lib is not None:
        monkeypatch.setattr(watch, "_path_in_library", in_lib)
    w = watch.Watcher([root], debounce_s=0.0, **kw)
    return w, calls


def test_ready_files_go_to_one_ingest_run(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    a, b = root / "a.mp4", root / "b.mp4"
    _make_file(a)
    _make_file(b)
    w, calls = _batch_watcher(root, monkeypatch)
    done = w.tick(now=1.0)
    assert sorted(done) == [a, b]
    assert len(calls) == 1                       # one warm-up, not two
    cmd, timeout = calls[0]
    assert "--files" in cmd and str(a) in cmd and str(b) in cmd
    assert timeout == 600.0 * 2                  # per-file budget, summed
    assert {str(a), str(b)} <= w.known


def test_lone_file_still_uses_single_file_run(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    a = root / "a.mp4"
    _make_file(a)
    w, calls = _batch_watcher(root, monkeypatch)
    assert w.tick(now=1.0) == [a]
    assert len(calls) == 1 and calls[0][0][-2:] == ["--dir", str(a)]


def test_failed_batch_does_not_charge_healthy_batchmates(tmp_path, monkeypatch):
    """One poison file kills the batch run (exit 2, nothing written after it).
    Files already in the library count as done; the rest are retried ALONE, so
    only the poison file ends up in back-off."""
    root = tmp_path / "inbox"
    root.mkdir()
    first, bad, late = root / "a1.mp4", root / "b_bad.mp4", root / "c_late.mp4"
    for f in (first, bad, late):
        _make_file(f)
    w, calls = _batch_watcher(
        root, monkeypatch,
        rc_for=lambda cmd: 2 if any(c.endswith("b_bad.mp4") for c in cmd) else 0,
        in_lib=lambda p: p.name == "a1.mp4")     # batch got a1 in before dying
    done = w.tick(now=1.0)
    assert sorted(done) == [first, late]
    assert str(bad) in w._failures and str(late) not in w._failures
    # batch run + one solo retry each for the two that were not in the library
    assert calls[0][0][2] == "--files"
    solo = sorted(c[0][-1] for c in calls[1:])
    assert solo == sorted([str(bad), str(late)])


def test_batch_timeout_falls_back_to_single_runs(tmp_path, monkeypatch):
    import subprocess
    root = tmp_path / "inbox"
    root.mkdir()
    a, b = root / "a.mp4", root / "b.mp4"
    _make_file(a)
    _make_file(b)
    calls = []

    def fake_run(cmd, timeout):
        calls.append(list(cmd))
        if "--files" in cmd:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return _fake_result(0)
    monkeypatch.setattr(watch, "run_tree", fake_run)
    monkeypatch.setattr(watch, "_path_in_library", lambda p: False)
    w = watch.Watcher([root], debounce_s=0.0)
    assert sorted(w.tick(now=1.0)) == [a, b]
    assert len(calls) == 3 and not w._failures


def test_batch_respects_max_batch(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    for n in ("a", "b", "c"):
        _make_file(root / (n + ".mp4"))
    w, calls = _batch_watcher(root, monkeypatch, max_batch=2)
    assert len(w.tick(now=1.0)) == 3
    assert len(calls) == 2


def test_ready_file_held_while_another_is_still_copying(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    a, b = root / "a.mp4", root / "b.mp4"
    _make_file(a)
    _make_file(b, size=10)
    calls = []
    monkeypatch.setattr(watch, "run_tree",
                        lambda cmd, timeout: calls.append(list(cmd)) or _fake_result(0))
    w = watch.Watcher([root], debounce_s=5.0, batch_hold_s=30.0)
    w.tick(now=0.0)
    _make_file(b, size=20)                       # b still growing
    assert w.tick(now=6.0) == [] and calls == []  # a ready, held for b
    assert sorted(w.tick(now=12.0)) == [a, b]
    assert len(calls) == 1 and "--files" in calls[0]   # b stable at 11+ → one batch


def test_hold_expires_even_if_another_file_keeps_growing(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    a, b = root / "a.mp4", root / "b.mp4"
    _make_file(a)
    calls = []
    monkeypatch.setattr(watch, "run_tree",
                        lambda cmd, timeout: calls.append(list(cmd)) or _fake_result(0))
    w = watch.Watcher([root], debounce_s=5.0, batch_hold_s=30.0)
    w.tick(now=0.0)
    size = 10
    for t in (5.0, 15.0, 25.0, 35.0):
        size += 10
        _make_file(b, size=size)                 # b never settles
        out = w.tick(now=t)
    assert out == [a]                            # released at 35 (held since 5)
    assert calls == [calls[0]] and calls[0][-2:] == ["--dir", str(a)]


def test_vanished_file_does_not_hold_the_batch(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    a, gone = root / "a.mp4", root / "gone.mp4"
    _make_file(a)
    _make_file(gone)
    calls = []
    monkeypatch.setattr(watch, "run_tree",
                        lambda cmd, timeout: calls.append(list(cmd)) or _fake_result(0))
    w = watch.Watcher([root], debounce_s=5.0, batch_hold_s=30.0)
    w.tick(now=0.0)
    gone.unlink()                                # tracked, then deleted
    assert w.tick(now=5.0) == [a]
