"""Offload reports how fast each file actually moved (#537).

A destination that is slow for reasons outside arkiv (an SMB session that landed
on Wi-Fi: 6 MiB/s measured) used to show only "file N/N" for an hour per big
file. The per-file event and the summary now carry MiB/s.
"""
import importlib
import json

offload = importlib.import_module("offload")


def _events(capsys):
    return [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip().startswith("{")]


def test_file_events_and_summary_carry_throughput(tmp_path, capsys, monkeypatch):
    src = tmp_path / "card"
    (src / "A001").mkdir(parents=True)
    (src / "A001" / "a.mp4").write_bytes(b"x" * (3 << 20))
    (src / "A001" / "b.mp4").write_bytes(b"y" * (1 << 20))
    dst = tmp_path / "nas"
    dst.mkdir()
    monkeypatch.chdir(tmp_path)
    _code, summary, _state = offload.run_offload(str(src), [str(dst)], progress="json", emit_mhl=False)
    ev = _events(capsys)
    files = [e for e in ev if e["type"] == "file"]
    assert len(files) == 2
    for e in files:
        assert e["status"] == "verified"
        assert isinstance(e["copy_mib_s"], (int, float)) and e["copy_mib_s"] > 0
        assert isinstance(e["verify_mib_s"], (int, float)) and e["verify_mib_s"] > 0
    s = next(iter(summary.values()))  # main() prints this as the terminal "done" event
    assert s["copy_mib_s"] and s["copy_mib_s"] > 0


def test_failed_file_event_has_no_throughput(tmp_path, capsys, monkeypatch):
    src = tmp_path / "card"
    src.mkdir()
    (src / "a.mp4").write_bytes(b"x" * 1024)
    dst = tmp_path / "nas"
    dst.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(offload, "_copy_single_file", lambda *a, **k: False)
    offload.run_offload(str(src), [str(dst)], progress="json", emit_mhl=False)
    f = [e for e in _events(capsys) if e["type"] == "file"][0]
    assert f["status"] == "failed" and "copy_mib_s" not in f
