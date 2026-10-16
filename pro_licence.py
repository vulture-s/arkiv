"""Signed Pro licence records: verification, offline.

A Pro licence is one flat JSON object of strings, signed with Ed25519 by the
licensor's key:

    {"format": "arkiv-pro-licence/1", "kid": "2026-10", "key": "PRO-001",
     "licensee": "<name the buyer chose>", "issued": "YYYY-MM-DD",
     "issuer": "立凡科技有限公司", "kind": "purchase", "sig": "<base64>"}

The signature covers every field except `sig` (see `signing_bytes`). Records are
issued with the licensor's tool in the closed `arkiv_pro` repository; this
module only verifies, and it must stay byte-for-byte compatible with that
repository's `arkiv_pro/licence.py`.

Why verification lives in core (decision 2026-10-08): the features a licence
unlocks (unlimited projects, cross-project aggregation) stay in core because
installations from before 1.1.0 were promised them permanently, without
installing anything. A buyer therefore needs only the licence file — no add-on
— and verifying it here costs nothing in secrecy: the public key and the
verifier are public by nature. What a forger lacks is the private key.

No expiry, no network, no activation server. Ed25519 is implemented in pure
Python (RFC 8032) so a packaged app's interpreter and a source checkout both
verify without `cryptography`; tests pin it to the RFC vectors and, where
`cryptography` is installed, to that library.
"""

import base64
import hashlib
import json

FORMAT = "arkiv-pro-licence/1"
REQUIRED = ("format", "kid", "key", "licensee", "issued", "issuer", "kind")
_DOMAIN = b"arkiv-pro-licence/1\n"

# Public keys trusted to sign licences, by kid. Rotation ADDS a key: removing
# one would invalidate perpetual licences already issued under it.
PUBLIC_KEYS = {
    "2026-10": "6cac02fdb7eeb4504d3fc4e4acf3d3d8085a593f99103ca241a1dcf5aca237d5",
}

# Licence ids withdrawn by the licensor (e.g. a refunded purchase). No
# phone-home: a withdrawal takes effect with the next release.
REVOKED = frozenset()


# ── Ed25519 (RFC 8032) ────────────────────────────────────────────────────────

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x):
    return pow(x, _P - 2, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _hash_mod_l(data):
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _L


def ed25519_verify(public, message, signature):
    """True only for a valid signature; never raises on malformed input."""
    if not isinstance(public, bytes) or not isinstance(signature, bytes):
        return False
    if len(public) != 32 or len(signature) != 64:
        return False
    a_point = _decompress(public)
    if a_point is None:
        return False
    r_enc = signature[:32]
    r_point = _decompress(r_enc)
    if r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = _hash_mod_l(r_enc + public + message)
    left = _mul(s, _G)
    right = _add(r_point, _mul(h, a_point))
    return (left[0] * right[2] - right[0] * left[2]) % _P == 0 and (
        left[1] * right[2] - right[1] * left[2]
    ) % _P == 0


# ── licence records ───────────────────────────────────────────────────────────

def signing_bytes(fields):
    """The exact bytes a signature covers: a domain tag + canonical JSON."""
    body = {k: v for k, v in fields.items() if k != "sig"}
    return _DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def check(record, public_keys=None, revoked=None):
    """(ok, reason). Never raises; `reason` is written to be shown to a user."""
    keys = PUBLIC_KEYS if public_keys is None else public_keys
    withdrawn = REVOKED if revoked is None else revoked
    if not isinstance(record, dict):
        return False, "not a licence record"
    if not all(isinstance(v, str) for v in record.values()):
        return False, "malformed licence record"
    missing = [k for k in REQUIRED + ("sig",) if not record.get(k, "").strip()]
    if missing:
        if "sig" in missing and record.get("licensee") and record.get("key"):
            return False, (
                "this licence file is not signed; licences issued before "
                "2026-10-08 need to be re-issued by the licensor"
            )
        return False, "licence record is missing: " + ", ".join(missing)
    if record["format"] != FORMAT:
        return False, "unsupported licence format"
    pub_hex = keys.get(record["kid"])
    if pub_hex is None:
        return False, "licence was signed with an unknown key"
    if record["key"] in withdrawn:
        return False, "licence has been withdrawn"
    try:
        sig = base64.b64decode(record["sig"], validate=True)
        pub = bytes.fromhex(pub_hex)
    except (ValueError, TypeError):
        return False, "malformed signature"
    if not ed25519_verify(pub, signing_bytes(record), sig):
        return False, "signature does not match"
    return True, "ok"
