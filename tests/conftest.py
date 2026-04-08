import importlib
import json
import sys
import types

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
    sys.modules.setdefault("torch", fake_torch)

    fake_silero = types.ModuleType("silero_vad")
    fake_silero.get_speech_timestamps = lambda *args, **kwargs: []
    fake_silero.load_silero_vad = lambda *args, **kwargs: object()
    sys.modules.setdefault("silero_vad", fake_silero)

    fake_soundfile = types.ModuleType("soundfile")
    fake_soundfile.read = lambda *args, **kwargs: ([], 16000)
    fake_soundfile.write = lambda *args, **kwargs: None
    sys.modules.setdefault("soundfile", fake_soundfile)

    fake_mlx = types.ModuleType("mlx_whisper")
    fake_mlx.transcribe = lambda *args, **kwargs: {
        "text": "",
        "segments": [],
        "language": kwargs.get("language", "zh"),
    }
    sys.modules.setdefault("mlx_whisper", fake_mlx)

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
def tmp_db(tmp_path, monkeypatch):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    return db_path


@pytest.fixture
def sample_record():
    state = {"value": 0}

    def _make(**overrides):
        state["value"] += 1
        idx = state["value"]
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
                [{"keywords": "人物 訪談 場景{0}".format(idx)}],
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
    with TestClient(server_module.app) as client:
        yield client
