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

    def fake_run(cmd, timeout):
        return _fake_result(7 if cmd[-1].endswith("lost.mp4") else 0)
    monkeypatch.setattr(watch, "run_tree", fake_run)

    w = watch.Watcher([root], debounce_s=0.0)       # default dispatch = ingest_file
    done = w.tick(now=1.0)

    assert done == [good]
    assert str(good) in w.known and str(lost) not in w.known
    assert str(lost) in w._failures                  # backed off, will be retried
