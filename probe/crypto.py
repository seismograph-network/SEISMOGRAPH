"""
seismograph.probe.crypto
========================
Ed25519 cryptographic identity and payload signing for the
SEISMOGRAPH probe SDK.

Responsibilities
----------------
1. KeyManager: load or generate an Ed25519 keypair, persist the
   raw private key bytes to a local file (.seismograph_id by
   default).  The key file must NEVER be committed to version
   control -- add .seismograph_id to .gitignore.

2. canonical_json: deterministic UTF-8 serialisation of a dict
   for signing / verification.  Keys are sorted, separators are
   compact (',', ':').  This MUST match byte-for-byte what the
   gateway verifies against.

3. sign_payload: sign a payload dict with an Ed25519 private key;
   return the hex-encoded signature string.

Privacy note
------------
The private key file contains raw 32 bytes -- no password, no
wrapping.  The probe operator is responsible for file-system-level
access control (chmod 600 is applied automatically on generation).

#SG-TRACE: REQ-PRIV-002
#   | assumption: one keypair per probe installation; key file path
#     is configurable for multi-tenant and test scenarios
#   | test: test_key_manager_generates_and_persists_key
#SG-TRACE: REQ-AUTH-002
#   | assumption: deterministic JSON serialisation is the canonical
#     signing surface; both probe and gateway use canonical_json()
#   | test: test_canonical_json_is_deterministic
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

logger = logging.getLogger(__name__)

# Default key file location (relative to the process working directory).
# Add .seismograph_id to .gitignore -- never commit the private key.
DEFAULT_KEY_PATH: Path = Path(".seismograph_id")


# ---------------------------------------------------------------------------
# Canonical serialisation
# ---------------------------------------------------------------------------


def canonical_json(payload_dict: dict) -> bytes:
    """Deterministically serialise *payload_dict* to UTF-8 JSON bytes.

    Keys are sorted recursively; separators are compact (no spaces).
    The output is stable across Python versions and dict insertion
    orders.

    This function is the SOLE serialisation path used by both the
    probe (signing) and the gateway (verification).  Any divergence
    between the two sides will cause every signature to fail.

    Args:
        payload_dict: Arbitrary JSON-serialisable mapping.

    Returns:
        UTF-8-encoded bytes of the canonical JSON representation.

    #SG-TRACE: REQ-AUTH-002
    #   | assumption: json.dumps sort_keys=True is stable across CPython
    #     versions for the value types used (str, float, int, dict, list)
    #   | test: test_canonical_json_is_deterministic
    """
    return json.dumps(
        payload_dict, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# KeyManager
# ---------------------------------------------------------------------------


class KeyManager:
    """Manages the Ed25519 keypair for one probe installation.

    On construction, loads the private key from *key_path* if the file
    exists and contains exactly 32 bytes of raw Ed25519 key material.
    If the file does not exist, a new keypair is generated, the raw
    private key bytes are written to *key_path* (mode 0o600), and a
    log message records the event.

    Args:
        key_path: Path to the raw private key file.  Defaults to
            DEFAULT_KEY_PATH (.seismograph_id in the CWD).

    Attributes:
        key_path: Resolved path to the private key file.

    #SG-TRACE: REQ-PRIV-002
    #   | assumption: raw 32-byte Ed25519 key file; no encryption wrapper
    #   | test: test_key_manager_generates_and_persists_key
    """

    def __init__(self, key_path: Path = DEFAULT_KEY_PATH) -> None:
        self.key_path: Path = Path(key_path)
        self._private_key: Ed25519PrivateKey = self._load_or_generate()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_or_generate(self) -> Ed25519PrivateKey:
        """Load key from file or generate + persist a new one."""
        if self.key_path.exists():
            raw = self.key_path.read_bytes()
            logger.debug(
                "KeyManager: loaded existing key from %s", self.key_path
            )
            return Ed25519PrivateKey.from_private_bytes(raw)

        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        self.key_path.write_bytes(raw)
        try:
            self.key_path.chmod(0o600)
        except OSError:  # Windows does not support POSIX chmod
            pass
        logger.info(
            "KeyManager: generated new Ed25519 keypair -> %s",
            self.key_path,
        )
        return private_key

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def private_key(self) -> Ed25519PrivateKey:
        """The Ed25519 private key instance."""
        return self._private_key

    @property
    def public_key_hex(self) -> str:
        """Hex-encoded raw Ed25519 public key (64 hex chars = 32 bytes)."""
        pub = self._private_key.public_key()
        return pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


# ---------------------------------------------------------------------------
# Signing helper
# ---------------------------------------------------------------------------


def sign_payload(payload_dict: dict, private_key: Ed25519PrivateKey) -> str:
    """Sign *payload_dict* with *private_key* and return a hex signature.

    Internally calls canonical_json() to produce the signing surface,
    ensuring the signature is over the same bytes that the gateway will
    reconstruct for verification.

    Args:
        payload_dict: The batch payload (e.g. SignalBatch.to_dict()).
        private_key:  The probe's Ed25519PrivateKey instance.

    Returns:
        Hex-encoded Ed25519 signature string (128 hex chars = 64 bytes).

    #SG-TRACE: REQ-AUTH-002
    #   | assumption: canonical_json() output matches what the gateway
    #     receives as the raw HTTP request body
    #   | test: test_sign_verify_round_trip
    """
    signed_bytes = canonical_json(payload_dict)
    return private_key.sign(signed_bytes).hex()
