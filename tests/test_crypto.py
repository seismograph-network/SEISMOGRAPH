"""
tests.test_crypto
=================
Unit tests for probe.crypto: canonical_json(), KeyManager, sign_payload().

Coverage contract
-----------------
TC-1  canonical_json is deterministic regardless of dict insertion order.
TC-2  sign / verify round-trip succeeds with the same key.
TC-3  Verification of a tampered payload fails (returns False).
TC-4  KeyManager generates a key file on first init; loads same key on
      second init; public key is stable across reloads.

Adversarial case (Sybil / tampering)
-------------------------------------
TC-3 is the primary adversarial test: a payload signed with key K and
then mutated before sending MUST fail gateway verification.

#SG-TRACE: REQ-AUTH-002
#   | test: TC-1, TC-2, TC-3
#SG-TRACE: REQ-PRIV-002
#   | test: TC-4 (KeyManager file I/O)
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from probe.crypto import KeyManager, canonical_json, sign_payload

# ---------------------------------------------------------------------------
# TC-1: canonical_json determinism
# ---------------------------------------------------------------------------


def test_canonical_json_is_deterministic():
    """TC-1: same dict in different insertion orders yields identical bytes."""
    d1 = {"z": 1, "a": 2, "m": 3}
    d2 = {"a": 2, "m": 3, "z": 1}
    d3 = {"m": 3, "z": 1, "a": 2}
    assert canonical_json(d1) == canonical_json(d2) == canonical_json(d3)


def test_canonical_json_compact_separators():
    """TC-1b: output uses compact separators (no spaces)."""
    b = canonical_json({"k": "v"})
    assert b == b'{"k":"v"}'
    assert b" " not in b


def test_canonical_json_nested():
    """TC-1c: nested dicts are also key-sorted."""
    d = {"b": {"y": 2, "x": 1}, "a": 0}
    result = canonical_json(d)
    # "a" before "b"; within "b", "x" before "y"
    assert result == b'{"a":0,"b":{"x":1,"y":2}}'


# ---------------------------------------------------------------------------
# TC-2: sign / verify round-trip
# ---------------------------------------------------------------------------


def test_sign_verify_round_trip():
    """TC-2: a payload signed with a key verifies successfully."""
    private_key = Ed25519PrivateKey.generate()
    payload = {
        "batch_id": "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb",
        "model_tuple": "test/model@v1",
        "metrics": {"json_success_rate": 0.95},
    }

    sig_hex = sign_payload(payload, private_key)
    assert isinstance(sig_hex, str)
    assert len(sig_hex) == 128  # 64 bytes -> 128 hex chars

    # Verify using the public key
    pub = private_key.public_key()
    canonical_bytes = canonical_json(payload)
    # Should NOT raise
    pub.verify(bytes.fromhex(sig_hex), canonical_bytes)


def test_sign_payload_returns_hex_string():
    """TC-2b: sign_payload return type and length contract."""
    key = Ed25519PrivateKey.generate()
    sig = sign_payload({"x": 1}, key)
    assert isinstance(sig, str)
    assert len(sig) == 128
    # valid hex
    bytes.fromhex(sig)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# TC-3: tampered payload fails verification
# ---------------------------------------------------------------------------


def test_tampered_payload_fails_verify():
    """TC-3 (adversarial): altering any field after signing must fail."""
    private_key = Ed25519PrivateKey.generate()
    original = {
        "batch_id": "12345678-1234-5678-1234-567812345678",
        "metrics": {"json_success_rate": 0.95},
        "result_count": 10,
    }
    sig_hex = sign_payload(original, private_key)

    # Tamper: change a metric value
    tampered = dict(original)
    tampered["metrics"] = {"json_success_rate": 0.10}
    tampered_bytes = canonical_json(tampered)

    pub = private_key.public_key()
    raised = False
    try:
        pub.verify(bytes.fromhex(sig_hex), tampered_bytes)
    except InvalidSignature:
        raised = True
    assert raised, "Tampered payload must NOT verify successfully"


def test_different_key_fails_verify():
    """TC-3b: signature from key A does not verify under key B."""
    key_a = Ed25519PrivateKey.generate()
    key_b = Ed25519PrivateKey.generate()
    payload = {"x": 42}

    sig_hex = sign_payload(payload, key_a)
    canonical_bytes = canonical_json(payload)

    pub_b = key_b.public_key()
    raised = False
    try:
        pub_b.verify(bytes.fromhex(sig_hex), canonical_bytes)
    except InvalidSignature:
        raised = True
    assert raised, "Signature from key A must not verify under key B"


# ---------------------------------------------------------------------------
# TC-4: KeyManager file I/O
# ---------------------------------------------------------------------------


def test_key_manager_generates_and_persists_key(tmp_path):
    """TC-4: first init creates key file; second init loads same key."""
    key_file = tmp_path / ".seismograph_id"
    assert not key_file.exists()

    km1 = KeyManager(key_path=key_file)
    assert key_file.exists()
    pub_hex_1 = km1.public_key_hex

    # Reload from the same file -- must be the same key
    km2 = KeyManager(key_path=key_file)
    pub_hex_2 = km2.public_key_hex

    assert pub_hex_1 == pub_hex_2, (
        "Public key must be stable across KeyManager reloads"
    )


def test_key_manager_public_key_hex_length(tmp_path):
    """TC-4b: public_key_hex is exactly 64 hex chars (32 raw bytes)."""
    km = KeyManager(key_path=tmp_path / ".seismograph_id")
    pub_hex = km.public_key_hex
    assert isinstance(pub_hex, str)
    assert len(pub_hex) == 64
    bytes.fromhex(pub_hex)  # valid hex


def test_key_manager_private_key_type(tmp_path):
    """TC-4c: private_key property returns an Ed25519PrivateKey instance."""
    km = KeyManager(key_path=tmp_path / ".seismograph_id")
    assert isinstance(km.private_key, Ed25519PrivateKey)


def test_key_manager_sign_with_loaded_key(tmp_path):
    """TC-4d: key loaded from file can sign; public key verifies."""
    key_file = tmp_path / ".seismograph_id"
    km = KeyManager(key_path=key_file)

    payload = {"msg": "hello seismograph"}
    sig_hex = sign_payload(payload, km.private_key)
    canonical_bytes = canonical_json(payload)

    # Reconstruct public key from hex and verify
    pub = Ed25519PrivateKey.from_private_bytes(
        key_file.read_bytes()
    ).public_key()
    pub.verify(bytes.fromhex(sig_hex), canonical_bytes)  # must not raise
