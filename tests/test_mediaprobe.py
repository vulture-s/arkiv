"""Can this environment read and decode THIS library's media?

Every case here is one that was measured on 2026-08-27/28 and that a
presence-style health check reports as green: an ffmpeg that exists but has no
decoder for the library's codec, a file that exists and stats and still cannot be
opened, a probe that answers about a different binary than the one doing the work.

The ffmpeg-dependent tests drive a fake binary that replays the EXACT stderr the
real failures produced — the parser's job is to read what ffmpeg actually says,
not what it seemed to say from memory.
"""
from __future__ import annotations

import os
import stat
import textwrap

import pytest

import mediaprobe as mp


def _fake_ffmpeg(tmp_path, stderr_text, name="ffmpeg-fake"):
    """A stand-in that prints canned stderr and exits 0, plus records its argv."""
    argv_log = tmp_path / (name + ".argv")
    script = tmp_path / name
    script.write_text(textwrap.dedent("""\
        #!/bin/sh
        printf '%s\\n' "$*" >> "{log}"
        cat >&2 <<'FFEOF'
        {body}
        FFEOF
        exit 0
        """).format(log=argv_log, body=stderr_text), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script), argv_log


# The real thing, captured from Synology's ffmpeg 4.1.9 on an iPhone .mov.
NAS_NO_AAC = """\
  Stream #0:0(und): Video: hevc (hvc1 / 0x31637668), none(bt2020nc/bt2020/unknown), 3840x2160
    Stream #0:1(und): Audio: aac (mp4a / 0x6134706D), 48000 Hz, stereo, 276 kb/s (default)
  Stream #0:1 -> #0:0 (? (?) -> pcm_s16le (native))
Decoder (codec aac) not found for input stream #0:1"""

HEALTHY = """\
    Stream #0:1(und): Audio: pcm_s16be (in24 / 0x34326E69), 48000 Hz, stereo, 2304 kb/s
[Parsed_volumedetect_0 @ 0x7f8] mean_volume: -21.5 dB
[Parsed_volumedetect_0 @ 0x7f8] max_volume: -0.0 dB"""

VIDEO_ONLY = """\
    Stream #0:0(und): Video: hevc (hvc1 / 0x31637668), 1920x1080, 11478 kb/s
Stream map 'a:0' matches no streams."""

DEMUXED_NO_SAMPLES = """\
    Stream #0:1(und): Audio: aac (mp4a / 0x6134706D), 48000 Hz, stereo, 129 kb/s"""


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "A7V" / "clip.MP4"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\x00" * 32)
    return str(p)


# ── the gates, in the order they fail ────────────────────────────────────────

def test_a_missing_file_is_named_as_such(tmp_path):
    r = mp.probe_one(str(tmp_path / "gone.MP4"))
    assert r["verdict"] == mp.MISSING


def test_a_file_that_exists_but_cannot_be_opened(tmp_path, clip):
    """macOS TCC on a network volume: the mount is there, `stat` works, `ls` shows
    the file, and the process is still forbidden to read it. `health.py`'s mount
    check passes — it only asks whether /Volumes/<name> exists."""
    os.chmod(clip, 0o000)
    try:
        if os.access(clip, os.R_OK):  # root, or a filesystem that ignores the mode
            pytest.skip("this user can read a 000 file; the gate needs a real denial")
        r = mp.probe_one(clip)
    finally:
        os.chmod(clip, 0o644)

    assert r["verdict"] == mp.UNREADABLE
    assert "Error" in r["detail"] or "denied" in r["detail"].lower()


def test_an_ffmpeg_without_the_codec_is_the_headline_case(tmp_path, clip):
    """Synology 4.1.9 measured: `-decoders` lists pcm_s16be and not aac. It demuxes
    the file, reports `Audio: aac`, and then cannot decode a sample. Every iPhone
    clip in that library transcribes to nothing, and nothing says why."""
    fake, _ = _fake_ffmpeg(tmp_path, NAS_NO_AAC)

    r = mp.probe_one(clip, ffmpeg=fake)

    assert r["verdict"] == mp.NO_DECODER
    assert r["codec"] == "aac"
    # The DETAIL is the actionable half, and it is what separates this branch from
    # the generic "produced no samples" fall-through — same verdict, different
    # thing to go and fix. Asserting only the verdict let the branch be deleted.
    assert "no decoder" in r["detail"], r["detail"]


def test_a_working_decoder_is_ok(tmp_path, clip):
    fake, _ = _fake_ffmpeg(tmp_path, HEALTHY)
    r = mp.probe_one(clip, ffmpeg=fake)
    assert r["verdict"] == mp.OK and r["codec"] == "pcm_s16be"


def test_a_file_with_no_audio_is_not_a_failure_of_the_environment(tmp_path, clip):
    """A silent clip is a fact about the footage. Reporting it as a broken decoder
    would send someone to reinstall ffmpeg over a title card."""
    fake, _ = _fake_ffmpeg(tmp_path, VIDEO_ONLY)
    assert mp.probe_one(clip, ffmpeg=fake)["verdict"] == mp.NO_AUDIO


def test_demuxed_but_no_samples_counts_as_unusable(tmp_path, clip):
    """The decoder may exist and still yield nothing on this file. Different cause,
    identical consequence for the pipeline — so it blocks either way."""
    fake, _ = _fake_ffmpeg(tmp_path, DEMUXED_NO_SAMPLES)
    r = mp.probe_one(clip, ffmpeg=fake)
    assert r["verdict"] == mp.NO_DECODER and r["codec"] == "aac"


def test_the_probe_bounds_what_it_decodes(tmp_path, clip):
    """Proving the decoder works does not require decoding a 30-minute clip, and an
    unbounded probe on a 1,500-clip library is not a preflight."""
    fake, log = _fake_ffmpeg(tmp_path, HEALTHY)

    mp.probe_one(clip, ffmpeg=fake, seconds=5)

    argv = log.read_text(encoding="utf-8")
    assert "-t 5" in argv, argv
    assert "-map a:0" in argv, "must exercise the same stream the pipeline extracts"


def test_it_uses_the_pipelines_own_ffmpeg_by_default(tmp_path, clip, monkeypatch):
    """The rule the whole module rests on: a probe that runs a different binary
    than the pipeline answers a different question. Three checks of the same
    nineteen clips gave three different answers before this existed."""
    fake, log = _fake_ffmpeg(tmp_path, HEALTHY)
    monkeypatch.setattr(mp.config, "FFMPEG_PATH", fake)

    mp.probe_one(clip)

    assert log.exists(), "probe did not run config.FFMPEG_PATH"


# ── sampling: the failure hides in the camera folder you didn't draw from ─────

def test_the_sample_spreads_across_camera_folders():
    """The measured library: `A7V/` is PCM off a Sony body and `iPhone Clip/reels/`
    is AAC off a phone. A sample that ignored the directory would have taken three
    files from one camera and declared the library fine while every phone clip was
    undecodable."""
    paths = (["A7V/{0}.MP4".format(i) for i in range(40)]
             + ["iPhone Clip/reels/{0}.mov".format(i) for i in range(6)])

    chosen = mp.choose_sample(paths, per_bucket=2, max_files=12)

    assert sum(1 for p in chosen if p.startswith("iPhone")) == 2
    assert sum(1 for p in chosen if p.startswith("A7V")) == 2


def test_a_biting_cap_still_leaves_every_kind_represented():
    """Draining bucket by bucket would spend the whole budget on whichever camera
    sorts first — which is exactly how a probe misses the broken one."""
    paths = []
    for cam in ("A", "B", "C", "D", "E"):
        paths += ["{0}/{1}.mov".format(cam, i) for i in range(10)]

    chosen = mp.choose_sample(paths, per_bucket=2, max_files=5)

    assert len(chosen) == 5
    assert len({p.split("/")[0] for p in chosen}) == 5, chosen


def test_the_same_library_samples_the_same_files_twice():
    """So a difference between two runs is a difference in the ENVIRONMENT. A
    random sample would let a fixed environment look flaky."""
    paths = ["A7V/{0}.MP4".format(i) for i in range(30)] + ["FX30/{0}.MP4".format(i) for i in range(30)]
    assert mp.choose_sample(paths) == mp.choose_sample(paths)


# ── what stops a batch, and what does not ────────────────────────────────────

def _result(files):
    by_codec = {}
    for r in files:
        slot = by_codec.setdefault(r["codec"] or r["verdict"],
                                   {"total": 0, "ok": 0, "verdicts": {}})
        slot["total"] += 1
        slot["ok"] += 1 if r["verdict"] == mp.OK else 0
        slot["verdicts"][r["verdict"]] = slot["verdicts"].get(r["verdict"], 0) + 1
    return {"sampled": len(files), "of": len(files), "files": files, "by_codec": by_codec}


def test_a_whole_codec_failing_is_blocking():
    res = _result([
        {"path": "a.MP4", "verdict": mp.OK, "codec": "pcm_s16be", "detail": ""},
        {"path": "b.mov", "verdict": mp.NO_DECODER, "codec": "aac", "detail": "x"},
        {"path": "c.mov", "verdict": mp.NO_DECODER, "codec": "aac", "detail": "x"},
    ])
    assert mp.blocking(res) == ["aac: 0/2 usable (no_decoder)"]


def test_one_bad_file_among_good_ones_is_not_blocking():
    """A corrupt clip is a corrupt clip. Halting a four-hour batch over it would
    make the guard the thing people switch off."""
    res = _result([
        {"path": "a.mov", "verdict": mp.OK, "codec": "aac", "detail": ""},
        {"path": "b.mov", "verdict": mp.NO_DECODER, "codec": "aac", "detail": "x"},
    ])
    assert mp.blocking(res) == []


def test_a_silent_clip_never_blocks():
    """`no_audio` is a fact about the footage, not about the environment — and it
    is the one verdict that must never stop a run."""
    res = _result([{"path": "title.mov", "verdict": mp.NO_AUDIO, "codec": None, "detail": ""}])
    assert mp.blocking(res) == []
    assert mp.NO_AUDIO not in mp.BLOCKING


def test_the_report_names_the_consequence_not_just_the_status():
    """Someone reading this at 2am needs "these transcribe to nothing, silently",
    not "aac: 0/2"."""
    res = _result([{"path": "b.mov", "verdict": mp.NO_DECODER, "codec": "aac", "detail": "no decoder"}])
    text = mp.format_report(res)
    assert "silently" in text and "aac" in text


def test_probe_resolves_paths_through_the_callers_resolver(tmp_path, monkeypatch):
    """Library rows hold paths relative to the media root; the probe must go
    through the same resolver the pipeline uses rather than guessing a root."""
    real = tmp_path / "A7V" / "clip.MP4"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"\x00")
    fake, log = _fake_ffmpeg(tmp_path, HEALTHY)

    res = mp.probe(["A7V/clip.MP4"], resolve=lambda p: str(tmp_path / p), ffmpeg=fake)

    assert res["files"][0]["verdict"] == mp.OK
    assert str(real) in log.read_text(encoding="utf-8")


# ── the preflight on the batch that produced the silent failure ──────────────

def _seed(paths):
    import importlib
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        for p in paths:
            conn.execute("INSERT INTO media (path, filename, has_audio) VALUES (?,?,1)",
                         (str(p), os.path.basename(str(p))))


def test_a_batch_is_refused_when_nothing_on_this_machine_is_readable(
        fastapi_client, tmp_path, monkeypatch):
    """The 2026-08-25 shape: four hours of work, nothing errors, nineteen clips
    come back empty and keep their wrong timecodes for three days. Whatever the
    caller sees, it must not be "queued: 222".

    A working fake ffmpeg is installed on purpose: without one, a machine that has
    no ffmpeg refuses for THAT reason instead, and the test would be pinning
    whichever cause the host happened to have. CI has no ffmpeg — this test needs
    to exercise the missing-files path in both.
    """
    fake, _ = _fake_ffmpeg(tmp_path, HEALTHY, name="ffmpeg-present")
    monkeypatch.setattr(mp.config, "FFMPEG_PATH", fake)
    _seed([tmp_path / "A7V" / "gone{0}.MP4".format(i) for i in range(3)])

    r = fastapi_client.post("/api/retranscribe-all", json={})

    assert r.status_code == 422, r.text
    assert "讀不了" in r.text and "missing" in r.text


def test_a_batch_still_runs_when_only_some_media_is_unusable(
        fastapi_client, tmp_path, monkeypatch):
    """One dead codec among working ones still leaves real work to do. A gate that
    halts the job over a title card is a gate people learn to route around."""
    good = tmp_path / "A7V"
    good.mkdir(parents=True)
    paths = []
    for i in range(3):
        f = good / "clip{0}.MP4".format(i)
        f.write_bytes(b"\x00")
        paths.append(f)
    paths += [tmp_path / "Missing" / "x{0}.mov".format(i) for i in range(2)]
    _seed(paths)
    fake, _ = _fake_ffmpeg(tmp_path, HEALTHY)
    monkeypatch.setattr(mp.config, "FFMPEG_PATH", fake)

    r = fastapi_client.post("/api/retranscribe-all", json={})

    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 5


def test_a_refused_batch_leaves_no_lock_behind(fastapi_client, tmp_path, monkeypatch):
    """The preflight sits before the single-flight guard and the ingest slot on
    purpose. If it bailed after taking them, one bad environment would wedge every
    later run with a 409 that has nothing to do with the real problem.

    Proven by a batch that WOULD succeed, not by repeating the failing one: with a
    leaked guard the second call still fails the preflight first and returns the
    same 422, so repeating it proves nothing. The first version of this test did
    exactly that and survived the mutation.
    """
    _seed([tmp_path / "A7V" / "gone{0}.MP4".format(i) for i in range(3)])
    assert fastapi_client.post("/api/retranscribe-all", json={}).status_code == 422

    good = tmp_path / "GoodCam"
    good.mkdir()
    ok_paths = []
    for i in range(3):
        f = good / "clip{0}.MP4".format(i)
        f.write_bytes(b"\x00")
        ok_paths.append(f)
    _seed(ok_paths)
    fake, _ = _fake_ffmpeg(tmp_path, HEALTHY, name="ffmpeg-ok")
    monkeypatch.setattr(mp.config, "FFMPEG_PATH", fake)

    second = fastapi_client.post("/api/retranscribe-all", json={})

    assert second.status_code == 200, (
        "a refused preflight held the single-flight guard: " + second.text)


CORRUPT = """\
[mov,mp4,m4a @ 0x7f8] moov atom not found
zero.MP4: Invalid data found when processing input"""


def test_a_file_ffmpeg_cannot_open_is_not_a_silent_clip(tmp_path, clip):
    """A zero-byte or truncated file and a title card both produce "no Audio:
    line". Treating them alike lets a drive full of broken files sail through the
    preflight as "footage with nobody talking". ffmpeg separates them: it lists
    the streams it found for real media, and lists nothing for what it cannot
    parse."""
    fake, _ = _fake_ffmpeg(tmp_path, CORRUPT, name="ffmpeg-corrupt")

    r = mp.probe_one(clip, ffmpeg=fake)

    assert r["verdict"] == mp.NOT_MEDIA
    assert mp.NOT_MEDIA in mp.BLOCKING


def test_a_missing_ffmpeg_is_reported_as_itself_not_as_bad_footage(tmp_path):
    """One fact about the machine, not twelve about the files. And it is the same
    silence: without ffmpeg `_to_wav` returns None, `transcribe()` returns the
    empty contract, the H1 guard declines to blank the row, and the batch reports
    success having done nothing."""
    res = mp.probe(["a.mov", "b.mov"], ffmpeg=str(tmp_path / "no-such-ffmpeg"))

    assert res["files"] == []
    assert "ffmpeg will not run" in mp.blocking(res)[0]
    assert "produce nothing" in mp.format_report(res)
