"""
seismograph.probe.sdk
=====================
ProbeSDK -- client-side orchestrator for the SEISMOGRAPH canary
probe pipeline.

Responsibilities
----------------
1. Manage OTel-style span lifecycle (start_canary_span /
   finish_canary_span) without requiring a running OTel collector.
   Spans carry gen_ai.* attributes conforming to OpenTelemetry
   GenAI semantic conventions.
2. Synthesise a privacy-preserving CanaryResult from each closed
   span (no raw output ever stored or transmitted).
3. Stage results in a local Aggregator (DP-noise applied on flush).
4. Enforce daily epsilon privacy budget via DPAccountant before
   each flush.  Budget exceeded -> sleep mode (no HTTP POST).
5. POST the resulting SignalBatch to the SEISMOGRAPH ingestion
   gateway on demand, with Ed25519 signature headers.

Privacy contract
----------------
Raw model output NEVER flows through this module.
Only derived, non-reversible features (SHA-256 hash of span_id,
token counts, json_valid flag) are synthesised into CanaryResult
objects and staged in the Aggregator.

Privacy budget contract (P2-004)
---------------------------------
flush() deducts FLUSH_EPSILON (2.0) from the DPAccountant on every
call with pending data.  If the daily budget is exhausted:
  1. A warning is logged: "Daily privacy budget exceeded. Probe
     entering sleep mode."
  2. The aggregator queue is cleared (prevent backlog accumulation).
  3. flush() returns {"status": "budget_exceeded"} gracefully --
     no HTTP request is made.
  4. Budget resets automatically after 24 hours.

Auth contract
-------------
Every outbound HTTP batch is signed with the probe's Ed25519
private key (managed by probe.crypto.KeyManager).  The gateway
verifies the signature before accepting the batch.

fleet_id (P3-001)
-----------------
ProbeConfig.fleet_id is an optional tenant identifier for private
fleet isolation.  When set, it is included in every SignalBatch
before signing, so the gateway can route the batch to the correct
per-fleet CUSUMDetector.  None means the public network path.

#SG-TRACE: REQ-SDK-001
#   | assumption: ProbeConfig is the single source of truth for all
#     probe-side configuration; no ambient env-var coupling
#   | test: test_probe_config_fields
#SG-TRACE: REQ-SDK-002
#   | assumption: OTelSpanContext mirrors the gen_ai.* semantic
#     conventions without requiring a live OTel collector
#   | test: test_otel_span_context_fields
#SG-TRACE: REQ-PRIV-002
#   | assumption: KeyManager is injected for tests; real probes use
#     the default .seismograph_id key file
#   | test: test_flush_sends_signature_headers
#SG-TRACE: REQ-PRIV-011
#   | assumption: DPAccountant daily_budget is set from ProbeConfig;
#     default 10.0 allows 5 flushes/day at FLUSH_EPSILON=2.0
#   | test: test_flush_noop_on_budget_exceeded
#SG-TRACE: REQ-ENT-001
#   | assumption: fleet_id is forwarded into SignalBatch via
#     Aggregator.flush(fleet_id=...) so it is covered by Ed25519
#     signature; no unsigned fleet routing
#   | test: test_fleet_id_in_signed_payload
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from probe.canary import CanaryResult
from probe.crypto import KeyManager, canonical_json
from probe.privacy import (
    Aggregator,
    DPAccountant,
    PrivacyBudgetExceededError,
    recommended_flush_interval_seconds,
)

__all__ = [
    "FLUSH_EPSILON",
    "ProbeConfig",
    "ProbeSDK",
    "OTelSpanContext",
    "recommended_flush_interval_seconds",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Epsilon cost deducted from DPAccountant on every flush() with pending
# data. With daily_budget=10.0, this allows 5 flushes per 24-hour window.
# Change requires Tatiana approval (privacy budget impact).
FLUSH_EPSILON: float = 2.0


# ---------------------------------------------------------------------------
# ProbeConfig
# ---------------------------------------------------------------------------


@dataclass
class ProbeConfig:
    """Immutable configuration for one probe deployment.

    Fields
    ------
    model_tuple:
        Target model identifier, e.g. "openai/gpt-4o@2025-08".
    suite_version_hash:
        SHA-256 hex digest of the frozen canary suite. Used to
        detect suite corpus changes across deployments.
    gateway_endpoint:
        Full URL of the SEISMOGRAPH ingestion endpoint,
        e.g. "http://localhost:8000/v1/signals".
    suite_version:
        Human-readable suite version string, e.g. "v1.0.0".
        Must match the suite_version embedded in canary prompts.
    otel_endpoint:
        OTLP gRPC endpoint for optional collector export.
        Empty string disables live OTel export (Phase 0 default).
    probe_private_key_path:
        Path to PEM-encoded Ed25519 private key for batch signing.
        Empty string disables signing (Phase 0 stub mode).
    flush_interval_seconds:
        Target flush cadence in seconds. Not enforced by the SDK
        itself; caller is responsible for scheduling.
    dry_run:
        If True, flush() builds the SignalBatch but skips the HTTP
        POST. Useful for integration testing without a live gateway.
    daily_epsilon_budget:
        Maximum total epsilon spend allowed per 24-hour window.
        Default 10.0 permits 5 flushes at FLUSH_EPSILON=2.0 each.
        Passed to DPAccountant at SDK initialisation.
    dp_storage_path:
        Path to the JSON file where DPAccountant persists the
        current epsilon spend across process restarts.  None
        (default) disables persistence (budget resets to zero on
        each ProbeSDK instantiation).  Set to a writable path
        such as '.seismograph_dp.json' in production.
    min_flush_interval_seconds:
        Transmission pacing. Minimum seconds between flushes that
        actually transmit (and spend epsilon). When > 0, a flush()
        arriving sooner than this since the last transmission is
        throttled: pending results stay staged in the Aggregator and
        accumulate into the next batch, and no epsilon is spent. This
        decouples collection cadence from transmission cadence so a
        short probe interval does not exhaust the daily DP budget.
        Default 0.0 disables pacing (every flush() transmits). Use
        probe.privacy.recommended_flush_interval_seconds(budget) to
        derive a budget-safe value.
    fleet_id:
        Optional tenant identifier for private fleet isolation.
        None (default) routes batches through the public network
        path (AgreementScorer -> PublicDriftAlert).  A non-None
        value routes through an isolated per-fleet CUSUMDetector
        and produces only private LocalDriftAlerts.

    #SG-TRACE: REQ-SDK-003
    #   | assumption: gateway_endpoint is the full URL including
    #     path (e.g. /v1/signals); SDK does not append any path
    #   | test: test_flush_posts_to_gateway_endpoint
    #SG-TRACE: REQ-PRIV-011
    #   | assumption: daily_epsilon_budget is per-probe-instance;
    #     each ProbeSDK has an independent DPAccountant
    #   | test: test_flush_noop_on_budget_exceeded
    #SG-TRACE: REQ-ENT-001
    #   | assumption: fleet_id is forwarded to SignalBatch.fleet_id
    #     before Ed25519 signing; gateway routing is authenticated
    #   | test: test_fleet_id_in_signed_payload
    """

    model_tuple: str
    suite_version_hash: str
    gateway_endpoint: str
    suite_version: str = "v1.0.0"
    otel_endpoint: str = ""
    probe_private_key_path: str = ""
    flush_interval_seconds: int = 86400
    dry_run: bool = False
    daily_epsilon_budget: float = 10.0
    dp_storage_path: str | None = None
    min_flush_interval_seconds: float = 0.0
    fleet_id: str | None = None


# ---------------------------------------------------------------------------
# OTelSpanContext
# ---------------------------------------------------------------------------


@dataclass
class OTelSpanContext:
    """Mutable span context following OTel GenAI semantic conventions.

    Populated by ProbeSDK.start_canary_span() and closed by
    finish_canary_span().  Attributes accumulate gen_ai.* metrics
    during the span lifetime.

    Fields
    ------
    span_id:
        UUID-format span identifier (not a real W3C trace ID).
    trace_id:
        UUID-format trace identifier.
    model_tuple:
        Model being probed; copied from ProbeConfig at span start.
    suite_version_hash:
        Suite hash; copied from ProbeConfig at span start.
    prompt_count:
        Number of canary prompts submitted in this span.
    start_time_ns:
        Monotonic nanosecond timestamp set at span open.
    end_time_ns:
        Monotonic nanosecond timestamp set at span close.
        Zero until finish_canary_span() is called.
    status_code:
        OTel status: "UNSET" | "OK" | "ERROR".
    attributes:
        Free-form gen_ai.* and error attributes accumulated
        during the span.  Expected keys (all optional):
          gen_ai.usage.output_tokens  (int)
          gen_ai.response.json_valid  (bool)
          gen_ai.prompt_id            (str)
          error.message               (str, ERROR spans only)

    #SG-TRACE: REQ-SDK-004
    #   | assumption: attributes dict is the sole carrier of
    #     gen_ai.* metrics; no raw prompt/response text is stored
    #   | test: test_span_attributes_no_raw_text
    """

    span_id: str
    trace_id: str
    model_tuple: str
    suite_version_hash: str
    prompt_count: int = 0
    start_time_ns: int = 0
    end_time_ns: int = 0
    status_code: str = "UNSET"
    attributes: dict[str, str | int | float | bool] = field(
        default_factory=dict
    )


# ---------------------------------------------------------------------------
# ProbeSDK
# ---------------------------------------------------------------------------


class ProbeSDK:
    """Client-side probe orchestrator.

    Manages the span lifecycle, synthesises CanaryResult objects from
    closed spans, enforces a daily epsilon privacy budget, and flushes
    DP-noised SignalBatches to the gateway.

    Parameters (constructor)
    ------------------------
    config:
        ProbeConfig instance.
    _http_client:
        Optional httpx.Client for dependency injection (testing).
        If None, a new httpx.Client() is created at flush time.
    _key_manager:
        Optional KeyManager for dependency injection (testing).
        If None, a new KeyManager() is created using the default
        .seismograph_id key file path.
    _accountant:
        Optional DPAccountant for dependency injection (testing).
        If None, a DPAccountant(daily_budget=config.daily_epsilon_budget)
        is created at init time.

    #SG-TRACE: REQ-SDK-005
    #   | assumption: one ProbeSDK instance per process; thread safety
    #     not guaranteed (single-threaded probe runner assumed)
    #   | test: test_probe_sdk_single_span_constraint
    """

    def __init__(
        self,
        config: ProbeConfig,
        _http_client: httpx.Client | None = None,
        _key_manager: KeyManager | None = None,
        _accountant: DPAccountant | None = None,
    ) -> None:
        self.config = config
        self._aggregator: Aggregator = Aggregator()
        self._active_span: OTelSpanContext | None = None
        self._http_client: httpx.Client | None = _http_client
        self._key_manager: KeyManager = (
            _key_manager if _key_manager is not None else KeyManager()
        )
        self._accountant: DPAccountant = (
            _accountant
            if _accountant is not None
            else DPAccountant(
                daily_budget=config.daily_epsilon_budget,
                storage_path=config.dp_storage_path,
            )
        )
        # Monotonic timestamp of the last flush that actually transmitted
        # (spent epsilon). None until the first transmission. Used to pace
        # transmissions when config.min_flush_interval_seconds > 0.
        self._last_flush_monotonic: float | None = None

    # ------------------------------------------------------------------
    # Span lifecycle
    # ------------------------------------------------------------------

    def start_canary_span(self, prompt_count: int) -> OTelSpanContext:
        """Open a new canary span.

        Parameters
        ----------
        prompt_count:
            Number of canary prompts that will be sent in this span.

        Returns
        -------
        OTelSpanContext
            The newly opened span.

        Raises
        ------
        RuntimeError
            If a span is already active.

        #SG-TRACE: REQ-SDK-006
        #   | assumption: span_id uniqueness is sufficient for Phase 0
        #     synthetic response_hash; W3C trace context not required
        #   | test: test_start_canary_span_sets_active_span
        """
        if self._active_span is not None:
            raise RuntimeError(
                "A canary span is already active. "
                "Call finish_canary_span() before starting a new one."
            )
        span = OTelSpanContext(
            span_id=str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            model_tuple=self.config.model_tuple,
            suite_version_hash=self.config.suite_version_hash,
            prompt_count=prompt_count,
            start_time_ns=time.monotonic_ns(),
            status_code="UNSET",
        )
        self._active_span = span
        logger.debug(
            "start_canary_span | span_id=%s model_tuple=%s prompts=%d",
            span.span_id,
            span.model_tuple,
            prompt_count,
        )
        return span

    def finish_canary_span(
        self,
        status_code: str = "OK",
        error_message: str | None = None,
    ) -> None:
        """Close the active span and stage a CanaryResult.

        Parameters
        ----------
        status_code:
            OTel span status: "OK" or "ERROR".
        error_message:
            Optional error detail; stored under "error.message".

        Raises
        ------
        RuntimeError
            If no span is currently active.

        #SG-TRACE: REQ-SDK-007
        #   | assumption: gen_ai.usage.output_tokens is the proxy for
        #     output_length; raw character count unavailable without
        #     storing raw output (privacy violation)
        #   | test: test_finish_canary_span_creates_canary_result
        """
        if self._active_span is None:
            raise RuntimeError(
                "No active canary span. "
                "Call start_canary_span() before finish_canary_span()."
            )
        span = self._active_span
        span.end_time_ns = time.monotonic_ns()
        span.status_code = status_code
        if error_message is not None:
            span.attributes["error.message"] = error_message

        output_tokens = span.attributes.get("gen_ai.usage.output_tokens", 0)
        output_length = int(output_tokens) if output_tokens else 0

        json_valid = bool(
            span.attributes.get("gen_ai.response.json_valid", False)
        )
        prompt_id = str(
            span.attributes.get("gen_ai.prompt_id", "span-synthetic")
        )

        response_hash = hashlib.sha256(
            span.span_id.encode("utf-8")
        ).hexdigest()

        latency_ms = int((span.end_time_ns - span.start_time_ns) // 1_000_000)

        result = CanaryResult(
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            model_tuple=span.model_tuple,
            suite_version=self.config.suite_version,
            prompt_id=prompt_id,
            response_hash=response_hash,
            output_length=output_length,
            json_valid=json_valid,
            latency_ms=latency_ms,
        )

        self._aggregator.add_result(result)
        logger.debug(
            "finish_canary_span | span_id=%s status=%s "
            "output_tokens=%d json_valid=%s latency_ms=%d",
            span.span_id,
            status_code,
            output_length,
            json_valid,
            latency_ms,
        )
        self._active_span = None

    def current_span(self) -> OTelSpanContext | None:
        """Return the currently active span, or None.

        #SG-TRACE: REQ-SDK-008
        #   | assumption: caller checks current_span() is not None
        #     before writing attributes; SDK does not enforce this
        #   | test: test_current_span_returns_active_span
        """
        return self._active_span

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def flush(self) -> dict[str, Any]:
        """Flush all staged results to the ingestion gateway.

        Privacy budget gate (P2-004)
        ----------------------------
        Before any HTTP activity:
          1. DPAccountant.reset_if_needed().
          2. DPAccountant.spend(FLUSH_EPSILON).
          3. If PrivacyBudgetExceededError: clear queue, return
             {"status": "budget_exceeded"}.

        Normal flush
        ------------
        For each model_tuple with pending CanaryResults, applies DP
        noise via Aggregator.flush(fleet_id=self.config.fleet_id),
        then POSTs the resulting SignalBatch to the gateway endpoint.

        fleet_id is passed to Aggregator.flush() so it is embedded in
        SignalBatch before Ed25519 signing.  Gateway routing is
        therefore authenticated and cannot be spoofed via unsigned
        headers.

        Returns {"status": "noop"} if no pending data.
        Returns {"status": "accumulating", ...} if throttled by
            min_flush_interval_seconds (results retained, no epsilon spent).
        Returns {"status": "budget_exceeded"} if budget exhausted.
        Returns {"status": "ok", "batches": [...]} on success.

        #SG-TRACE: REQ-SDK-009
        #   | assumption: gateway_endpoint is the complete URL
        #   | test: test_flush_posts_valid_payload_on_202
        #SG-TRACE: REQ-AUTH-002
        #   | assumption: canonical_json bytes sent as body match what
        #     gateway reads via await request.body()
        #   | test: test_flush_sends_signature_headers
        #SG-TRACE: REQ-PRIV-011
        #   | assumption: budget check happens before DP noise / HTTP
        #   | test: test_flush_noop_on_budget_exceeded
        #SG-TRACE: REQ-ENT-001
        #   | assumption: fleet_id forwarded from config -> aggregator
        #     -> SignalBatch before signing
        #   | test: test_fleet_id_in_signed_payload
        """
        pending = self._aggregator.model_tuples_pending()
        if not pending:
            logger.info("flush() called with no pending results -- noop")
            return {"status": "noop"}

        # Transmission pacing gate (decouples collection from transmission).
        # When min_flush_interval_seconds > 0, a flush that arrives sooner
        # than that interval since the last *transmission* is throttled:
        # results stay staged in the Aggregator (which keeps accumulating)
        # and no epsilon is spent. This prevents a short collection interval
        # from burning the daily DP budget in the first few rounds.
        min_interval = self.config.min_flush_interval_seconds
        if min_interval > 0 and self._last_flush_monotonic is not None:
            elapsed = time.monotonic() - self._last_flush_monotonic
            if elapsed < min_interval:
                staged = sum(
                    self._aggregator.pending_count(mt) for mt in pending
                )
                logger.info(
                    "flush() throttled: %.0fs since last transmission < "
                    "min %.0fs; accumulating (%d staged across %d models)",
                    elapsed,
                    min_interval,
                    staged,
                    len(pending),
                )
                return {
                    "status": "accumulating",
                    "seconds_until_flush": round(min_interval - elapsed, 1),
                    "staged_results": staged,
                }

        # Privacy budget gate.
        self._accountant.reset_if_needed()
        try:
            self._accountant.spend(FLUSH_EPSILON)
        except PrivacyBudgetExceededError:
            logger.warning(
                "Daily privacy budget exceeded. Probe entering sleep mode."
            )
            self._aggregator.clear_all()
            return {"status": "budget_exceeded"}

        # Transmission committed (budget spent). Record the time so flushes
        # arriving within min_flush_interval_seconds are throttled above.
        self._last_flush_monotonic = time.monotonic()

        batches: list[dict[str, Any]] = []
        _client: httpx.Client | None = self._http_client

        for model_tuple in pending:
            # Pass fleet_id so it is embedded in SignalBatch before sign.
            signal_batch = self._aggregator.flush(
                model_tuple,
                fleet_id=self.config.fleet_id,
            )

            if self.config.dry_run:
                logger.info(
                    "DRY RUN: would POST batch_id=%s model_tuple=%s "
                    "result_count=%d fleet_id=%s",
                    signal_batch.batch_id,
                    model_tuple,
                    signal_batch.result_count,
                    signal_batch.fleet_id,
                )
                batches.append(
                    {
                        "dry_run": True,
                        "batch_id": signal_batch.batch_id,
                        "result_count": signal_batch.result_count,
                    }
                )
                continue

            payload_dict = signal_batch.to_dict()
            canonical_bytes = canonical_json(payload_dict)
            signature_hex = self._key_manager.private_key.sign(
                canonical_bytes
            ).hex()
            public_key_hex = self._key_manager.public_key_hex

            if _client is None:
                _client = httpx.Client()
            response = _client.post(
                self.config.gateway_endpoint,
                content=canonical_bytes,
                headers={
                    "Content-Type": "application/json",
                    "x-signature": signature_hex,
                    "x-public-key": public_key_hex,
                },
            )

            if response.status_code != 202:
                raise RuntimeError(
                    f"Gateway rejected batch {signal_batch.batch_id}: "
                    f"status={response.status_code} "
                    f"body={response.text[:256]}"
                )

            logger.info(
                "flush() accepted | batch_id=%s model_tuple=%s "
                "result_count=%d fleet_id=%s status=202",
                signal_batch.batch_id,
                model_tuple,
                signal_batch.result_count,
                signal_batch.fleet_id,
            )
            batches.append(response.json())

        return {"status": "ok", "batches": batches}

    # ------------------------------------------------------------------
    # Reserved: run_suite (Phase 1 -- real provider calls)
    # ------------------------------------------------------------------

    def run_suite(
        self,
        suite: list[dict[str, str]] | None = None,
    ) -> list[CanaryResult]:
        """Execute the canary suite against the live model endpoint.

        Not implemented in Phase 0.

        Raises
        ------
        NotImplementedError
            Always.  Implement in Phase 1 with provider SDK wiring.

        #SG-TRACE: REQ-SDK-010 (Phase 1 gate)
        #   | assumption: run_suite() is the Phase 1 entry point
        #   | test: test_run_suite_not_implemented_phase_0
        """
        raise NotImplementedError(
            "run_suite() is not implemented in Phase 0. "
            "Use probe.canary.execute_canary(mock=True) for Phase 0 "
            "structural testing. Phase 1 will wire real provider calls."
        )
