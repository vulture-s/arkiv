"""Tests for issue #48 — vision frame-failure tolerance.

The vision phase used to halt the entire run on the first frame that failed both
the primary and fallback model (zero-tolerance). For a 481-frame overnight run a
single transient Ollama hiccup killed the night. `--max-failures N` / `--skip-failed`
add tolerance while a consecutive-failure guard preserves fail-fast on a real outage.
"""
import importlib
import types

import pytest


# ── Policy unit tests: _vision_halt_decision ────────────────────────────────
@pytest.fixture
def ing():
    return importlib.import_module("ingest")


def _decide(ing, total, consecutive, max_failures=0, skip_failed=False, file_failed=1, file_total=1):
    return ing._vision_halt_decision(file_failed, file_total, total, consecutive, max_failures, skip_failed)


def test_default_zero_tolerance_halts_on_first_failure(ing):
    halt, reason = _decide(ing, total=1, consecutive=1, max_failures=0)
    assert halt is True
    assert "max-failures=0" in reason


def test_max_failures_tolerates_up_to_n_then_halts(ing):
    assert _decide(ing, total=3, consecutive=1, max_failures=3)[0] is False  # at the limit → continue
    assert _decide(ing, total=4, consecutive=1, max_failures=3)[0] is True   # over the limit → halt


def test_skip_failed_never_halts_on_frame_failures(ing):
    halt, _ = _decide(ing, total=999, consecutive=1, skip_failed=True)
    assert halt is False


def test_consecutive_guard_fires_at_threshold(ing):
    k = ing._VISION_CONSECUTIVE_HALT
    assert _decide(ing, total=k, consecutive=k - 1, max_failures=10_000)[0] is False
    halt, reason = _decide(ing, total=k, consecutive=k, max_failures=10_000)
    assert halt is True
    assert "consecutive" in reason


def test_consecutive_guard_overrides_skip_failed(ing):
    # An Ollama outage must stop even an explicit --skip-failed run.
    k = ing._VISION_CONSECUTIVE_HALT
    halt, reason = _decide(ing, total=k, consecutive=k, skip_failed=True)
    assert halt is True
    assert "consecutive" in reason


# ── _describe_frames_with_fallback ──────────────────────────────────────────
def _install_fake_vision(ing, monkeypatch):
    """describe_frames mock keyed on path tokens. Reads vis.VISION_MODEL so the
    fallback pass can rescue a TFAIL (transient) frame but not a PFAIL one."""
    def fake(paths):
        out = []
        for p in paths:
            ps = str(p)
            is_fallback = "minicpm" in (ing.vis.VISION_MODEL or "")
            if "PFAIL" in ps:
                out.append({"description": ""})                       # both models fail
            elif "TFAIL" in ps and not is_fallback:
                out.append({"description": ""})                       # primary fails
            else:
                out.append({"description": "desc:" + ps, "tags": ["t"]})
        return out
    monkeypatch.setattr(ing.vis, "describe_frames", fake)


def test_fallback_rescues_transient_not_persistent(ing, monkeypatch):
    _install_fake_vision(ing, monkeypatch)
    results, still_failed = ing._describe_frames_with_fallback(
        ["ok_0.jpg", "TFAIL_1.jpg", "PFAIL_2.jpg"])
    assert still_failed == [2]                       # only the persistent one remains failed
    assert results[0]["description"].startswith("desc:")
    assert results[1]["description"].startswith("desc:")  # transient rescued by fallback
    assert not results[2].get("description")


# ── Integration: _run_vision_only tolerance + resumability ──────────────────
@pytest.fixture
def vision_db(tmp_db, monkeypatch):
    """A DB with one media + N frames (empty descriptions, thumbnail set) plus the
    Ollama-touching helpers stubbed out. Returns (ing, db, mid, make_frames)."""
    ing = importlib.import_module("ingest")
    db = importlib.import_module("db")
    monkeypatch.setattr(ing, "_unload_ollama_model", lambda *a, **k: None)
    monkeypatch.setattr(ing, "_ensure_vision_ready", lambda *a, **k: None)
    _install_fake_vision(ing, monkeypatch)

    def make(thumb_names):
        db.upsert({"path": "clip.mp4", "filename": "clip.mp4", "ext": ".mp4"})
        with db.get_conn() as c:
            mid = c.execute("SELECT id FROM media WHERE path=?", ("clip.mp4",)).fetchone()["id"]
        for i, name in enumerate(thumb_names):
            db.upsert_frame(mid, i, float(i), thumbnail_path=name, description="")
        return mid
    return ing, db, make


def _args(**kw):
    base = {"max_failures": 0, "skip_failed": False, "dir": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _desc_count(db, mid):
    with db.get_conn() as c:
        done = c.execute("SELECT COUNT(*) AS n FROM frames WHERE media_id=? AND description<>''", (mid,)).fetchone()["n"]
        empty = c.execute("SELECT COUNT(*) AS n FROM frames WHERE media_id=? AND (description IS NULL OR description='')", (mid,)).fetchone()["n"]
    return done, empty


def test_default_halts_on_persistent_failure(vision_db):
    ing, db, make = vision_db
    mid = make(["ok_0.jpg", "PFAIL_1.jpg", "ok_2.jpg"])
    halted = ing._run_vision_only(_args())          # default zero-tolerance
    assert halted is True


def test_skip_failed_completes_and_leaves_failures_empty(vision_db):
    ing, db, make = vision_db
    mid = make(["ok_0.jpg", "PFAIL_1.jpg", "ok_2.jpg", "PFAIL_3.jpg"])
    halted = ing._run_vision_only(_args(skip_failed=True))
    assert halted is False                          # never halts on frame failures
    done, empty = _desc_count(db, mid)
    assert done == 2                                # the two ok frames described
    assert empty == 2                               # the two PFAIL frames left empty → resumable


def test_skip_failed_resumable_picks_up_only_failed(vision_db):
    """After a skip-failed run, the failed frames still satisfy the --vision-only
    'empty description' query — i.e. a re-run retries exactly them."""
    ing, db, make = vision_db
    mid = make(["ok_0.jpg", "PFAIL_1.jpg"])
    ing._run_vision_only(_args(skip_failed=True))
    with db.get_conn() as c:
        resumable = c.execute(
            "SELECT frame_index FROM frames WHERE media_id=? AND (description IS NULL OR description='') "
            "AND thumbnail_path IS NOT NULL", (mid,)).fetchall()
    assert [r["frame_index"] for r in resumable] == [1]   # only the PFAIL frame


def test_max_failures_tolerates_then_halts(vision_db):
    ing, db, make = vision_db
    # 3 persistent failures across one file; max_failures=2 → exceeded → halt.
    mid = make(["PFAIL_0.jpg", "PFAIL_1.jpg", "PFAIL_2.jpg"])
    halted = ing._run_vision_only(_args(max_failures=2))
    assert halted is True
