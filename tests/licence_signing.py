"""Issue Pro licences for tests, under a test-only key.

Core only verifies (`pro_licence`); signing lives with the licensor's tool in the
closed `arkiv_pro` repository. Tests need records that verify, so this module
carries the signing half of RFC 8032 and a fixed TEST seed -- never the
licensor's key. `trust_test_key()` adds the test kid to the keys core accepts
for the duration of one test.
"""

import base64
import hashlib
import json

import pro_licence as pl

TEST_SEED = bytes(range(32))
TEST_KID = "test-only"


def _expand(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _compress(p):
    zinv = pl._inv(p[2])
    x = p[0] * zinv % pl._P
    y = p[1] * zinv % pl._P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def public_key(seed=TEST_SEED):
    a, _ = _expand(seed)
    return _compress(pl._mul(a, pl._G))


def sign(message, seed=TEST_SEED):
    a, prefix = _expand(seed)
    pub = public_key(seed)
    r = pl._hash_mod_l(prefix + message)
    r_enc = _compress(pl._mul(r, pl._G))
    s = (r + pl._hash_mod_l(r_enc + pub + message) * a) % pl._L
    return r_enc + int.to_bytes(s, 32, "little")


def make_record(**over):
    record = {
        "format": pl.FORMAT,
        "kid": TEST_KID,
        "key": "TEST-001",
        "licensee": "test suite",
        "issued": "2026-10-08",
        "issuer": "立凡科技有限公司",
        "kind": "purchase",
    }
    record.update(over)
    record["sig"] = base64.b64encode(sign(pl.signing_bytes(record))).decode("ascii")
    return record


def trust_test_key(monkeypatch):
    keys = dict(pl.PUBLIC_KEYS)
    keys[TEST_KID] = public_key().hex()
    monkeypatch.setattr(pl, "PUBLIC_KEYS", keys)


def write_licence(path, monkeypatch, **over):
    """Write a signed test licence at `path`, point core at it, trust its key."""
    trust_test_key(monkeypatch)
    path.write_text(json.dumps(make_record(**over), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("ARKIV_PRO_LICENSE", str(path))
    return path
