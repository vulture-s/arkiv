"""Tests for the DIT Offload UI endpoints (server.py) — /api/offload/preview,
/api/offload, and the /dit page. The functional 'working UI' for the DIT engine.
"""


def test_offload_preview_mirror(fastapi_client, tmp_path):
    # source is a dedicated subdir so the fixture's test.db (in tmp_path root) isn't
    # seen by _collect_sources (which copies *everything*, not just media).
    card = tmp_path / "card"; (card / "DCIM").mkdir(parents=True)
    (card / "DCIM" / "C0001.MP4").write_bytes(b"x" * 100)
    (card / "DCIM" / "C0002.MP4").write_bytes(b"y" * 50)
    r = fastapi_client.post("/api/offload/preview", json={"src": str(card)})
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 2
    assert d["organize"] is None
    rels = {f["rel"] for f in d["files"]}
    assert rels == {"DCIM/C0001.MP4", "DCIM/C0002.MP4"}


def test_offload_preview_organize(fastapi_client, tmp_path, monkeypatch):
    import offload
    monkeypatch.setattr(offload, "_probe_camera_meta_batch",
                        lambda paths: {str(p): {"date": "2026-03-09", "camera": "Sony FX30", "reel": "A001"} for p in paths})
    card = tmp_path / "card"; card.mkdir()
    (card / "C0001.MP4").write_bytes(b"x")
    r = fastapi_client.post("/api/offload/preview",
                            json={"src": str(card), "organize": "{date}/{camera}/{reel}"})
    assert r.status_code == 200
    assert r.json()["files"][0]["rel"] == "2026-03-09/Sony FX30/A001/C0001.MP4"


def test_offload_preview_missing_src_400(fastapi_client, tmp_path):
    r = fastapi_client.post("/api/offload/preview", json={"src": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_offload_preview_bad_template_400(fastapi_client, tmp_path):
    (tmp_path / "C0001.MP4").write_bytes(b"x")
    r = fastapi_client.post("/api/offload/preview",
                            json={"src": str(tmp_path), "organize": "no-tokens"})
    assert r.status_code == 400


def test_offload_run_missing_src_400(fastapi_client, tmp_path):
    r = fastapi_client.post("/api/offload", json={"src": str(tmp_path / "nope"), "dst": ["/x"]})
    assert r.status_code == 400


def test_offload_run_empty_dst_400(fastapi_client, tmp_path):
    (tmp_path / "C0001.MP4").write_bytes(b"x")
    r = fastapi_client.post("/api/offload", json={"src": str(tmp_path), "dst": []})
    assert r.status_code == 400


def test_dit_redirects_to_spa_offload(fastapi_client):
    # Svelte cutover Phase 3: the standalone /dit island was ported into the SPA;
    # the old path now 308-redirects to the SPA offload route.
    r = fastapi_client.get("/dit", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/#/offload"


def test_offload_preview_limit_clamped(fastapi_client, tmp_path):
    # Codex: client-controlled limit must be clamped (0 / negative / huge → cap 200),
    # never disabling the cap. 3 files; valid limit slices, limit=0 falls back to cap.
    card = tmp_path / "card"; card.mkdir()
    for i in range(3):
        (card / f"C{i}.MP4").write_bytes(b"x")
    assert fastapi_client.post("/api/offload/preview",
                               json={"src": str(card), "limit": 2}).json()["count"] == 2
    # limit=0 must NOT mean "no slicing / 0 files" — it falls back to the 200 cap
    assert fastapi_client.post("/api/offload/preview",
                               json={"src": str(card), "limit": 0}).json()["count"] == 3
    # an absurd value is capped, not honored verbatim (still returns the 3 real files)
    assert fastapi_client.post("/api/offload/preview",
                               json={"src": str(card), "limit": 99999}).json()["count"] == 3


def test_offload_run_streams_progress(fastapi_client, tmp_path):
    import json
    card = tmp_path / "card"; card.mkdir()
    (card / "A.MP4").write_bytes(b"aaa")
    (card / "B.MP4").write_bytes(b"bbb")
    dst = tmp_path / "dst"; dst.mkdir()
    r = fastapi_client.post("/api/offload", json={"src": str(card), "dst": [str(dst)]})
    assert r.status_code == 200
    events = [json.loads(l) for l in r.text.splitlines() if l.strip().startswith("{")]
    types = [e.get("type") for e in events]
    assert "dst_start" in types
    assert types.count("file") >= 2              # one progress event per copied file
    done = [e for e in events if e.get("type") == "done"][-1]
    assert done["code"] == 0                      # terminal event carries the exit code
    # the offload really copied the files (not just emitted events)
    assert (dst / "A.MP4").read_bytes() == b"aaa"
    assert (dst / "B.MP4").read_bytes() == b"bbb"


def test_offload_run_stream_synthesizes_done_on_early_exit(fastapi_client, tmp_path):
    # offload exits (code 4) on a bad --organize template BEFORE emitting its own
    # done event; the stream must still end with a synthetic done so the UI's
    # done-handler fires instead of hanging (Codex).
    import json
    card = tmp_path / "card"; card.mkdir()
    (card / "A.MP4").write_bytes(b"a")
    dst = tmp_path / "dst"; dst.mkdir()
    r = fastapi_client.post("/api/offload",
                            json={"src": str(card), "dst": [str(dst)], "organize": "no-tokens"})
    assert r.status_code == 200  # validation passed → stream started
    events = [json.loads(l) for l in r.text.splitlines() if l.strip().startswith("{")]
    done = [e for e in events if e.get("type") == "done"]
    assert done, "stream must always emit a terminal done event"
    assert done[-1]["code"] != 0  # carries the offload's non-zero exit
