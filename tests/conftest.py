import importlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _install_fake_modules():
    try:
        import numpy  # noqa: F401
    except Exception:
        fake_numpy = types.ModuleType("numpy")
        fake_numpy_typing = types.ModuleType("numpy.typing")
        fake_numpy_typing.NDArray = object
        sys.modules.setdefault("numpy", fake_numpy)
        sys.modules.setdefault("numpy.typing", fake_numpy_typing)

    fake_torch = types.ModuleType("torch")

    class _Cuda(object):
        @staticmethod
        def is_available():
            return False

    fake_torch.cuda = _Cuda()
    # `_vad_filter` does `torch.from_numpy(audio)` purely to hand silero a tensor;
    # it never calls a tensor method itself. Identity keeps the numpy array flowing
    # through, which is what the energy-VAD fixture below wants to slice. Without
    # this the function cannot be executed at all — see the note on soundfile.
    fake_torch.from_numpy = lambda a: a
    sys.modules.setdefault("torch", fake_torch)

    fake_silero = types.ModuleType("silero_vad")
    fake_silero.get_speech_timestamps = lambda *args, **kwargs: []
    fake_silero.load_silero_vad = lambda *args, **kwargs: object()
    sys.modules.setdefault("silero_vad", fake_silero)

    # soundfile is a real dependency (requirements.txt), but CI does NOT install it
    # — ci.yml hand-picks its packages and soundfile is not on the list — so the fake
    # is load-bearing there and cannot simply be deleted.
    #
    # It used to be inert: read() returned ([], 16000) and write() dropped its input
    # on the floor. Combined with torch having no from_numpy, that made
    # `transcribe._vad_filter` *structurally unreachable* in the suite: it could not
    # be executed even by a test that wanted to. That is why a timestamp bug in it
    # went unnoticed — not because nobody wrote the test, but because nobody could.
    #
    # Backed by stdlib `wave` (mono 16-bit PCM ↔ float32), which is all the
    # transcription path ever asks of it: _to_wav already produces mono 16 kHz.
    fake_soundfile = types.ModuleType("soundfile")

    def _sf_read(path, dtype="float32"):
        import numpy as _np
        import wave as _wave

        with _wave.open(str(path), "rb") as w:
            sr = w.getframerate()
            raw = w.readframes(w.getnframes())
        data = _np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
        return data, sr

    def _sf_write(path, data, samplerate, *args, **kwargs):
        import numpy as _np
        import wave as _wave

        arr = _np.asarray(data, dtype="float32")
        pcm = _np.clip(arr, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        with _wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(samplerate))
            w.writeframes(pcm.tobytes())

    fake_soundfile.read = _sf_read
    fake_soundfile.write = _sf_write
    sys.modules.setdefault("soundfile", fake_soundfile)

    # Production mlx_whisper.transcribe returns {text, language, segments=[
    #   {start, end, text, no_speech_prob, avg_logprob, compression_ratio, ...}
    # ]}. Default stub yields one realistic-shape segment so a future test that
    # imports transcribe.transcribe() doesn't silently flow through an empty list
    # (audit Codex Round-2 Scope C nit). Tests that need empty behaviour can still
    # monkeypatch this lambda.
    fake_mlx = types.ModuleType("mlx_whisper")
    fake_mlx.transcribe = lambda *args, **kwargs: {
        "text": "fake stub segment",
        "language": kwargs.get("language", "zh") or "zh",
        "segments": [{
            "start": 0.0, "end": 1.0,
            "text": "fake stub segment",
            "no_speech_prob": 0.1,
            "avg_logprob": -0.3,
            "compression_ratio": 1.2,
        }],
    }
    sys.modules.setdefault("mlx_whisper", fake_mlx)

    # WhisperX (CUDA path). Production: whisperx.load_model() → has .transcribe();
    # whisperx.load_audio() → np-like; whisperx.load_align_model() / whisperx.align().
    # Stub keeps the call surface so transcribe._transcribe_whisperx can run end-to-end
    # without CUDA / cuDNN. Tests for postprocess shape still drive _postprocess
    # directly via monkeypatch.
    fake_whisperx = types.ModuleType("whisperx")

    class _FakeWhisperXModel(object):
        def transcribe(self, audio, **kwargs):
            return {
                "language": kwargs.get("language", "zh") or "zh",
                "segments": [{
                    "start": 0.0, "end": 1.0,
                    "text": "fake whisperx segment",
                    "no_speech_prob": 0.1,
                    "avg_logprob": -0.3,
                    "compression_ratio": 1.2,
                }],
            }

    fake_whisperx.load_model = lambda *args, **kwargs: _FakeWhisperXModel()
    fake_whisperx.load_audio = lambda *args, **kwargs: object()
    fake_whisperx.load_align_model = lambda *args, **kwargs: (object(), {"language": "zh"})
    fake_whisperx.align = lambda segments, *args, **kwargs: {"segments": segments}
    sys.modules.setdefault("whisperx", fake_whisperx)

    fake_fw = types.ModuleType("faster_whisper")

    class FakeWhisperModel(object):
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return [], types.SimpleNamespace(language=kwargs.get("language", "zh"))

    fake_fw.WhisperModel = FakeWhisperModel
    sys.modules.setdefault("faster_whisper", fake_fw)

    fake_chromadb = types.ModuleType("chromadb")

    class FakePersistentClient(object):
        def __init__(self, *args, **kwargs):
            pass

        def delete_collection(self, *args, **kwargs):
            return None

        def get_or_create_collection(self, *args, **kwargs):
            return object()

    fake_chromadb.PersistentClient = FakePersistentClient
    sys.modules.setdefault("chromadb", fake_chromadb)


_install_fake_modules()


@pytest.fixture
def synth_wav(tmp_path):
    """Build a mono 16 kHz wav from a (kind, seconds) script, and say where the speech is.

    Returns `make(script) -> (path, samples, sample_rate, speech_windows)` where
    `speech_windows` is the ground truth in ORIGINAL-media seconds — the thing a
    transcript timestamp is supposed to agree with.

        path, audio, sr, truth = make([("silence", 3), ("tone", 1), ("silence", 4)])
        # truth == [(3.0, 4.0)]
    """
    import numpy as np
    import soundfile as sf

    def make(script, sample_rate=16000, freq=220.0):
        parts, windows, cursor = [], [], 0.0
        for kind, seconds in script:
            n = int(round(seconds * sample_rate))
            if kind == "tone":
                t = np.arange(n, dtype="float32") / sample_rate
                parts.append((0.6 * np.sin(2 * np.pi * freq * t)).astype("float32"))
                windows.append((cursor, cursor + seconds))
            else:
                parts.append(np.zeros(n, dtype="float32"))
            cursor += seconds
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype="float32")
        path = tmp_path / "synth-{0}.wav".format(len(list(tmp_path.iterdir())))
        sf.write(str(path), audio, sample_rate)
        return str(path), audio, sample_rate, windows

    return make


@pytest.fixture
def energy_vad(monkeypatch):
    """Replace Silero with a deterministic energy gate, in the module that uses it.

    `transcribe.py:14` does `from silero_vad import get_speech_timestamps`, so the
    name is bound into `transcribe` — patch it there, not on the fake module.

    Why not the real Silero: `load_silero_vad()` downloads a model (three runners,
    three chances to flake), the torch wheel CI deliberately avoids is ~2 GB, and
    real stamps move with the model version — the test would then assert against a
    moving target instead of against our own arithmetic. Our arithmetic is what broke.
    """
    import numpy as np

    def _fake(tensor, model, sampling_rate=16000, min_silence_duration_ms=300,
              speech_pad_ms=0, threshold=0.5, **kwargs):
        audio = np.asarray(tensor, dtype="float32")
        frame = max(1, int(sampling_rate * 0.03))
        loud = [
            (i, min(i + frame, len(audio)))
            for i in range(0, len(audio), frame)
            if len(audio[i:i + frame]) and float(np.max(np.abs(audio[i:i + frame]))) > 0.1
        ]
        stamps = []
        for start, end in loud:
            if stamps and start - stamps[-1]["end"] <= int(sampling_rate * min_silence_duration_ms / 1000.0):
                stamps[-1]["end"] = end
            else:
                stamps.append({"start": start, "end": end})
        pad = int(sampling_rate * speech_pad_ms / 1000.0)
        if pad:
            for s in stamps:
                s["start"] = max(0, s["start"] - pad)
                s["end"] = min(len(audio), s["end"] + pad)
        return stamps

    transcribe = importlib.import_module("transcribe")
    monkeypatch.setattr(transcribe, "get_speech_timestamps", _fake)
    return _fake


@pytest.fixture(autouse=True)
def _isolate_install_meta(tmp_path, monkeypatch):
    """Keep the grandfather latch out of the developer's real home directory.

    `entitlements._record_grandfather_latch` WRITES `~/.arkiv/install-meta.json`
    the first time an install is observed to predate the cap, and more than ten
    test modules reach that code path through `projects.add_project`,
    `/api/search/all`, and the bins helpers. Without this fixture the suite
    would leave a real latch on the maintainer's machine and then read it back
    on the next run — every free-tier assertion silently inverting, green for
    the wrong reason locally and red only in CI.

    Autouse, unlike `pro_entitled` below, precisely because it does not switch
    any gate off: it points at a path that does not exist, so the latch is
    simply absent and every test still exercises the live scan it was written
    for. A test that wants a latch writes one at this path itself.
    """
    monkeypatch.setenv(
        "ARKIV_INSTALL_META", str(tmp_path / "install-state" / "install-meta.json")
    )


@pytest.fixture
def pro_entitled(tmp_path, monkeypatch):
    """Run a test as an installation that owns the Pro add-on.

    For tests whose SUBJECT is a cross-project feature — federated search path
    sanitisation, bin dedup/copy across projects — rather than the licence gate
    itself. Post-1.1.0 those features need entitlement, so without this the test
    would be asserting on a 403 and quietly stop covering the thing it was
    written for.

    Deliberately opt-in per test rather than autouse. An autouse version would
    switch the gate off for the whole suite, and the next regression that
    wrongly opened a Pro feature to the free tier would go green — the exact
    failure mode this fixture exists to avoid creating. `tests/
    test_entitlements.py` does NOT use it and keeps asserting the refusals.
    """
    licence = tmp_path / "pro-license.json"
    licence.write_text(
        json.dumps({"licensee": "test suite", "key": "TEST-PRO"}), encoding="utf-8"
    )
    monkeypatch.setenv("ARKIV_PRO_LICENSE", str(licence))
    return licence


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    db_path = tmp_path / "test.db"
    # R5-23 (#54): db.get_db_path() follows config.DB_PATH when no --db override is
    # set, so monkeypatching config.DB_PATH alone points the whole stack at the tmp
    # db (the old separate `db.DB_PATH` value copy is gone).
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db()
    return db_path


@pytest.fixture
def sample_record():
    state = {"value": 0}

    def _make(**overrides):
        state["value"] += 1
        idx = state["value"]
        # Production frame_tags shape (vision.py PROMPT — see vision.py:15-27):
        # list of dicts each carrying description / tags / content_type /
        # focus_score / exposure / stability / audio_quality / atmosphere /
        # energy / edit_position / edit_reason. Pre-Codex-Round-2-C the fixture
        # used legacy `keywords` shape, which let the C2 frame_tags-as-text bug
        # slip past unit tests. Now it mirrors what vision pipeline writes.
        base = {
            "path": "/tmp/media_{0}.mp4".format(idx),
            "filename": "媒體_{0}.mp4".format(idx),
            "ext": ".mp4",
            "duration_s": 30.0 + idx,
            "size_mb": 10.0 + idx,
            "width": 1920,
            "height": 1080,
            "fps": 29.97,
            "has_audio": 1,
            "transcript": "這是第{0}段中文逐字稿，用來驗證 UTF-8 與查詢行為。".format(idx),
            "lang": "zh",
            "frame_tags": json.dumps(
                [{
                    "description": "場景{0} 描述：人物訪談畫面。".format(idx),
                    "tags": ["人物", "訪談", "場景{0}".format(idx)],
                    "content_type": "Talking-Head",
                    "focus_score": 5,
                    "exposure": "normal",
                    "stability": "穩定",
                    "audio_quality": "清晰",
                    "atmosphere": "正式",
                    "energy": "中",
                    "edit_position": "中段",
                    "edit_reason": "fixture sample {0}".format(idx),
                }],
                ensure_ascii=False,
            ),
            "thumbnail_path": "/tmp/thumb_{0}.jpg".format(idx),
            "processed_at": "2026-04-09T0{0}:00:00".format(idx),
        }
        base.update(overrides)
        return base

    return _make


@pytest.fixture
def server_module(tmp_db):
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    return importlib.reload(server)


@pytest.fixture
def fastapi_client(server_module):
    import auth
    import admin

    token = admin.create_token(name="pytest-admin", scopes=sorted(auth.SCOPES))
    headers = {"Authorization": "Bearer {0}".format(token["raw_token"])}
    original_export_roots = os.environ.get("ARKIV_EXPORT_ROOTS")
    os.environ["ARKIV_EXPORT_ROOTS"] = str(Path(tempfile.gettempdir()).resolve())
    try:
        with TestClient(server_module.app, headers=headers) as client:
            yield client
    finally:
        if original_export_roots is None:
            os.environ.pop("ARKIV_EXPORT_ROOTS", None)
        else:
            os.environ["ARKIV_EXPORT_ROOTS"] = original_export_roots
