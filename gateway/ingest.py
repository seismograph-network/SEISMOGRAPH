"""
seismograph.gateway.ingest
===========================
Mock ingest endpoint -- the Phase 0 equivalent of a FastAPI route.

`receive_batch` simulates the full request lifecycle:
  parse JSON -> validate schema -> authenticate -> accept/reject.

Phase 1 will wrap this function in a FastAPI POST /v1/ingest endpoint.
The function signature and return contract are designed to remain stable
across that transition.

Return contract
---------------
(status_code: int, body: dict)

  202  Accepted -- batch is valid, signature passed (mock), queued.
  400  Bad Request -- JSON parse failure or Pydantic validation error.
  401  Unauthorized -- signature verification failed (Phase 0: never
       fires, because verify_signature is a stub returning True).
  500  Internal -- unexpected error; always logged.

#SG-TRACE: REQ-GW-015
#   | assumption: (status_code, body) tuple is stable across Phase 0->1
#     transition; FastAPI wrapper reads this tuple to build HTTPResponse
#   | test: test_receive_batch_valid_returns_202
#SG-TRACE: REQ-GW-016
#   | assumption: JSON decode failure and Pydantic validation failure
#     both map to 400; details are included in body but never echo
#     the raw input (prevent raw-text reflection)
#   | test: test_receive_batch_invalid_json_returns_400
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from gateway.auth import verify_signature
from gateway.schema import InboundSignalBatch

logger = logging.getLogger(__name__)


def receive_batch(
    json_str: str,
    signature_hex: str = "",
    public_key_hex: str = "",
) -> tuple[int, dict]:
    """Process one inbound signal batch.

    Parameters
    ----------
    json_str:
        Raw JSON string from the probe (network body).
    signature_hex:
        Hex-encoded Ed25519 signature. Empty string is accepted in
        Phase 0 (stub auth). Phase 2 makes this mandatory.
    public_key_hex:
        Hex-encoded Ed25519 public key. Same Phase 0/2 transition note.

    Returns
    -------
    (status_code, body_dict)
        See module docstring for status code contract.

    #SG-TRACE: REQ-GW-017
    #   | assumption: raw json_str is NEVER logged or echoed in error
    #     responses -- only Pydantic field paths and error types
    #   | test: test_receive_batch_error_body_contains_no_raw_input
    """
    # Step 1: JSON parse
    try:
        raw_dict = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.warning("receive_batch: JSON parse failure: %s", exc)
        return 400, {
            "error": "json_parse_failure",
            "detail": str(exc),
        }

    # Step 2: Pydantic schema validation
    try:
        batch = InboundSignalBatch.model_validate(raw_dict)
    except ValidationError as exc:
        # Return structured Pydantic errors; never echo raw_dict
        errors = exc.errors(include_url=False)
        logger.warning(
            "receive_batch: schema validation failed | errors=%d batch_id=%s",
            len(errors),
            raw_dict.get("batch_id", "<unknown>"),
        )
        return 400, {
            "error": "schema_validation_failure",
            "detail": errors,
        }

    # Step 3: Signature verification (Phase 0 stub)
    canonical = json_str.encode("utf-8")
    if not verify_signature(canonical, signature_hex, public_key_hex):
        logger.error(
            "receive_batch: signature verification failed | "
            "batch_id=%s client_id=%s",
            batch.batch_id,
            batch.client_id,
        )
        return 401, {
            "error": "signature_verification_failed",
            "detail": "Ed25519 signature is invalid for this batch.",
        }

    # Step 4: Accept
    logger.info(
        "receive_batch: 202 Accepted | batch_id=%s client_id=%s "
        "model_tuple=%s results=%d",
        batch.batch_id,
        batch.client_id,
        batch.model_tuple,
        batch.result_count,
    )
    return 202, {
        "status": "accepted",
        "batch_id": str(batch.batch_id),
        "result_count": batch.result_count,
    }
