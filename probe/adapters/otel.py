"""
seismograph.probe.adapters.otel
================================
OpenTelemetry SpanProcessor adapter.

Passively taps gen_ai.* spans emitted by the user's existing OTel
instrumentation and feeds SEISMOGRAPH canary metrics without any
additional prompt or API call.

Privacy contract: only span attributes are inspected; no raw text.
  - response_hash = SHA-256(gen_ai.response.id) if present, else
    SHA-256(span.context.span_id bytes).  Non-reversible.
  - output_length = gen_ai.usage.output_tokens (integer).
  - json_valid = True iff gen_ai.response.finish_reason == "stop".
  - Raw prompt text and raw model output are never stored.

Usage::

    from opentelemetry.sdk.trace import TracerProvider
    from probe.adapters.otel import SeismographSpanProcessor
    from probe.sdk import ProbeConfig, ProbeSDK

    sdk = ProbeSDK(ProbeConfig(
        model_tuple="openai/gpt-4o@2025-08",
        gateway_url="https://your-gateway.example.com",
    ))
    provider = TracerProvider()
    provider.add_span_processor(SeismographSpanProcessor(sdk))

#SG-TRACE: REQ-OTEL-001
#   | assumption: gen_ai.system is the reliable discriminator for
#     GenAI spans; non-GenAI spans are silently skipped
#   | test: test_otel_non_genai_span_skipped
#SG-TRACE: REQ-OTEL-002
#   | assumption: span.context.span_id (8-byte int) has sufficient
#     entropy for response fingerprinting in Phase 0
#   | test: test_otel_on_end_adds_canary_result
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from probe.canary import SUITE_VERSION, CanaryResult
from probe.sdk import ProbeSDK

# ---------------------------------------------------------------------------
# GenAI semantic convention attribute keys (OTel spec)
# ---------------------------------------------------------------------------

_ATTR_SYSTEM: str = "gen_ai.system"
_ATTR_REQUEST_MODEL: str = "gen_ai.request.model"
_ATTR_RESPONSE_MODEL: str = "gen_ai.response.model"
_ATTR_OUTPUT_TOKENS: str = "gen_ai.usage.output_tokens"
_ATTR_FINISH_REASON: str = "gen_ai.response.finish_reason"
_ATTR_RESPONSE_ID: str = "gen_ai.response.id"


# ---------------------------------------------------------------------------
# Attribute extraction helpers
# ---------------------------------------------------------------------------


def _model_tuple_from_attrs(
    attrs: dict[str, Any],
) -> str | None:
    """Construct model_tuple from GenAI semantic convention attributes.

    Returns None if gen_ai.system is absent (not a GenAI span).

    Format: ``<system>/<request_model>@<response_model_or_latest>``

    Args:
        attrs: Flat dict of span attributes.

    Returns:
        Model tuple string, or None if the span should be skipped.
    """
    system: str | None = attrs.get(_ATTR_SYSTEM)
    if not system:
        return None
    model: str = str(attrs.get(_ATTR_REQUEST_MODEL, "unknown"))
    version: str = str(attrs.get(_ATTR_RESPONSE_MODEL, "latest"))
    return f"{system}/{model}@{version}"


def _response_hash_from_span(span: ReadableSpan) -> str:
    """SHA-256 fingerprint derived from span identity.

    Prefers gen_ai.response.id if present; falls back to the
    span_id integer encoded as 8 big-endian bytes.

    Privacy: the hash is non-reversible in both cases.

    Args:
        span: A completed OTel ReadableSpan.

    Returns:
        Lowercase hex SHA-256 digest string.
    """
    attrs = dict(span.attributes or {})
    response_id = attrs.get(_ATTR_RESPONSE_ID)
    if response_id:
        return hashlib.sha256(str(response_id).encode()).hexdigest()
    span_id_int: int = span.context.span_id  # type: ignore[union-attr]
    return hashlib.sha256(
        span_id_int.to_bytes(8, "big"),
    ).hexdigest()


# ---------------------------------------------------------------------------
# SpanProcessor implementation
# ---------------------------------------------------------------------------


class SeismographSpanProcessor(SpanProcessor):
    """Passively taps completed gen_ai.* OTel spans.

    Inject into a TracerProvider to automatically stage canary
    metrics for every GenAI API call the user's code already makes.
    No additional prompts or API calls are issued.

    Thread-safety: Aggregator.add_result() is not thread-safe in
    Phase 0 (documented as KNOWN-LIMIT-007). For multi-threaded
    applications, wrap with a locking TracerProvider or use a
    dedicated SDK instance per thread.

    Attributes:
        _sdk: The bound ProbeSDK instance whose aggregator receives
            the staged CanaryResult objects.

    #SG-TRACE: REQ-OTEL-001
    #   | assumption: gen_ai.system present on every GenAI span
    #     emitted by opentelemetry-instrumentation-openai and
    #     opentelemetry-instrumentation-anthropic
    #   | test: test_otel_non_genai_span_skipped
    """

    def __init__(self, sdk: ProbeSDK) -> None:
        """Bind to a ProbeSDK instance.

        Args:
            sdk: The ProbeSDK whose aggregator will receive
                staged CanaryResult objects.
        """
        self._sdk: ProbeSDK = sdk

    def on_start(
        self,
        span: Any,
        parent_context: Any = None,
    ) -> None:
        """No-op; we only inspect completed spans."""

    def on_end(self, span: ReadableSpan) -> None:
        """Extract gen_ai.* attributes and stage a CanaryResult.

        Silently skips spans that lack gen_ai.system.

        Args:
            span: A completed ReadableSpan from the OTel SDK.

        #SG-TRACE: REQ-OTEL-002
        #   | assumption: span.end_time - span.start_time is
        #     nanosecond resolution; //1_000_000 converts to ms
        #   | test: test_otel_latency_ms_computed
        """
        attrs: dict[str, Any] = dict(span.attributes or {})
        model_tuple = _model_tuple_from_attrs(attrs)
        if model_tuple is None:
            return

        response_hash = _response_hash_from_span(span)

        output_length: int = int(attrs.get(_ATTR_OUTPUT_TOKENS, 0))
        finish_reason: str = str(attrs.get(_ATTR_FINISH_REASON, ""))
        json_valid: bool = finish_reason == "stop"

        if span.end_time is not None and span.start_time is not None:
            latency_ms: int = int(
                (span.end_time - span.start_time) // 1_000_000
            )
        else:
            latency_ms = -1

        result = CanaryResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_tuple=model_tuple,
            suite_version=SUITE_VERSION,
            prompt_id=span.name,
            response_hash=response_hash,
            output_length=output_length,
            json_valid=json_valid,
            latency_ms=latency_ms,
        )
        self._sdk._aggregator.add_result(result)

    def shutdown(self) -> None:
        """No-op; ProbeSDK lifecycle is managed externally."""

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """No-op; flush is driven by ProbeSDK.flush().

        Returns:
            Always True.
        """
        return True
