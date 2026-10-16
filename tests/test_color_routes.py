"""Color-profile API: list / assign / batch-assign / import / serve .cube."""
import importlib

import pytest

cp = importlib.import_module("color_profiles")
db = importlib.import_module("db")


@pytest.fixture
def user_luts(tmp_path, monkeypatch):
    d = tmp_path / "luts"
    monkeypatch.setattr(cp, "USER_LUT_DIR", d)
    monkeypatch.setattr(cp, "_CACHE_DIR", tmp_path / "cache")
    return d


@pytest.fixture
def no_regrade(monkeypatch):
    ingest = importlib.import_module("ingest")
    calls = []
    monkeypatch.setattr(ingest, "regrade_thumbnail", lambda mid: calls.append(mid) or None)
    return calls


def test_list_profiles_includes_builtin_and_user(fastapi_client, user_luts):
    user_luts.mkdir()
    (user_luts / "DLogM.cube").write_text(cp.generate_cube("dji-dlog", 2), encoding="utf-8")
    body = fastapi_client.get("/api/color-profiles").json()
    ids = {p["id"]: p["source"] for p in body["profiles"]}
    assert ids["sony-slog3-sgamut3cine"] == "builtin" and ids["user:DLogM"] == "user"
    assert body["user_lut_dir"] == str(user_luts)


def test_set_profile_persists_and_regrades(fastapi_client, sample_record, user_luts, no_regrade):
    db.upsert(sample_record())
    r = fastapi_client.patch("/api/media/1/color-profile", json={"color_profile": "nikon-nlog"})
    assert r.status_code == 200 and r.json()["color_profile"] == "nikon-nlog"
    assert db.get_record_by_id(1)["color_profile"] == "nikon-nlog"
    assert no_regrade == [1]
    assert fastapi_client.get("/api/media/1").json()["color_profile"] == "nikon-nlog"


def test_set_profile_none_and_clear(fastapi_client, sample_record, user_luts, no_regrade):
    db.upsert(sample_record())
    assert fastapi_client.patch("/api/media/1/color-profile", json={"color_profile": "none"}).status_code == 200
    assert db.get_record_by_id(1)["color_profile"] == "none"
    assert fastapi_client.patch("/api/media/1/color-profile", json={"color_profile": ""}).status_code == 200
    assert db.get_record_by_id(1)["color_profile"] is None


def test_unknown_profile_rejected(fastapi_client, sample_record, user_luts, no_regrade):
    db.upsert(sample_record())
    r = fastapi_client.patch("/api/media/1/color-profile", json={"color_profile": "made-up"})
    assert r.status_code == 422 and no_regrade == []
    assert fastapi_client.patch("/api/media/999/color-profile",
                                json={"color_profile": "nikon-nlog"}).status_code == 404


def test_batch_assign_is_not_shadowed_by_media_id_route(fastapi_client, sample_record, user_luts, no_regrade):
    db.upsert(sample_record()); db.upsert(sample_record())
    r = fastapi_client.post("/api/media/color-profile",
                            json={"ids": [1, 2, 99], "color_profile": "sony-slog3-sgamut3cine"})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == [1, 2] and r.json()["missing"] == [99]
    assert {db.get_record_by_id(i)["color_profile"] for i in (1, 2)} == {"sony-slog3-sgamut3cine"}
    assert sorted(no_regrade) == [1, 2]


def test_import_valid_cube_then_serve_it(fastapi_client, user_luts):
    cube = cp.generate_cube("panasonic-vlog", 3)
    r = fastapi_client.post("/api/color-profiles/import",
                            files={"file": ("DJI D-Log M → 709.cube", cube.encode(), "text/plain")})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert pid.startswith("user:") and "/" not in pid
    served = fastapi_client.get("/api/color-profiles/{0}/cube".format(pid))
    assert served.status_code == 200 and "LUT_3D_SIZE 3" in served.text


@pytest.mark.parametrize("payload, msg", [
    (b"LUT_1D_SIZE 4\n0 0 0\n", "1D"),
    (b"LUT_3D_SIZE 2\n0 0 0\n", "資料列數"),
    (b"hello", "資料列格式錯誤"),
    (b"0 0 0\n", "LUT_3D_SIZE"),
    (b"\xff\xfe\x00bad", ""),
])
def test_import_rejects_bad_files(fastapi_client, user_luts, payload, msg):
    r = fastapi_client.post("/api/color-profiles/import", files={"file": ("bad.cube", payload, "text/plain")})
    assert r.status_code == 422 and msg in r.json()["detail"]
    assert not user_luts.exists() or not list(user_luts.glob("*.cube"))


def test_import_name_cannot_escape_lut_dir(fastapi_client, user_luts):
    r = fastapi_client.post("/api/color-profiles/import",
                            files={"file": ("../../evil.cube", cp.generate_cube("apple-log", 2).encode(), "text/plain")})
    assert r.status_code == 200
    assert [f.name for f in user_luts.iterdir()] == ["evil.cube"]


def test_builtin_cube_served(fastapi_client, user_luts):
    r = fastapi_client.get("/api/color-profiles/sony-slog3-sgamut3cine/cube")
    assert r.status_code == 200 and "LUT_3D_SIZE 33" in r.text
    assert fastapi_client.get("/api/color-profiles/nope/cube").status_code == 404
