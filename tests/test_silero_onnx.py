"""silero_onnx must cut speech exactly where the torch silero path did.

transcribe._vad_filter used `silero_vad` (torch JIT) until torch was dropped from
the bundle; silero_onnx runs the same model on onnxruntime. The reference below
was produced by that torch path (see `_source` in the JSON), so these tests are
the equivalence claim in CI form — not a snapshot of whatever silero_onnx does
today. A 50-clip / 54-minute comparison over real library audio (identical
stamps on all 50) is recorded in the PR that introduced silero_onnx.

`glass_half` is a bundled CC-BY sample clip (sample/LICENSES.md) and the only one
of the four with speech in it.
"""
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnxruntime")
import silero_onnx  # noqa: E402

FIX = Path(__file__).parent / "vad_reference"  # not tests/fixtures/: that is gitignored for personal media
REF = json.loads((FIX / "glass_half_torch_reference.json").read_text())


def _audio():
    raw = (FIX / "glass_half_16k.s16le").read_bytes()
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


def test_stamps_match_the_torch_reference():
    stamps = silero_onnx.get_speech_timestamps(_audio(), silero_onnx.SileroVAD(), **REF["params"])
    assert stamps == REF["stamps"]


def test_probabilities_match_the_torch_reference():
    probs = silero_onnx.speech_probs(_audio(), silero_onnx.SileroVAD())
    assert len(probs) == len(REF["probs"])
    assert np.max(np.abs(np.array(probs) - np.array(REF["probs"]))) < 1e-4


def test_a_reused_model_is_reset_between_files():
    """`_vad_filter` keeps one model for the whole ingest; state from the previous
    clip must not leak into the next one's first windows."""
    model = silero_onnx.SileroVAD()
    silero_onnx.speech_probs(np.random.RandomState(0).uniform(-1, 1, 48000).astype(np.float32), model)
    assert silero_onnx.speech_probs(_audio(), model) == silero_onnx.speech_probs(_audio(), silero_onnx.SileroVAD())


def test_silence_has_no_speech():
    model = silero_onnx.SileroVAD()
    assert silero_onnx.get_speech_timestamps(np.zeros(32000, dtype=np.float32), model, threshold=0.5) == []


def test_only_16k_is_accepted():
    with pytest.raises(ValueError):
        silero_onnx.get_speech_timestamps(np.zeros(8000, dtype=np.float32), silero_onnx.SileroVAD(), sampling_rate=8000)


def test_the_vendored_model_is_the_one_the_reference_came_from():
    import hashlib
    digest = hashlib.sha256(silero_onnx.MODEL_PATH.read_bytes()).hexdigest()
    assert digest == "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


def test_transcribe_does_not_import_torch():
    """The whole point: nothing on the transcription path may pull torch back in."""
    src = (Path(__file__).resolve().parent.parent / "transcribe.py").read_text()
    for banned in ("import torch", "from torch", "from silero_vad", "import silero_vad"):
        assert banned not in src, banned
