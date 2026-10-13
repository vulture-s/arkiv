"""`/api/stream` refuses any codec a browser cannot decode, not just two of them.

Issue #420. The gate compared against `PROXY_CODECS` — `{hevc, prores, …}` — which
is the list of formats the proxy BUILDER knows how to transcode, not the list a
browser can show. Anything outside it (mjpeg, qtrle, dnxhd, cinepak, rawvideo…)
was handed over raw: black pane, no error, no way to ask for a proxy. The same
failure the `REMUX_CONTAINERS` comment already describes for containers — the
codec half of it was never closed.

The fix is an ALLOW-list, because the set of codecs browsers play is short and
slow-moving while the set they refuse is unbounded.

🔴 Two traps, both of which turn a fix into a regression:

  a JPEG still probes as `mjpeg`  → 409 would break every image preview
  an MP3 probes as `mp3`          → 409 would break every audio preview

Gating on VIDEO_EXT covers both. Gating on "not an image" — the obvious guard,
and the one the issue proposes — covers only the first, and the audio half is
easy to miss because images are the case people remember.
"""
from __future__ import annotations

import importlib

import pytest

import codec
import pathres


# ── the helper on its own ────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["h264", "avc1", "vp9", "av1", "av01", "H264", " h264 "])
def test_playable_codecs(name):
    assert codec.is_browser_playable_video(name) is True


@pytest.mark.parametrize("name", ["mjpeg", "qtrle", "dnxhd", "cinepak", "rawvideo",
                                  "hevc", "prores", "theora", "mpeg4"])
def test_unplayable_codecs(name):
    assert codec.is_browser_playable_video(name) is False


@pytest.mark.parametrize("name", ["theora", "mpeg4"])
def test_the_two_that_were_wrong(name):
    """Measured in a real Chrome 152, 2026-09-05:

        canPlayType('video/ogg; codecs="theora"')    → ""
        canPlayType('video/mp4; codecs="mp4v.20.8"') → ""

    Both shipped in the allow-list on the strength of what browsers used to do.
    Theora decoding was dropped from Chrome; MPEG-4 Part 2 (DivX/Xvid) was never
    in it. A wrong 'playable' is the worse direction of error — it hands raw
    bytes to a player that shows a black pane and no error, which is the exact
    symptom this gate was built to stop.

    Kept separate from the list above so the reason survives: if a future Chrome
    brings either back, this is the test whose docstring says what to re-measure
    rather than a name silently deleted from a parametrize list."""
    assert name not in codec.BROWSER_PLAYABLE_VIDEO
    assert codec.is_browser_playable_video(name) is False


@pytest.mark.parametrize("name", [None, "", "   "])
def test_unknown_is_none_not_false(name):
    """None means the probe failed, and that must not become a 409 telling the
    user to build a proxy for a file we could not read."""
    assert codec.is_browser_playable_video(name) is None


def test_proxy_codecs_are_a_subset_of_the_unplayable_ones():
    """Every codec we build a proxy FOR must be one a browser cannot show, or we
    would be transcoding for no reason."""
    for c in codec.PROXY_CODECS:
        assert codec.is_browser_playable_video(c) is False, c


# ── the other direction, which is where the user got stranded ────────────────
@pytest.mark.parametrize("bad", ["mjpeg", "qtrle", "dnxhd", "cinepak",
                                 "rawvideo", "theora", "mpeg4"])
def test_everything_the_endpoint_refuses_is_something_the_builder_will_build(
        bad, monkeypatch):
    """A 409 is an instruction. The half that carries it out has to agree.

    The subset test above only says we never transcode without reason. It says
    nothing about the reverse, and the reverse is the one with a user on the end
    of it: `/api/stream` answers 409 "build a proxy" from the allow-list, while
    the decision in `build_proxies()` used to test membership of `PROXY_CODECS`
    — two formats. So a dnxhd clip was refused by the endpoint and then skipped
    by the builder, and **the 409 never cleared**. The user is told to do one
    thing, does it, and nothing happens — which reads as the button being broken.

    `build_proxies()`'s own docstring already stated this pairing ("if the two
    halves disagree the user is told to do something that then silently does
    nothing"); #428 closed the container half of it and left the codec half open.

    The builder was never the constraint: `_build_proxy_cmd` is
    `ffmpeg -i … -c:v libx264`, which re-encodes anything ffmpeg can decode.
    """
    assert codec.is_browser_playable_video(bad) is False, "precondition"
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: bad)
    assert codec.needs_proxy("/tmp/x.mov") == codec.NEEDED, bad


@pytest.mark.parametrize("good", ["h264", "avc1", "vp8", "vp9", "av1", "av01"])
def test_a_codec_the_browser_plays_is_still_not_transcoded(good, monkeypatch):
    """The paired invariant must not collapse into "build a proxy for everything"
    — that would burn hours of CPU and a second copy of every library."""
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: good)
    assert codec.needs_proxy("/tmp/x.mp4") == codec.NOT_NEEDED, good


def test_a_codec_we_cannot_judge_is_unknown_not_needed(monkeypatch):
    """`None` from the allow-list means "the probe told us nothing", and the
    endpoint falls through rather than refusing. The builder must match that:
    transcoding on an unreadable answer would hand every junk codec column a
    proxy it never needed."""
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: "   ")
    assert codec.needs_proxy("/tmp/x.mov") == codec.UNKNOWN


# ── through the endpoint ─────────────────────────────────────────────────────
@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "cam" / "A001.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00" * 16)
    return src


def _seed(db, sample_record, src, **over):
    rec = dict(path=str(src), filename=src.name, ext=src.suffix,
               duration_s=12.0, fps=30.0, has_audio=1)
    rec.update(over)
    db.upsert(sample_record(**rec))
    return 1


@pytest.mark.parametrize("bad", ["mjpeg", "qtrle", "dnxhd"])
def test_a_browser_incompatible_video_gets_409(
    fastapi_client, server_module, sample_record, clip, monkeypatch, bad
):
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec=bad)
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 409
    body = r.json()
    assert body["need_proxy"] is True
    assert bad in body["reason"], "the reason should name the codec that failed"


def test_hevc_keeps_its_original_wording(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """The pre-existing HEVC/ProRes message is what the UI already renders; a
    wider gate must not quietly reword the case that already worked."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="hevc")
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 409
    assert "HEVC/ProRes" in r.json()["reason"]


def test_h264_still_streams(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="h264")
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)

    assert fastapi_client.get("/api/stream/%d" % mid).status_code == 200


def test_an_unprobeable_video_falls_through_instead_of_409(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """ffprobe missing / NAS unreachable keeps the old behaviour: hand over the
    bytes and let the browser decide."""
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec=None)
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: None)

    assert fastapi_client.get("/api/stream/%d" % mid).status_code == 200


def test_a_codec_we_cannot_judge_falls_through(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """The `None` branch, reached directly.

    The test above exercises the outer `and stored_codec` guard instead: with no
    codec at all the gate is skipped before the verdict is consulted, so
    `playable is None` is never evaluated. (A mutation flipping `is False` to
    `is not True` survived the whole suite until this existed — an equivalent
    mutant only because the branch was unreachable as written, which is worth
    knowing rather than assuming.)
    """
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec="some-future-codec")
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)
    monkeypatch.setattr(codec, "is_browser_playable_video", lambda c: None)

    assert fastapi_client.get("/api/stream/%d" % mid).status_code == 200


# ── 🔴 the two regressions this gate could cause ─────────────────────────────
def test_a_jpeg_still_is_not_refused_for_being_mjpeg(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """A JPEG's codec IS `mjpeg`. Asking "can a browser play this video codec"
    about a photograph and acting on the answer breaks every image preview."""
    db = importlib.import_module("db")
    img = tmp_path / "stills" / "shot.jpg"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    mid = _seed(db, sample_record, img, codec="mjpeg", duration_s=0.04,
                fps=25.0, has_audio=0)
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 0.04)

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 200, "image preview must not be gated on video codecs"


def test_an_audio_file_is_not_refused_for_not_being_video(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """🔴 The half that gating on "not an image" would miss. An MP3 is not an
    image and `mp3` is not a browser-playable VIDEO codec, so that guard hands
    it a 409 and audio preview dies."""
    db = importlib.import_module("db")
    snd = tmp_path / "audio" / "take.mp3"
    snd.parent.mkdir(parents=True, exist_ok=True)
    snd.write_bytes(b"ID3" + b"\x00" * 32)
    mid = _seed(db, sample_record, snd, codec="mp3", duration_s=30.0,
                fps=0.0, has_audio=1)
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 30.0)

    r = fastapi_client.get("/api/stream/%d" % mid)

    assert r.status_code == 200, "audio preview must not be gated on video codecs"


# ── and the builder half, end to end ─────────────────────────────────────────
@pytest.mark.parametrize("bad", ["mjpeg", "qtrle", "dnxhd"])
def test_the_builder_builds_the_proxy_the_endpoint_just_demanded(
    fastapi_client, server_module, sample_record, clip, monkeypatch, bad
):
    """Same clip, both halves, in one test — because the bug lives between them.

    `test_a_browser_incompatible_video_gets_409` above proves the refusal, and
    each half passed its own tests while the pair was broken: the endpoint said
    409, `build_proxies()` tested `PROXY_CODECS` and skipped the file, and the
    user's next click did nothing at all.

    The batch builder decides from the STORED codec, never re-probing, so it
    needs its own assertion — the tri-state `needs_proxy()` path does not cover
    this branch.
    """
    db = importlib.import_module("db")
    mid = _seed(db, sample_record, clip, codec=bad)
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)

    assert fastapi_client.get("/api/stream/%d" % mid).status_code == 409

    ingest = importlib.import_module("ingest")
    built = []
    fake_out = clip.parent / "proxy.mp4"
    fake_out.write_bytes(b"\x00" * 8)  # the reporter stats it for the size delta
    monkeypatch.setattr(ingest, "generate_proxy",
                        lambda media_id, path, **k: built.append(path) or str(fake_out))

    ingest.build_proxies()

    assert built == [str(clip)], "the 409 was issued and never cleared"


def test_the_builder_still_skips_what_the_endpoint_streams(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """The pairing runs both ways: no 409, no proxy. Without this the fix could
    be "build a proxy for everything", which passes every test above."""
    db = importlib.import_module("db")
    _seed(db, sample_record, clip, codec="h264")
    ingest = importlib.import_module("ingest")

    built = []
    monkeypatch.setattr(ingest, "generate_proxy",
                        lambda media_id, path, **k: built.append(path) or "/fake/p.mp4")

    ingest.build_proxies()

    assert built == []


def test_a_row_whose_stored_codec_says_nothing_is_left_alone(
    fastapi_client, server_module, sample_record, clip, monkeypatch
):
    """A blank-ish codec column must not be read as "the browser cannot play it".

    `is_browser_playable_video` answers None there, and `not None` is True — so a
    guard written the obvious way would hand a proxy to every legacy row with a
    junk codec value, on a library where nothing is wrong.
    """
    db = importlib.import_module("db")
    _seed(db, sample_record, clip, codec="   ")
    ingest = importlib.import_module("ingest")

    built = []
    monkeypatch.setattr(ingest, "generate_proxy",
                        lambda media_id, path, **k: built.append(path) or "/fake/p.mp4")

    ingest.build_proxies()

    assert built == []
