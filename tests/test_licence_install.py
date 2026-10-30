"""POST /api/licence + entitlements.install_licence: the buyer's way in.

The invariant that matters most is the negative one: an import that does not
verify must leave a working licence exactly where it was.
"""
import json

import entitlements
from tests.licence_signing import make_record, trust_test_key, write_licence


def _point_at(tmp_path, monkeypatch):
    path = tmp_path / "arkiv-home" / "pro-license.json"
    monkeypatch.setenv("ARKIV_PRO_LICENSE", str(path))
    trust_test_key(monkeypatch)
    return path


def test_valid_record_is_saved_and_unlocks_pro(tmp_path, monkeypatch):
    path = _point_at(tmp_path, monkeypatch)
    assert not entitlements.has_pro()
    ok, reason, info = entitlements.install_licence(make_record(licensee="pen"))
    assert ok and reason == "ok"
    assert info["licensee"] == "pen"
    assert path.exists()  # parent dir created on first install
    assert entitlements.has_pro()
    assert not (path.parent / "pro-license.json.tmp").exists()


def test_tampered_record_is_refused_and_existing_licence_kept(tmp_path, monkeypatch):
    path = write_licence(tmp_path / "pro-license.json", monkeypatch, licensee="original")
    before = path.read_bytes()
    forged = make_record(licensee="original")
    forged["licensee"] = "someone else"  # signature no longer covers this
    ok, reason, info = entitlements.install_licence(forged)
    assert not ok and info is None
    assert reason == "signature does not match"
    assert path.read_bytes() == before
    assert entitlements.has_pro()


def test_unsigned_and_non_record_inputs_are_refused(tmp_path, monkeypatch):
    path = _point_at(tmp_path, monkeypatch)
    ok, reason, _ = entitlements.install_licence({"licensee": "x", "key": "k"})
    assert not ok and "not signed" in reason
    for junk in (None, "text", [1, 2], 42):
        ok, reason, _ = entitlements.install_licence(junk)
        assert not ok and reason == "not a licence record"
    assert not path.exists()


def test_oversized_record_is_refused(tmp_path, monkeypatch):
    path = _point_at(tmp_path, monkeypatch)
    big = make_record(licensee="x" * (entitlements.LICENCE_MAX_BYTES + 1))
    ok, reason, _ = entitlements.install_licence(big)
    assert not ok and "too large" in reason
    assert not path.exists()


def test_api_installs_and_returns_entitlement_state(fastapi_client, tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    r = fastapi_client.post("/api/licence", json=make_record(licensee="buyer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pro"] is True and body["licence"]["licensee"] == "buyer"
    assert body["licence_problem"] is None


def test_api_rejects_with_reason(fastapi_client, tmp_path, monkeypatch):
    path = _point_at(tmp_path, monkeypatch)
    bad = make_record()
    bad["sig"] = bad["sig"][:-4] + "AAAA"
    r = fastapi_client.post("/api/licence", json=bad)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "licence_rejected"
    assert r.json()["detail"]["message"]
    assert not path.exists()
