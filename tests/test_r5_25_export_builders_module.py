"""R5-25 (round-5 #51): export-format builders extracted to export_builders.py.

Third leaf module of the APIRouter split. Holds the pure serialisers that turn a
media record into CSV / EDL / FCPXML / SRT / VTT text, plus the timecode +
framerate math they share. Every function is pure except `_build_metadata_csv`,
which reads the DB via `db` (itself a leaf — no cycle).

These tests pin the module boundary (leaf import graph + identity re-export) and
a representative slice of the format/injection-hardening behaviour so the move is
provably semantics-preserving. Full route-level export coverage already lives in
the existing test_export*/timeline suites.
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_NAMES = (
    "_CSV_FORMULA_PREFIXES",
    "_csv_safe",
    "_parse_frame_tags",
    "_build_metadata_csv",
    "_edl_reel",
    "_media_streams",
    "_edl_comment",
    "_subtitle_ts",
    "_subtitle_text",
    "_edl_timecode",
    "_start_tc_seconds",
    "_edl_fps_warning",
    "_fcpxml_rational",
)


# ── module boundary ──────────────────────────────────────────────────────────
def test_export_builders_is_a_leaf_module():
    src = (_ROOT / "export_builders.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_server_reexports_export_builders_by_identity():
    import server
    import export_builders
    for name in _NAMES:
        assert getattr(server, name) is getattr(export_builders, name), (
            "server.{0} must BE export_builders.{0} (a re-export)".format(name)
        )


def test_http_and_log_helpers_did_not_go_to_export_builders():
    # _attachment_headers (Content-Disposition) and _log_safe (terminal sanitiser)
    # are HTTP/logging concerns, not export-format serialisers — they must NOT have
    # been pulled into export_builders. _log_safe later moved to routers/misc.py
    # with its only caller /api/client-log (R5-25 misc peel); _attachment_headers
    # still lives in server (moves with the export routes in a later peel).
    import export_builders
    assert not hasattr(export_builders, "_attachment_headers")
    assert not hasattr(export_builders, "_log_safe")
    import server
    assert hasattr(server, "_attachment_headers")
    import routers.misc
    assert hasattr(routers.misc, "_log_safe")


# ── injection-hardening / format behaviour preserved by the move ─────────────
def test_csv_safe_defuses_formula_injection():
    import export_builders as eb
    assert eb._csv_safe("=1+1") == "'=1+1"
    assert eb._csv_safe("@cmd") == "'@cmd"
    assert eb._csv_safe("normal") == "normal"
    assert eb._csv_safe("") == ""


def test_edl_reel_strips_control_chars_and_pads_8():
    import export_builders as eb
    # a CR/LF-poisoned reel_name must not survive to inject EDL header lines
    reel = eb._edl_reel({"reel_name": "A001\r\nFCM: NONAME"}, "STEM")
    assert "\r" not in reel and "\n" not in reel
    assert len(reel) == 8
    # blank reel_name → stem fallback, padded/truncated to 8
    assert eb._edl_reel({"reel_name": "   "}, "MYSTEM").rstrip() == "MYSTEM"


def test_edl_comment_and_subtitle_text_neutralise_injection():
    import export_builders as eb
    assert "\n" not in eb._edl_comment("shot\nFCM: evil")
    # a literal cue boundary in transcript text must be neutralised
    assert "-->" not in eb._subtitle_text("fake 00:00 --> 00:01 boundary")


def test_subtitle_ts_and_edl_timecode_formats():
    import export_builders as eb
    assert eb._subtitle_ts(3661.5) == "01:01:01,500"       # SRT comma
    assert eb._subtitle_ts(3661.5, sep=".") == "01:01:01.500"  # VTT dot
    assert eb._subtitle_ts(-5) == "00:00:00,000"           # negative clamped
    assert eb._edl_timecode(1.0, 25.0) == "00:00:01:00"    # NDF
    assert eb._edl_timecode(1.0, 30.0, drop_frame=True).count(";") == 1  # DF sep


def test_fcpxml_rational_exact_for_ntsc():
    import export_builders as eb
    assert eb._fcpxml_rational(29.97) == ("1001", "30000")
    assert eb._fcpxml_rational(23.976) == ("1001", "24000")
    assert eb._fcpxml_rational(25.0) == ("1", "25")


def test_edl_fps_warning_only_on_mixed_rates():
    import export_builders as eb
    assert eb._edl_fps_warning([{"fps": 25.0}, {"fps": 25.0}], 25.0) is None
    warn = eb._edl_fps_warning([{"fps": 25.0}, {"fps": 30.0}], 25.0)
    assert warn is not None and "mixed frame rates" in warn


def test_parse_frame_tags_json_and_legacy():
    import export_builders as eb
    import json
    ft = json.dumps([
        {"description": "a wide shot", "tags": ["wide"], "content_type": "B-Roll"},
        {"description": "closeup", "tags": ["wide", "cu"]},
    ])
    first, descs, tags, ct, atmo, energy, edit = eb._parse_frame_tags(ft)
    assert first == "a wide shot"
    assert descs == ["a wide shot", "closeup"]
    assert tags == ["wide", "cu"]           # deduped, order-preserving
    assert ct == "B-Roll"
    # legacy plain-text frame_tags → first line + all lines
    first2, descs2, tags2, *_ = eb._parse_frame_tags("line one\nline two")
    assert first2 == "line one"
    assert descs2 == ["line one", "line two"]
    assert tags2 == []
    # empty → all-empty tuple
    assert eb._parse_frame_tags("") == ("", [], [], None, None, None, None)
