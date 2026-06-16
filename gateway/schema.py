"""
seismograph.gateway.schema
===========================
Pydantic v2 models for the SEISMOGRAPH ingestion gateway.

InboundSignalBatch
------------------
The canonical wire format accepted by POST /v1/signals.

  - ``extra="forbid"`` rejects any unknown field, hardening the privacy
    boundary against accidental raw-text leakage by probe clients.
  - ``frozen=True`` makes instances hashable and prevents mutation after
    validation, satisfying the immutability invariant required by the
    Sybil-resistance layer (signature binding).
  - Three cross-field validators catch semantic errors early, before the
    batch reaches the storage or CUSUM layers.

ModelWeatherResponse
--------------------
Read-only response model for GET /v1/weather.

WebhookRegistration
-------------------
Write model for POST /v1/webhooks.  Accepted from enterprise operators
to register a target URL for private-fleet drift notifications.

#SG-TRACE: REQ-GW-001
#   | assumption: extra="forbid" prevents raw prompt leakage via unknown
#     fields in the Pydantic model; audit on every schema change
#   | test: test_unknown_field_rejected
#SG-TRACE: REQ-GW-003
#   | assumption: window_start < window_end is a hard invariant; batches
#     with equal timestamps are rejected as zero-width windows
#   | test: test_window_order_enforced
#SG-TRACE: REQ-ENT-002
#   | assumption: WebhookRegistration is write-only (never returned in
#     responses); auth_token is never echoed back to any client
#   | test: test_register_webhook_api
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InboundSignalBatch(BaseModel):
    """Canonical wire format for one canary probe signal batch.

    Fields
    ------
    batch_id:
        UUID v4 that uniquely identifies this batch.  Assigned by the
        probe SDK at flush time.
    client_id:
        Pseudonymous UUID v4 identifying the submitting probe session.
        Rotated per Aggregator instance.  Never linked to org identity.
    window_start / window_end:
        Inclusive time bounds of the probe window.  window_start must
        be strictly before window_end (enforced by check_window_order).
    model_tuple:
        Non-empty string identifying the probed model, e.g.
        "openai/gpt-4o@2025-08".  Used as the primary grouping key.
    suite_version:
        Human-readable canary suite version, e.g. "v1.0.0".
    metrics:
        Flat dict of DP-noised aggregate metric values.  Only the
        keys listed in _ALLOWED_METRIC_KEYS are accepted; any unknown
        key raises a 422 (guards against raw-text leakage via metrics
        dict injection).
    canary_hashes:
        Mapping of prompt_id -> SHA-256 response hash.  Keys must be
        alphanumeric, hyphens, underscores, or dots (version-style
        probe IDs like "v1.0.0-logic" are valid); values must be
        exactly 64 hex chars.  Validated by check_canary_hash_format.
    result_count:
        Number of canary prompts included in this batch (>= 1).
    fleet_id:
        Optional tenant identifier for private fleet isolation.
        None (default) routes the batch through the public network
        path (AgreementScorer -> PublicDriftAlert).  A non-None value
        routes through an isolated per-fleet CUSUMDetector and
        produces only private LocalDriftAlerts -- never visible via
        the public /v1/weather endpoint.

    #SG-TRACE: REQ-GW-002
    #   | assumption: client_id is a UUID4 from Aggregator; not linked
    #     to real org identity anywhere in the public API surface
    #   | test: test_inbound_signal_batch_fields
    #SG-TRACE: REQ-PRIV-001
    #   | assumption: extra="forbid" is the primary schema-level guard
    #     against raw prompt/output leakage via unknown JSON keys
    #   | test: test_unknown_field_rejected
    #SG-TRACE: REQ-ENT-001
    #   | assumption: fleet_id=None means public path; non-None means
    #     private fleet path -- routing enforced in gateway/main.py
    #   | test: test_enterprise_fleet_id_routing
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: UUID
    client_id: UUID
    window_start: datetime
    window_end: datetime
    model_tuple: str = Field(min_length=1)
    suite_version: str = Field(min_length=1)
    metrics: dict[str, float]
    canary_hashes: dict[str, str]
    result_count: int = Field(ge=1)
    fleet_id: str | None = None

    # Metric keys accepted in the metrics dict.  Any unknown key is
    # treated as a potential raw-text leakage vector and rejected.
    _ALLOWED_METRIC_KEYS: frozenset[str] = frozenset(
        {"avg_output_length", "json_success_rate", "result_count"}
    )

    @model_validator(mode="after")
    def check_window_order(self) -> InboundSignalBatch:
        """window_start must be strictly before window_end.

        #SG-TRACE: REQ-GW-003
        #   | assumption: equal timestamps indicate a zero-width window
        #     (probe bug); rejected to prevent degenerate CUSUM updates
        #   | test: test_window_order_enforced
        """
        if self.window_start >= self.window_end:
            raise ValueError(
                "window_start must be strictly before window_end; "
                f"got start={self.window_start!r}"
                f" end={self.window_end!r}"
            )
        return self

    @model_validator(mode="after")
    def check_metrics_keys(self) -> InboundSignalBatch:
        """Reject any metric key not in the allow-list.

        Guards against raw-text leakage via unknown metric keys.
        All valid keys are listed in _ALLOWED_METRIC_KEYS.

        #SG-TRACE: REQ-PRIV-001
        #   | assumption: the allow-list is the definitive enumeration
        #     of non-sensitive aggregate metrics
        #   | test: test_unknown_metric_key_rejected
        """
        unknown = set(self.metrics) - self._ALLOWED_METRIC_KEYS
        if unknown:
            raise ValueError(
                f"metrics contains unknown keys (possible raw-text "
                f"leakage path): {unknown}"
            )
        return self

    @model_validator(mode="after")
    def check_canary_hash_format(self) -> InboundSignalBatch:
        """Validate canary_hashes key and value format.

        Keys: alphanumeric, hyphens, underscores, dots (prompt IDs
        may use version-style names like "v1.0.0-logic").
        Values: exactly 64 lowercase hex characters (SHA-256 digest).

        #SG-TRACE: REQ-PRIV-004
        #   | assumption: 64-char lowercase hex is the only permissible
        #     value format; longer strings risk embedding raw output
        #   | test: test_canary_hash_format_enforced
        """
        key_pat = re.compile(r"^[\w.\-]+$")
        val_pat = re.compile(r"^[0-9a-f]{64}$")
        for key, val in self.canary_hashes.items():
            if not key_pat.match(key):
                raise ValueError(
                    f"canary_hashes key {key!r} contains invalid "
                    "characters (expected alphanumeric, -, _, .)"
                )
            if not val_pat.match(val):
                raise ValueError(
                    f"canary_hashes[{key!r}] = {val!r} is not a "
                    "64-character lowercase hex SHA-256 digest"
                )
        return self


class ModelWeatherResponse(BaseModel):
    """Read-only drift-weather status for one model_tuple.

    Returned by GET /v1/weather.  Status is DRIFTING only when a
    PublicDriftAlert (quorum-verified) exists in the last 24h.

    Fields
    ------
    model_tuple:
        Model identifier string.
    status:
        "STABLE" or "DRIFTING".
    last_alert_timestamp:
        UTC timestamp of the most recent PublicDriftAlert, or None.
    recent_avg_output_length:
        Average DP-noised output length over the last 10 signal
        batches, or None if insufficient data.
    recent_json_success_rate:
        Average DP-noised JSON success rate over the last 10 batches,
        or None if insufficient data.

    #SG-TRACE: REQ-DASH-002
    #   | assumption: status=DRIFTING requires quorum
    #     (PublicDriftAlert); single-org LocalDriftAlert never changes
    #     public weather status
    #   | test: test_weather_returns_stable_when_no_alerts
    """

    model_config = ConfigDict(frozen=True)

    model_tuple: str
    status: str
    last_alert_timestamp: datetime | None = None
    recent_avg_output_length: float | None = None
    recent_json_success_rate: float | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.status not in ("STABLE", "DRIFTING"):
            raise ValueError(
                f"status must be STABLE or DRIFTING; got {self.status!r}"
            )


class WebhookRegistration(BaseModel):
    """Write model for POST /v1/webhooks.

    Accepted from enterprise operators to register or replace the
    webhook target URL for a private fleet.  Registering a new URL for
    the same fleet_id atomically replaces the prior entry.

    Fields
    ------
    fleet_id:
        Non-empty tenant identifier.  Must match the fleet_id value
        used in InboundSignalBatch (same string, case-sensitive).
    target_url:
        HTTP or HTTPS endpoint that will receive drift notifications
        as POST requests with JSON bodies.  Min length 1 (format
        validation is the caller's responsibility for Phase 3;
        Phase 4 enforces HTTPS-only via a URL validator).
    auth_token:
        Optional Bearer token injected as
        "Authorization: Bearer <token>" on outgoing dispatch requests.
        If None, no Authorization header is added.
        NEVER echoed back in any API response.

    Security note (Aegis):
        auth_token is write-only.  The GET /v1/webhooks endpoint
        (if added in Phase 4) must never return this field.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: extra="forbid" prevents unknown field injection;
    #     auth_token is not included in any response body
    #   | test: test_register_webhook_api
    """

    model_config = ConfigDict(extra="forbid")

    fleet_id: str = Field(min_length=1)
    target_url: str = Field(min_length=1)
    auth_token: str | None = None
