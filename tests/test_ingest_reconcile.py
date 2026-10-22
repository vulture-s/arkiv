"""Issue #393 — an ingest batch must say whether every scanned file is accounted for.

2026-08-28: 25 new clips, `--skip-failed`, BENCHMARK SUMMARY listed 24, the DB
grew by 24, exit 0. The 25th file was fine (a lone re-run took it). The only way
to find it was `comm` between the disk listing and `media.filename`, because:

  * BENCHMARK SUMMARY lists what WAS processed, never what was scanned and not;
  * the counters are the batch's own claims, so a file that fell through all of
    them left no trace;
  * `--skip-failed` printed the first 20 names of files whose frames it skipped.

These tests pin the output that closes that: one reconcile line, the name of every
file that is not cleanly in the library, and a DB cross-check that does not trust
the counters.

Point 2 of the issue (exit non-zero on a mismatch) was approved on 2026-10-14:
a mismatch exits 7 (`ingest.EXIT_RECONCILE_MISMATCH`), chosen after checking that
no caller branches on a specific non-zero value (watch.py, /api/ingest, ws, bins,
seed scripts all test `!= 0`). Precedence 2 > 7 > 1.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def ing():
    return importlib.import_module("ingest")


def ing_exit_mismatch():
    return importlib.import_module("ingest").EXIT_RECONCILE_MISMATCH


def _known(db, *paths):
    """The set of `media.path` values the DB would hold for these files."""
    return {db.to_relative(str(p)) for p in paths}


# ── the pure report ──────────────────────────────────────────────────────────
def test_clean_batch_says_every_file_is_accounted_for(ing, tmp_path):
    db = importlib.import_module("db")
    a, b, c = (tmp_path / n for n in ("a.mp4", "b.mp4", "c.mp4"))
    outcomes = {str(a): "ingested", str(b): "ingested", str(c): "already-indexed"}

    lines = ing._reconcile_report([a, b, c], outcomes, _known(db, a, b, c))

    assert lines[0].startswith("Reconcile: scanned=3  ingested=2  already-indexed=1  moved=0  failed=0")
    assert "✓ every file accounted for" in lines[0]
    assert len(lines) == 1, "a clean run must stay one line"


def test_a_file_in_no_bucket_is_named(ing, tmp_path):
    """The literal #393 shape: scanned, never counted, never mentioned."""
    db = importlib.import_module("db")
    files = [tmp_path / "A7520260820_99{0:02d}.MP4".format(i) for i in range(3)]
    lost = files[1]
    outcomes = {str(files[0]): "ingested", str(files[2]): "ingested"}

    out = "\n".join(ing._reconcile_report(files, outcomes, _known(db, files[0], files[2])))

    assert "⚠ 1 file(s) need attention" in out
    assert "unaccounted" in out
    assert lost.name in out


def test_counted_as_ingested_but_no_db_row_is_named(ing, tmp_path):
    """The counters are claims; the DB is the fact. A file the batch says it
    ingested but that has no row must surface — the counter alone would say OK."""
    db = importlib.import_module("db")
    a, ghost = tmp_path / "a.mp4", tmp_path / "ghost.mp4"
    outcomes = {str(a): "ingested", str(ghost): "ingested"}

    out = "\n".join(ing._reconcile_report([a, ghost], outcomes, _known(db, a)))

    assert "no DB row" in out
    assert "ghost.mp4" in out
    assert "⚠ 1 file(s) need attention" in out


def test_a_skip_whose_row_vanished_is_named(ing, tmp_path):
    """SKIP is decided from a snapshot taken before the batch. If the row is gone
    by the end, "already indexed" is no longer true and must not pass silently."""
    db = importlib.import_module("db")
    a = tmp_path / "a.mp4"

    out = "\n".join(ing._reconcile_report([a], {str(a): "already-indexed"}, set()))

    assert "no DB row" in out and "a.mp4" in out


def test_failed_and_not_reached_are_named(ing, tmp_path):
    db = importlib.import_module("db")
    ok, bad, rest1, rest2 = (tmp_path / n for n in ("ok.mp4", "bad.mp4", "r1.mp4", "r2.mp4"))
    outcomes = {str(ok): "ingested", str(bad): "failed"}

    lines = ing._reconcile_report([ok, bad, rest1, rest2], outcomes, _known(db, ok),
                                  not_reached=[rest1, rest2])
    out = "\n".join(lines)

    assert "failed=1" in lines[0] and "not-reached=2" in lines[0]
    assert "⚠ 3 file(s) need attention" in lines[0]
    for n in ("bad.mp4", "r1.mp4", "r2.mp4"):
        assert n in out
    assert "unaccounted" not in out, "not-reached files are accounted for, just not done"


def test_limit_counts_toward_scanned(ing, tmp_path):
    """--limit drops files before the loop; "scanned" must still mean scanned."""
    db = importlib.import_module("db")
    a = tmp_path / "a.mp4"

    lines = ing._reconcile_report([a], {str(a): "ingested"}, _known(db, a),
                                  deferred=4, prefiltered_known=2)

    assert "scanned=7" in lines[0]
    assert "already-indexed=2" in lines[0]
    assert "deferred(--limit)=4" in lines[0]
    assert "✓" in lines[0], "a deliberate --limit is not a problem"


def test_skip_failed_names_every_file_not_just_twenty(ing, tmp_path):
    """#393 point 3: when --skip-failed actually skips, print the file names."""
    db = importlib.import_module("db")
    skips = [("clip_{0:02d}.mp4".format(i), 1, 3) for i in range(25)]

    out = "\n".join(ing._reconcile_report([], {}, set(), frame_skips=skips, skip_failed=True))

    assert "--skip-failed skipped vision frames in 25 file(s)" in out
    for fn, _, _ in skips:
        assert fn in out


def test_names_are_capped_with_a_count(ing, tmp_path):
    files = [tmp_path / "f{0}.mp4".format(i) for i in range(5)]

    out = "\n".join(ing._reconcile_report(files, {}, set(), name_cap=2))

    assert "f0.mp4" in out and "f1.mp4" in out and "f4.mp4" not in out
    assert "… and 3 more" in out


# ── through main(): the line is printed and the exit code is unchanged ─────
@pytest.fixture
def run_main(tmp_db, tmp_path, monkeypatch, ing, capsys):
    """Run ingest.main() over a real directory with every model / probe stubbed.
    `behaviour` maps filename → "ok" | "fail" | "noread" | "raise" | "lost"
    ("fail" = bare {} with no reason, "noread" = a _NoRecord carrying one,
    "raise" = process_file throws; "lost" = process_file
    reports success but the row lands under a different path, i.e. the batch
    counts it while the library does not have it)."""
    db = importlib.import_module("db")
    config = importlib.import_module("config")
    health = importlib.import_module("health")

    monkeypatch.setattr(health, "preflight_paths", lambda: (True, []))
    monkeypatch.setattr(ing.tr, "warm_up", lambda: None)
    monkeypatch.setattr(ing.tr, "warm_up_ollama", lambda: None)
    monkeypatch.setattr(ing, "build_proxies", lambda: (0, 0))
    monkeypatch.setattr(ing, "_file_hash", lambda p: None)
    monkeypatch.setattr(ing, "detect_gpu", lambda: "test")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)  # bench_ingest.json lands here

    def go(behaviour, extra=()):
        media = tmp_path / "CLIP"
        media.mkdir(exist_ok=True)
        for name in behaviour:
            (media / name).write_bytes(b"\x00" * 8)

        def fake_process(path, skip_vision, existing=None, refresh=False, file_hash=None):
            mode = behaviour[path.name]
            if mode == "fail":
                return {}
            if mode == "noread":
                return ing._NoRecord("ffprobe could not read the file")
            if mode == "raise":
                raise OSError("[Errno 5] Input/output error: '/Volumes/X/" + path.name + "'")
            stored = path if mode in ("ok", "vis") else path.with_name("elsewhere_" + path.name)
            frames = [{"index": 0, "timestamp_s": 0.0, "thumbnail_path": "t.jpg"}] if mode == "vis" else []
            return {"path": db.to_relative(str(stored)), "filename": stored.name,
                    "ext": path.suffix.lower(), "duration_s": 1.0, "_frames": frames}

        monkeypatch.setattr(ing, "process_file", fake_process)
        vision = ["--skip-vision"] if "vis" not in behaviour.values() else []
        monkeypatch.setattr(sys, "argv", ["ingest.py", "--dir", str(media),
                                          *vision, "--no-embed", *extra])
        code = 0
        try:
            ing.main()
        except SystemExit as e:
            code = e.code
        return code, capsys.readouterr().out
    return go


def test_main_prints_reconcile_on_a_clean_batch(run_main):
    code, out = run_main({"a.mp4": "ok", "b.mp4": "ok"})

    assert "Reconcile: scanned=2  ingested=2  already-indexed=0  moved=0  failed=0" in out
    assert "✓ every file accounted for" in out
    assert code in (0, None)


def test_main_names_a_file_the_counters_say_is_in_but_the_db_lacks(run_main):
    code, out = run_main({"a.mp4": "ok", "A7520260820_9912.MP4": "lost"})

    assert "ingested=2" in out, "precondition: the counter believes both made it"
    assert "no DB row" in out
    assert "A7520260820_9912.MP4" in out
    assert code == ing_exit_mismatch(), "#393 point 2: a file with no DB row is not a clean run"
    assert "[exit 7] reconcile mismatch: 1 file(s)" in out


def test_main_keeps_its_exit_code_on_a_partial_failure(run_main):
    code, out = run_main({"a.mp4": "ok", "bad.mp4": "fail"})

    assert "failed=1" in out and "bad.mp4" in out
    assert code == 1, "the pre-existing partial-failure exit code is untouched"


def test_main_counts_a_rerun_as_already_indexed(run_main):
    run_main({"a.mp4": "ok"})
    code, out = run_main({"a.mp4": "ok"})

    assert "already-indexed=1" in out
    assert "✓ every file accounted for" in out


# ── --vision-only --skip-failed: every skipped name, not the first 20 ───────
def test_vision_only_skip_failed_prints_every_file_name(tmp_db, ing, monkeypatch, capsys):
    db = importlib.import_module("db")
    monkeypatch.setattr(ing, "_unload_ollama_model", lambda *a, **k: None)
    monkeypatch.setattr(ing, "_ensure_vision_ready", lambda *a, **k: None)
    monkeypatch.setattr(ing, "_preflight_vision_model", lambda *a, **k: None)
    monkeypatch.setattr(ing.vis, "model_available", lambda name: True)
    # one frame fails on both models, the other succeeds → partial failure per
    # file, so the consecutive-outage guard never trips and the run completes.
    monkeypatch.setattr(ing.vis, "describe_frames", lambda paths, model=None: [
        {"description": ""} if "FAIL" in str(p) else {"description": "d", "tags": ["t"]}
        for p in paths])

    names = ["clip_{0:02d}.mp4".format(i) for i in range(25)]
    for name in names:
        db.upsert({"path": name, "filename": name, "ext": ".mp4"})
        with db.get_conn() as c:
            mid = c.execute("SELECT id FROM media WHERE path=?", (name,)).fetchone()["id"]
        db.upsert_frame(mid, 0, 0.0, thumbnail_path="ok.jpg", description="")
        db.upsert_frame(mid, 1, 1.0, thumbnail_path="FAIL.jpg", description="")

    halted, _ = ing._run_vision_only(types.SimpleNamespace(
        max_failures=0, skip_failed=True, dir=None))
    out = capsys.readouterr().out

    assert halted is False
    for name in names:
        assert name in out.split("left empty", 1)[1], name


# ── #393 point 2: the exit code ──────────────────────────────────────────────
def test_exit_code_number_does_not_collide(ing):
    """0-6 are taken (0 clean, 1 partial, 2 all-fail, 3 frames.py symlink guard,
    4 preflight, 5/6 --migrate-storage). Pin the value so a renumbering is a
    deliberate, documented change rather than an accident."""
    assert ing.EXIT_RECONCILE_MISMATCH == 7


def test_mismatch_counts_only_the_traceless_kinds(ing, tmp_path):
    db = importlib.import_module("db")
    ok, lost, gone, bad, late = (tmp_path / n for n in
                                 ("ok.mp4", "lost.mp4", "gone.mp4", "bad.mp4", "late.mp4"))
    queued = [ok, lost, gone, bad, late]
    outcomes = {str(ok): "ingested", str(lost): "ingested", str(bad): "failed"}
    # gone: in no bucket; lost: counted but no row; bad: failed (has its own
    # record + exit 1); late: not reached after a halt (has its own record).
    n = ing._reconcile_mismatch(queued, outcomes, _known(db, ok), not_reached=[late])
    assert n == 2


def test_mismatch_is_zero_on_a_clean_batch(ing, tmp_path):
    db = importlib.import_module("db")
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    assert ing._reconcile_mismatch(
        [a, b], {str(a): "ingested", str(b): "already-indexed"}, _known(db, a, b)) == 0


def test_mismatch_outranks_a_partial_failure(run_main, ing):
    code, out = run_main({"a.mp4": "ok", "bad.mp4": "fail", "lost.mp4": "lost"})
    assert "failed=1" in out and "no DB row" in out
    assert code == ing.EXIT_RECONCILE_MISMATCH


def test_all_failed_still_exits_2(run_main):
    code, _ = run_main({"bad1.mp4": "fail", "bad2.mp4": "fail"})
    assert code == 2


def test_reconcile_unavailable_is_not_a_mismatch(run_main, ing, monkeypatch):
    """The DB read for the cross-check fails → 'unavailable', and the exit code
    falls back to the other signals. Not measured must not be reported as either
    'clean' by the line or 'mismatch' by the code."""
    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(ing, "_reconcile", boom)
    code, out = run_main({"a.mp4": "ok", "lost.mp4": "lost"})
    assert "Reconcile: unavailable (RuntimeError: db locked)" in out
    assert code in (0, None)


# ── #393 point 4: a failed file's reconcile line carries its cause ───────────
def test_main_reconcile_names_the_reason_for_a_no_record_failure(run_main):
    """process_file returned an empty record and raised nothing — the blind-spot
    shape from #393. The cause must sit next to the name in the summary."""
    code, out = run_main({"a.mp4": "ok", "bad.mp4": "noread"})

    summary = out.split("Reconcile:", 1)[1]
    assert "bad.mp4 — ffprobe could not read the file" in summary
    assert code == 1, "exit code is unchanged by the reason"


def test_main_reconcile_names_a_generic_reason_when_none_was_given(run_main):
    code, out = run_main({"a.mp4": "ok", "bad.mp4": "fail"})

    summary = out.split("Reconcile:", 1)[1]
    assert "bad.mp4 — " + "process_file returned no record (no exception raised)" in summary
    assert code == 1


def test_main_reconcile_carries_the_exception_into_the_summary(run_main):
    code, out = run_main({"a.mp4": "ok", "gone.mp4": "raise"})

    summary = out.split("Reconcile:", 1)[1]
    assert "gone.mp4 — OSError: [Errno 5] Input/output error" in summary
    assert code == 1, "exit code is unchanged by the reason"


def test_process_file_returns_a_falsy_record_with_a_reason_when_ffprobe_fails(
        ing, tmp_path, monkeypatch):
    clip = tmp_path / "broken.mp4"
    clip.write_bytes(b"\x00" * 8)
    monkeypatch.setattr(ing, "probe", lambda p: None)

    rec = ing.process_file(clip, skip_vision=True)

    assert not rec and rec == {}, "callers that test truthiness must see no record"
    assert ing._failure_reason(record=rec) == "ffprobe could not read the file"


def test_failure_reason_is_one_capped_line(ing):
    r = ing._failure_reason(exc=RuntimeError("line one\nline two " + "x" * 500))
    assert "\n" not in r and r.startswith("RuntimeError: line one line two")
    assert len(r) == 200 and r.endswith("…")
    assert ing._failure_reason(exc=KeyError()) == "KeyError"


def test_reconcile_report_accepts_bare_and_tuple_outcomes(ing, tmp_path):
    db = importlib.import_module("db")
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    outcomes = {str(a): "ingested", str(b): ("failed", "boom")}
    out = "\n".join(ing._reconcile_report([a, b], outcomes, _known(db, a)))
    assert "ingested=1" in out and "failed=1" in out
    assert "b.mp4 — boom" in out


# ── #393 root cause: the verdict must be the LAST line (survives `| tail`) ──────
def _last_line(out):
    return [l for l in out.splitlines() if l.strip()][-1]


def test_partial_failure_ends_with_an_exit_line_naming_file_and_reason(run_main):
    code, out = run_main({"a.mp4": "ok", "A7520260820_9912.MP4": "noread"})
    assert code == 1
    assert _last_line(out) == ("[exit 1] 1 file(s) failed: "
                               "A7520260820_9912.MP4 — ffprobe could not read the file")


def test_all_failed_ends_with_an_exit_2_line(run_main):
    code, out = run_main({"bad1.mp4": "raise", "bad2.mp4": "fail"})
    assert code == 2
    last = _last_line(out)
    assert last.startswith("[exit 2] 2 file(s) failed: ")
    assert "bad1.mp4 — OSError" in last and "bad2.mp4 — " in last


def test_exit_line_survives_a_tail_after_benchmark(run_main):
    """The concrete #393 failure: `… | tail -3` showed only BENCHMARK rows."""
    code, out = run_main({"a.mp4": "ok", "b.mp4": "ok", "bad.mp4": "fail"})
    tail3 = "\n".join(out.splitlines()[-3:])
    assert code == 1
    assert "[exit 1]" in tail3 and "bad.mp4" in tail3


def test_exit_line_caps_names_and_handles_bare_outcomes(ing):
    outcomes = {"/x/f%d.mp4" % i: ("failed", "r%d" % i) for i in range(4)}
    outcomes["/x/bare.mp4"] = "failed"
    outcomes["/x/ok.mp4"] = ("ingested", "")
    line = ing._exit_tail_line(1, outcomes)
    assert line.startswith("[exit 1] 5 file(s) failed: f0.mp4 — r0; f1.mp4 — r1; f2.mp4 — r2")
    assert "(+2 more, see output above)" in line and "f3.mp4" not in line
    assert ing._exit_tail_line(1, {"/x/bare.mp4": "failed"}) == \
        "[exit 1] 1 file(s) failed: bare.mp4 — no reason recorded"


def test_exit_line_names_vision_errors_and_halt_reason(ing):
    line = ing._exit_tail_line(1, {}, [("v.mp4", "RuntimeError: ollama gone")],
                               "3 consecutive vision errors (last: v.mp4)", True)
    assert line == ("[exit 1] vision failed on 1 file(s): v.mp4 — RuntimeError: ollama gone; "
                    "vision halted: 3 consecutive vision errors (last: v.mp4)")


def test_vision_only_halt_ends_with_an_exit_line(ing, monkeypatch, capsys, tmp_db):
    """--vision-only halt: embed output follows the VISION HALTED banner, so the
    verdict has to be restated last (#393 shape on the vision-only path)."""
    monkeypatch.setattr(ing, "_run_vision_only", lambda args: (True, [1, 2]))
    monkeypatch.setattr(importlib.import_module("health"), "preflight_paths", lambda: (True, []))
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--vision-only", "--no-embed"])
    code = 0
    try:
        ing.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    assert code == 1
    assert _last_line(out) == ("[exit 1] vision-only halted mid-patch "
                               "(2 clip(s) updated; see VISION HALTED above)")


def test_clean_batch_prints_no_exit_line(run_main):
    code, out = run_main({"a.mp4": "ok"})
    assert code in (0, None) and "[exit " not in out


def test_batch_vision_halt_ends_with_an_exit_line_naming_the_cause(run_main, ing, monkeypatch):
    """Phase-2 whole-file vision errors → halt after 3 in a row. The final line
    must name the files, the error, and why it halted — not just `vision halted`."""
    for name in ("_preflight_vision_model", "_unload_ollama_model", "_ensure_vision_ready"):
        monkeypatch.setattr(ing, name, lambda *a, **k: None)

    def boom(paths):
        raise RuntimeError("ollama connection refused")
    monkeypatch.setattr(ing, "_describe_frames_with_fallback", boom)

    code, out = run_main({"v1.mp4": "vis", "v2.mp4": "vis", "v3.mp4": "vis"})
    last = _last_line(out)
    assert "VISION HALTED" in out, "precondition: the halt path ran"
    assert code == 1
    assert last.startswith("[exit 1] vision failed on 3 file(s): v1.mp4 — RuntimeError: "
                           "ollama connection refused"), last
    assert last.endswith("vision halted: 3 consecutive vision errors (last: v3.mp4)"), last


def test_exit_line_stays_one_line_with_newlines_in_names(ing):
    line = ing._exit_tail_line(1, {"/x/bad\nname.mp4": ("failed", "OSError: a\nb")})
    assert "\n" not in line
    assert line == "[exit 1] 1 file(s) failed: bad name.mp4 — OSError: a b"


def test_vision_model_preflight_ends_with_an_exit_line(ing, monkeypatch, capsys, tmp_db):
    monkeypatch.setattr(ing.vis, "is_model_installed", lambda m: False)
    monkeypatch.setattr(ing.vis, "list_vision_models", lambda: ["other:7b"])
    try:
        ing._preflight_vision_model()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    assert code == 2
    assert _last_line(out).startswith("[exit 2] vision model not installed: ")


def test_missing_dir_ends_with_an_exit_line(ing, monkeypatch, capsys, tmp_path, tmp_db):
    monkeypatch.setattr(importlib.import_module("health"), "preflight_paths", lambda: (True, []))
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--dir", str(tmp_path / "nope"), "--no-embed"])
    try:
        ing.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    assert code == 1 and _last_line(out).startswith("[exit 1] --dir does not exist: ")


def test_storage_preflight_ends_with_an_exit_line(ing, monkeypatch, capsys, tmp_path, tmp_db):
    monkeypatch.setattr(importlib.import_module("health"), "preflight_paths",
                        lambda: (False, ["thumbs dir missing"]))
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--dir", str(tmp_path), "--no-embed"])
    try:
        ing.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    assert code == 4 and _last_line(out) == "[exit 4] storage preflight failed: 1 problem(s) (see above)"
