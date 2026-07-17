"""Wave-0 ingest robustness: --dir unsupported-file skip notice.

The #1 silent trust-breaker for a first-wave user was a --dir scan dropping
pro/cinema footage (.mxf/.braw/.r3d) with zero feedback → "Found 0 media files".
_report_unsupported gives that a visible summary. (scene-timeout cap + forced-zh
notice in this PR are inline one-liners, exercised via the ingest path.)
"""
from pathlib import Path

import ingest


def test_report_unsupported_names_pro_codecs(capsys):
    all_files = [Path("/card/a.mp4"), Path("/card/b.MXF"), Path("/card/c.braw"), Path("/card/d.mov")]
    supported = [Path("/card/a.mp4"), Path("/card/d.mov")]
    ingest._report_unsupported(all_files, supported)
    out = capsys.readouterr().out
    assert "Skipped 2 unsupported" in out
    assert ".mxf" in out and ".braw" in out          # case-folded
    assert "Pro/cinema" in out                        # pro-codec callout fired


def test_report_unsupported_noop_when_all_supported(capsys):
    files = [Path("/x/a.mp4"), Path("/x/b.mov")]
    ingest._report_unsupported(files, files)
    assert capsys.readouterr().out == ""


def test_report_unsupported_generic_only_no_pro_line(capsys):
    # unsupported but non-pro (e.g. a stray .txt) → count shown, no Pro/cinema line
    all_files = [Path("/x/a.mp4"), Path("/x/notes.txt")]
    supported = [Path("/x/a.mp4")]
    ingest._report_unsupported(all_files, supported)
    out = capsys.readouterr().out
    assert "Skipped 1 unsupported" in out
    assert "Pro/cinema" not in out


def test_ingest_halt_decision_streak():
    # below threshold → keep going; at/over → halt; threshold 0 → disabled
    assert ingest._ingest_halt_decision(4, 5) == (False, "")
    halt, reason = ingest._ingest_halt_decision(5, 5)
    assert halt is True and "consecutive" in reason
    assert ingest._ingest_halt_decision(99, 5)[0] is True
    assert ingest._ingest_halt_decision(99, 0) == (False, "")  # 0 disables the guard


def test_ingest_halt_decision_default_threshold():
    # default threshold comes from the module constant (env-tunable, default 5)
    thr = ingest._INGEST_CONSECUTIVE_HALT
    if thr > 0:
        assert ingest._ingest_halt_decision(thr)[0] is True
        assert ingest._ingest_halt_decision(thr - 1)[0] is False
