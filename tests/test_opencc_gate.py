"""issue #279 — a missing opencc used to be SILENT at three layers, so a 1506-clip
library was transcribed with zh→Traditional conversion secretly off and every check
stayed green (audit 2026-07-30: 81 Simplified rows stored, health all-PASS,
`--retraditionalize --dry-run` reporting "0 to convert").

The degrade-to-identity behaviour is kept (a missing wheel must never break a
transcribe) — these tests pin that it is no longer silent:
  1. zh_convert warns once when asked to convert CJK with no converter
  2. the backfill flags the run instead of reporting a clean-looking 0
  3. health.py WARNs (not a quiet SKIP) when the library has zh media
"""
import importlib

import pytest

zh = importlib.import_module("zh_convert")
retrad = importlib.import_module("retraditionalize")
health = importlib.import_module("health")


@pytest.fixture
def no_opencc(monkeypatch):
    """Simulate opencc being absent: every converter build returns None."""
    monkeypatch.setattr(zh, "_converter", lambda config: None)
    monkeypatch.setattr(zh, "_warned_no_converter", False)
    return monkeypatch


# ── 1. zh_convert: identity degrade is loud (once) ───────────────────────────
def test_convert_warns_once_on_cjk_without_converter(no_opencc, capsys):
    assert zh.to_taiwan("这是软件") == "这是软件"      # still degrades, never raises
    zh.to_taiwan("这是视频")                            # second call
    err = capsys.readouterr().err
    assert "opencc is not installed" in err
    assert err.count("opencc is not installed") == 1   # warn ONCE, not per clip


def test_no_warning_for_latin_text(no_opencc, capsys):
    zh.to_taiwan("this is english narration")
    assert "opencc" not in capsys.readouterr().err     # would fire on every en clip


def test_opencc_available_reports_false(no_opencc):
    assert zh.opencc_available() is False


# ── 2. backfill: a 0 that means "couldn't look" says so ──────────────────────
def test_backfill_flags_missing_opencc(no_opencc, tmp_db):
    counts = retrad.backfill(dry_run=True)
    assert counts["opencc_missing"] is True


def test_summary_leads_with_blocker_not_clean_zeros(no_opencc):
    counts = retrad._new_counts()
    counts["opencc_missing"] = True
    out = retrad.format_summary(counts, dry_run=True)
    assert "opencc is NOT installed" in out
    assert "could not check" in out                    # explicitly not "library is clean"


def test_summary_normal_when_opencc_present():
    counts = retrad._new_counts()          # opencc_missing defaults False
    out = retrad.format_summary(counts, dry_run=True)
    assert "opencc is NOT installed" not in out
    assert "zh scanned" in out


# ── 3. health: WARN when the library actually has zh content ─────────────────
def test_health_warns_when_zh_media_present(monkeypatch, capsys):
    monkeypatch.setattr(zh, "opencc_available", lambda: False)
    monkeypatch.setattr(health, "_zh_media_count", lambda: 81)
    monkeypatch.setattr(health, "WARN_COUNT", 0)
    health._check_opencc()
    out = capsys.readouterr().out
    assert "[WARN]" in out and "81 zh transcript" in out
    assert "[SKIP]" not in out                          # must NOT read as benign
    assert health.WARN_COUNT == 1


def test_health_skips_when_no_zh_content(monkeypatch, capsys):
    monkeypatch.setattr(zh, "opencc_available", lambda: False)
    monkeypatch.setattr(health, "_zh_media_count", lambda: 0)
    health._check_opencc()
    out = capsys.readouterr().out
    assert "[SKIP]" in out and "[WARN]" not in out      # genuinely optional here


def test_health_passes_when_opencc_present(monkeypatch, capsys):
    monkeypatch.setattr(zh, "opencc_available", lambda: True)
    health._check_opencc()
    assert "[PASS]" in capsys.readouterr().out
