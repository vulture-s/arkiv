"""Core's licence verifier: RFC 8032 correctness, the trusted key, the contract."""

import os

import pytest

import pro_licence as pl
from tests.licence_signing import make_record, public_key, sign

RFC_PUB = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
RFC_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
    "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)


def test_rfc8032_vector_1_verifies():
    assert pl.ed25519_verify(RFC_PUB, b"", RFC_SIG)
    assert not pl.ed25519_verify(RFC_PUB, b"x", RFC_SIG)


def test_agrees_with_cryptography():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization

    for n in range(8):
        seed, msg = os.urandom(32), os.urandom(n * 13)
        key = ed.Ed25519PrivateKey.from_private_bytes(seed)
        pub = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        assert public_key(seed) == pub
        assert sign(msg, seed=seed) == key.sign(msg)
        assert pl.ed25519_verify(pub, msg, key.sign(msg))


@pytest.mark.parametrize("i", [0, 31, 32, 63])
def test_any_flipped_signature_bit_fails(i):
    bad = bytearray(RFC_SIG)
    bad[i] ^= 1
    assert not pl.ed25519_verify(RFC_PUB, b"", bytes(bad))


@pytest.mark.parametrize(
    "pub,sig",
    [(b"", b""), (RFC_PUB[:31], RFC_SIG), ("x", RFC_SIG), (b"\xff" * 32, RFC_SIG)],
)
def test_malformed_input_is_false_not_an_exception(pub, sig):
    assert pl.ed25519_verify(pub, b"", sig) is False


def test_every_trusted_key_is_a_valid_point():
    assert pl.PUBLIC_KEYS
    for kid, pub_hex in pl.PUBLIC_KEYS.items():
        assert pl._decompress(bytes.fromhex(pub_hex)) is not None, kid


def test_a_record_forged_under_the_real_kid_is_rejected():
    record = make_record(kid="2026-10")  # signed with the TEST seed
    assert pl.check(record) == (False, "signature does not match")


def test_signing_bytes_are_the_cross_repo_contract():
    """Pinned byte-for-byte: the closed arkiv_pro repo signs these exact bytes.

    If this changes, every licence already issued stops verifying.
    """
    record = {
        "format": "arkiv-pro-licence/1", "kid": "k", "key": "PRO-001",
        "licensee": "pen", "issued": "2026-09-01", "issuer": "立凡科技有限公司",
        "kind": "vip", "sig": "ignored",
    }
    expected = (
        '{"format":"arkiv-pro-licence/1","issued":"2026-09-01",'
        '"issuer":"立凡科技有限公司","key":"PRO-001","kid":"k",'
        '"kind":"vip","licensee":"pen"}'
    )
    assert pl.signing_bytes(record) == b"arkiv-pro-licence/1\n" + expected.encode("utf-8")


def test_editing_any_field_breaks_the_signature():
    keys = {"test-only": public_key().hex()}
    for field, value in [("licensee", "x"), ("key", "PRO-002"), ("kind", "vip"),
                         ("issued", "2027-01-01"), ("issuer", "x")]:
        record = make_record()
        record[field] = value
        assert not pl.check(record, keys, frozenset())[0], field
    record = make_record()
    record["seats"] = "99"
    assert not pl.check(record, keys, frozenset())[0]
