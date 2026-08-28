"""Quiet recordings lose whole passages before whisper ever sees them.

VAD reads a low-level track as silence and drops it, so the transcript comes back
with holes — and the holes land disproportionately on relaxed, quietly-spoken
passages, which in mixed Mandarin/Taiwanese material is exactly where the Taiwanese
tends to be. Normalising the audio recovers them.

**But only for tracks that need it**, and that gate is the whole design. Running
`dynaudnorm` over an already well-levelled track pulls the noise floor up in the
quiet stretches — which is where hallucinations come from. So the level is measured
first and the filter is applied only below a threshold.

⚠️ **That is the design. Measured 2026-08-28, it is not what happens** — across 13
clips all sitting below the gate: 8 identical, 5 worse with it, 0 better, and the
three quietest produced nothing either way. The default is now OFF (see config.py
for the numbers). These tests still pin the mechanism, because the mechanism is
still correct and still reachable by env var; what changed is which way it ships.
"""
from __future__ import annotations

import importlib
import os

import subprocess

import pytest

import transcribe as tr


class _FakeRun:
    """Capture the ffmpeg argv, and answer volumedetect with a scripted level."""

    def __init__(self, mean_db=None, fail_probe=False):
        self.mean_db = mean_db
        self.fail_probe = fail_probe
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "volumedetect" in " ".join(cmd):
            if self.fail_probe:
                raise OSError("ffmpeg missing")
            body = ("" if self.mean_db is None
                    else "[Parsed_volumedetect_0 @ 0x0] mean_volume: {0} dB\n".format(self.mean_db))
            return subprocess.CompletedProcess(cmd, 0, b"", body.encode())
        # the extract call: pretend it wrote the output file
        out = cmd[-2]
        with open(out, "wb") as fh:
            fh.write(b"\x00" * 64)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


def _extract_cmd(fake):
    return next(c for c in fake.calls if "volumedetect" not in " ".join(c))


def test_a_quiet_track_is_normalised(monkeypatch):
    fake = _FakeRun(mean_db=-46.8)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE", True)

    tr._to_wav("/clip.mp4")

    assert "dynaudnorm" in " ".join(_extract_cmd(fake))


def test_a_healthy_track_is_left_alone(monkeypatch):
    """The half that protects existing libraries: normalising good audio raises the
    noise floor in the quiet parts, and that is where hallucinations start."""
    fake = _FakeRun(mean_db=-22.0)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE", True)

    tr._to_wav("/clip.mp4")

    assert "dynaudnorm" not in " ".join(_extract_cmd(fake))
    assert "-af" not in _extract_cmd(fake)


def test_an_unmeasurable_track_is_left_alone(monkeypatch):
    """No reading means no evidence the track needs help. Guessing 'probably quiet'
    would apply the filter to everything ffmpeg can't probe."""
    fake = _FakeRun(mean_db=None)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE", True)

    tr._to_wav("/clip.mp4")

    assert "dynaudnorm" not in " ".join(_extract_cmd(fake))


def test_a_failed_probe_does_not_break_extraction(monkeypatch):
    """The probe is an optimisation. If it throws, transcription must still run —
    losing the whole clip to a level check would be a far worse trade."""
    fake = _FakeRun(fail_probe=True)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE", True)

    out = tr._to_wav("/clip.mp4")

    assert out
    assert "dynaudnorm" not in " ".join(_extract_cmd(fake))


def test_the_feature_can_be_turned_off_entirely(monkeypatch):
    """Off means off — including the probe, so a library that doesn't want this
    doesn't pay an extra ffmpeg pass per clip either."""
    fake = _FakeRun(mean_db=-46.8)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE", False)

    tr._to_wav("/clip.mp4")

    assert not any("volumedetect" in " ".join(c) for c in fake.calls)
    assert "dynaudnorm" not in " ".join(_extract_cmd(fake))


def test_the_probe_writes_nothing(monkeypatch):
    """`-f null -` decodes to nowhere. A probe that re-encoded would double the
    cost of every ingest."""
    fake = _FakeRun(mean_db=-40.0)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE", True)

    tr._to_wav("/clip.mp4")

    probe = next(c for c in fake.calls if "volumedetect" in " ".join(c))
    assert probe[-2:] == ["-f", "null"] or probe[-3:-1] == ["-f", "null"]


@pytest.mark.parametrize("db,expected", [(-30.1, True), (-29.9, False)])
def test_the_threshold_is_where_it_says_it_is(monkeypatch, db, expected):
    fake = _FakeRun(mean_db=db)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE", True)
    monkeypatch.setattr(tr, "AUDIO_NORMALISE_BELOW_DB", -30.0)

    tr._to_wav("/clip.mp4")

    assert ("dynaudnorm" in " ".join(_extract_cmd(fake))) is expected


def test_the_level_is_parsed_from_ffmpeg_stderr(monkeypatch):
    fake = _FakeRun(mean_db=-33.8)
    monkeypatch.setattr(subprocess, "run", fake)

    assert tr._mean_volume_db("/clip.mp4") == pytest.approx(-33.8)


def test_level_normalisation_is_off_by_default(monkeypatch):
    """Measured 2026-08-28 across 13 clips that all sit below the gate:
    8 identical, 5 worse with it, **0 better**.

    The three quietest (-55.0, -39.9, -39.7 dB) — where recovering lost speech was
    the entire point — produced nothing either way. Where it changed the answer it
    added text rather than recovering it: one clip went from 3 segments to 10 by
    repeating one sentence three times, and one produced a DIFFERENT transcript on
    two runs of the same input while the un-normalised run was identical both
    times. That is the model generating, not transcribing.

    Pinned as a default rather than left to a comment, because the comment that
    used to justify ON described the exact failure this turned out to have.
    """
    if os.getenv("ARKIV_AUDIO_NORMALISE"):
        pytest.skip("the environment overrides the default under test")
    config = importlib.reload(importlib.import_module("config"))
    assert config.AUDIO_NORMALISE is False


def test_the_env_var_still_turns_it_back_on(monkeypatch):
    """The code stays: 13 clips from two libraries is not every kind of audio."""
    monkeypatch.setenv("ARKIV_AUDIO_NORMALISE", "1")
    config = importlib.reload(importlib.import_module("config"))
    try:
        assert config.AUDIO_NORMALISE is True
    finally:
        monkeypatch.delenv("ARKIV_AUDIO_NORMALISE")
        importlib.reload(config)
