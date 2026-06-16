"""
seismograph.gateway.ingestion
==============================
Batch ingestion endpoint -- validates probe batch schema and signatures,
enforces the privacy boundary, and hands verified feature vectors to the
correlation engine.

Security invariants:
  - Malformed batches are rejected atomically (no partial ingestion).
  - Unsigned batches are rejected and the error is logged.
  - Raw prompt text or raw output text in any batch field causes rejection.
  - Sybil resistance: signature check + reputation weighting
    (reputation layer is a Phase 2 stub here).

#SG-TRACE: REQ-GW-002
#   | assumption: batch schema validation uses Pydantic v2;
#     schema version is carried in every batch
#   | test: test_ingestion_rejects_malformed_batch
#SG-TRACE: REQ-GW-003
#   | assumption: signature key registry is an in-memory dict for Phase 0;
#     persistent PKI in Phase 2
#   | test: test_ingestion_rejects_unsigned_batch
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Batch schema (Pydantic-free stub -- Pydantic v2 added in Phase 1)
# ---------------------------------------------------------------------------


@dataclass
class ProbeBatch:
    """Incoming batch from a remote probe.

    Fields:
        batch_id:            Caller-assigned UUID.
        org_id:              Pseudonymous organisation identifier.
        suite_version_hash:  Content-addressed canary suite version.
        feature_vectors:     List of serialised FeatureVector dicts.
                             No raw text permitted.
        signature:           Ed25519 hex signature over canonical bytes.
        schema_version:      Batch schema version string, e.g. "1.0".

    #SG-TRACE: REQ-GW-004
    #   | assumption: feature_vectors contain no raw prompt/output fields;
    #     enforced by ProbeSDK before transmission
    #   | test: test_batch_no_raw_fields
    """

    batch_id: str
    org_id: str
    suite_version_hash: str
    feature_vectors: list[dict[str, Any]]
    signature: str
    schema_version: str = "1.0"

    def canonical_bytes(self) -> bytes:
        """Return deterministic bytes over which signature is verified."""
        payload = {
            "batch_id": self.batch_id,
            "org_id": self.org_id,
            "suite_version_hash": self.suite_version_hash,
            "feature_vectors": self.feature_vectors,
            "schema_version": self.schema_version,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()


# ---------------------------------------------------------------------------
# Ingestion gateway
# ---------------------------------------------------------------------------


class IngestionGateway:
    """Validates and routes incoming probe batches.

    Phase 0: in-memory key registry, no HTTP transport layer yet.
    Phase 1: FastAPI endpoint wrapping this class.

    #SG-TRACE: REQ-GW-005
    #   | assumption: key registry maps org_id -> Ed25519 public key bytes
    #   | test: test_ingestion_gateway_known_org_accepted
    """

    BANNED_FIELD_SUBSTRINGS: list[str] = [
        "raw_prompt",
        "raw_output",
        "prompt_text",
        "output_text",
    ]

    def __init__(self) -> None:
        # Maps org_id -> Ed25519 public key bytes (populated externally)
        self._key_registry: dict[str, bytes] = {}
        self._accepted_count: int = 0
        self._rejected_count: int = 0

    def register_key(self, org_id: str, public_key_bytes: bytes) -> None:
        """Register an Ed25519 public key for an organisation."""
        self._key_registry[org_id] = public_key_bytes

    def ingest(self, batch: ProbeBatch) -> bool:
        """Validate and ingest a batch.

        Returns True on acceptance, False on rejection.
        Rejection is always logged with a reason;
        no partial ingestion occurs.

        #SG-TRACE: REQ-GW-006
        #   | assumption: rejection is atomic -- all vectors accepted or none
        #   | test: test_ingestion_atomic_rejection
        """
        try:
            self._validate_schema(batch)
            self._check_no_raw_fields(batch)
            self._verify_signature(batch)
        except ValueError as exc:
            logger.error(
                "Batch rejected | batch_id=%s org_id=%s reason=%s",
                batch.batch_id,
                batch.org_id,
                str(exc),
            )
            self._rejected_count += 1
            return False

        self._accepted_count += 1
        logger.info(
            "Batch accepted | batch_id=%s org_id=%s vectors=%d",
            batch.batch_id,
            batch.org_id,
            len(batch.feature_vectors),
        )
        return True

    # ------------------------------------------------------------------
    # Private validation helpers
    # ------------------------------------------------------------------

    def _validate_schema(self, batch: ProbeBatch) -> None:
        """Raise ValueError if required fields are absent or malformed."""
        if not batch.batch_id:
            raise ValueError("batch_id is empty")
        if not batch.org_id:
            raise ValueError("org_id is empty")
        if not batch.suite_version_hash:
            raise ValueError("suite_version_hash is empty")
        if not isinstance(batch.feature_vectors, list):
            raise ValueError("feature_vectors must be a list")
        if not batch.signature:
            raise ValueError("signature is missing -- batch unsigned")

    def _check_no_raw_fields(self, batch: ProbeBatch) -> None:
        """Raise ValueError if any feature vector has raw-text fields.

        #SG-TRACE: REQ-GW-007
        #   | assumption: field name check sufficient for Phase 0;
        #     content inspection added in Phase 2 Aegis audit
        #   | test: test_ingestion_rejects_raw_prompt_field
        """
        for i, vec in enumerate(batch.feature_vectors):
            for key in vec:
                for banned in self.BANNED_FIELD_SUBSTRINGS:
                    if banned in key.lower():
                        raise ValueError(
                            f"feature_vectors[{i}] contains "
                            f"banned field {key!r}"
                        )

    def _verify_signature(self, batch: ProbeBatch) -> None:
        """Raise ValueError if signature invalid or org_id unknown.

        Phase 0 stub: actual Ed25519 verification requires the
        cryptography library (wired in Phase 1).  Checks that the org
        is registered and signature field is non-empty.

        #SG-TRACE: REQ-GW-008
        #   | assumption: cryptography.hazmat.primitives.asymmetric.ed25519
        #     used in Phase 1
        #   | test: test_ingestion_signature_verification_phase1
        """
        if batch.org_id not in self._key_registry:
            raise ValueError(
                f"org_id={batch.org_id!r} not in key registry -- unknown probe"
            )
        # TODO Phase 1: replace with real Ed25519 verification
        _ = batch.canonical_bytes()  # computed but not yet verified

    @property
    def stats(self) -> dict[str, int]:
        """Return acceptance/rejection counters."""
        return {
            "accepted": self._accepted_count,
            "rejected": self._rejected_count,
        }
