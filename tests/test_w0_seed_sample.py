"""Wave-0 sample project: scripts/seed_sample.py loads the bundled CC-BY clips
so a fresh arkiv delivers search on first run. Tests the discovery + idempotency
logic with a mocked ingest (no Ollama needed)."""
import importlib.util
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("seed_sample", REPO / "scripts" / "seed_sample.py")
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)

EXPECTED = {"caminandes_llama.mp4", "coffee_run.mp4", "glass_half.mp4", "wing_it.mp4"}


def test_bundled_clips_present_and_tiny():
    clips = sorted(seed.CLIPS_DIR.glob("*.mp4"))
    assert {c.name for c in clips} == EXPECTED
    # bundled in-repo → keep them tiny (each well under 1 MB)
    for c in clips:
        assert c.stat().st_size < 1_000_000


def test_skips_when_all_indexed(monkeypatch):
    monkeypatch.setattr(seed, "_already_indexed", lambda: set(EXPECTED))
    with mock.patch.object(seed.subprocess, "run") as run:
        rc = seed.main()
    assert rc == 0
    run.assert_not_called()  # idempotent: no re-ingest


def test_ingests_missing_clips(monkeypatch):
    monkeypatch.setattr(seed, "_already_indexed", lambda: set())
    with mock.patch.object(seed.subprocess, "run") as run:
        rc = seed.main()
    assert rc == 0
    run.assert_called_once()
    cmd = run.call_args[0][0]
    assert "--files" in cmd
    assert sum(str(a).endswith(".mp4") for a in cmd) == 4  # all four passed to ingest


def test_already_indexed_is_graceful():
    # swallows any DB error → always a set (never raises into the CLI)
    assert isinstance(seed._already_indexed(), set)
