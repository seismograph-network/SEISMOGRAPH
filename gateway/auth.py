"""
seismograph.gateway.auth
========================
Ed25519 signature verification for inbound probe batches.

Every batch arriving at the ingestion gateway must carry:
  x-signature  -- hex-encoded Ed25519 signature (64 bytes / 128 hex chars)
  x-public-key -- hex-encoded raw Ed25519 public key (32 bytes / 64 hex chars)

The signature must be over the exact bytes received as the HTTP request
body.  The probe SDK sends the body as canonical JSON
(json.dumps(sort_keys=True, separators=(",", ":")).encode("utf-8")) --
the same canonical_json() helper used here for the round-trip test.

verify_signature() is intentionally pure: it takes bytes + two hex
strings and returns True/False.  All FastAPI plumbing (extracting
request.body(), reading headers) lives in gateway/main.py.

Security contract
-----------------
- Empty/missing signature or public-key strings return False immediately.
- InvalidSignature, ValueError (bad hex, wrong key length), and any
  other exception are caught and return False.  No exception leaks.
- This module does NOT implement reputation scoring or Sybil resistance
  (P2-002 gate).  A signed batch from a brand-new public key is accepted;
  the key is logged but not checked against a known-good list yet.

#SG-TRACE: REQ-AUTH-002
#   | assumption: probe sends body as canonical_json bytes; gateway
#     reads raw body via await request.body() before Pydantic parsing
#   | test: test_signed_request_returns_202 (T12)
#SG-TRACE: REQ-PRIV-002 (partial)
#   | assumption: full Sybil resistance (reputation weighting) deferred
#     to P2-002; this module enforces cryptographic authenticity only
#   | test: test_tampered_payload_rejected (T13)
"""

from __future__ import annotations

import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)

logger = logging.getLogger(__name__)


def verify_signature(
    payload_bytes: bytes,
    signature_hex: str,
    public_key_hex: str,
) -> bool:
    """Verify an Ed25519 signature over *payload_bytes*.

    Parameters
    ----------
    payload_bytes:
        The raw HTTP request body bytes (canonical JSON from probe).
    signature_hex:
        Hex-encoded Ed25519 signature string (128 hex chars = 64 bytes).
    public_key_hex:
        Hex-encoded raw Ed25519 public key (64 hex chars = 32 bytes).

    Returns
    -------
    bool
        True if the signature is valid, False for any failure including
        missing inputs, bad hex, wrong key length, or tampered payload.

    #SG-TRACE: REQ-AUTH-002
    #   | assumption: empty signature strings are rejected immediately;
    #     no fallback to "accept-all" mode post-Phase-0
    #   | test: test_missing_signature_returns_401 (T4)
    """
    if not signature_hex or not public_key_hex:
        logger.warning(
            "verify_signature: missing signature or public key -- rejected"
        )
        return False

    try:
        pub_bytes = bytes.fromhex(public_key_hex)
        pub_key: Ed25519PublicKey = Ed25519PublicKey.from_public_bytes(
            pub_bytes
        )
        sig_bytes = bytes.fromhex(signature_hex)
        pub_key.verify(sig_bytes, payload_bytes)
        return True

    except InvalidSignature:
        logger.warning(
            "verify_signature: signature mismatch -- rejected (key=%s...)",
            public_key_hex[:16],
        )
        return False

    except (ValueError, Exception) as exc:  # noqa: BLE001
        logger.warning("verify_signature: validation error -- %s", exc)
        return False
