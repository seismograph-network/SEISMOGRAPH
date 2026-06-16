"""
tests.test_adapters
====================
Unit tests for probe/adapters/otel.py and probe/adapters/mcp.py.

OT1 -- on_end with valid gen_ai span stages a CanaryResult
OT2 -- on_end with missing gen_ai.system skips the span
OT3 -- model_tuple constructed correctly from gen_ai.* attrs
OT4 -- output_length sourced from gen_ai.usage.output_tokens
OT5 -- latency_ms computed from span start/end nanoseconds

MC1 -- check_model_weather returns STABLE formatted string
MC2 -- check_model_weather returns DRIFTING with json_success_rate
MC3 -- check_model_weather returns "No data" for unknown model
MC4 -- check_model_weather propagates HTTP error
MC5 -- _parse_weather_list handles missing optional fields gracefully
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from probe.adapters.mcp import (
    _parse_weather_list,
    check_model_weather,
)
from probe.adapters.otel import (
    SeismographSpanProcessor,
    _model_tuple_from_attrs,
)
from probe.crypto import KeyManager
from probe.sdk import ProbeConfig, ProbeSDK

# ---------------------------------------------------------------------------
# OTel test helpers
# ---------------------------------------------------------------------------

_MODEL = "openai/gpt-4o@2025-08"
_SPAN_ID_INT = 0xABCDEF0123456789
# 500 ms expressed in nanoseconds
_START_NS = 1_000_000_000
_END_NS = _START_NS + 500_000_000


@dataclass
class _FakeSpanContext:
    """Minimal stand-in for opentelemetry.trace.SpanContext."""

    span_id: int


@dataclass
class _FakeSpan:
    """Duck-typed ReadableSpan for processor unit tests.

    Provides only the attributes accessed by SeismographSpanProcessor
    so tests remain decoupled from the full OTel SDK internals.
    """

    name: str
    attributes: dict[str, Any]
    context: _FakeSpanContext
    start_time: int | None = None
    end_time: int | None = None


def _make_genai_span(
    *,
    span_name: str = "openai.chat",
    system: str = "openai",
    request_model: str = "gpt-4o",
    response_model: str = "2025-08",
    output_tokens: int = 128,
    finish_reason: str = "stop",
    response_id: str | None = None,
    start_ns: int | None = _START_NS,
    end_ns: int | None = _END_NS,
) -> _FakeSpan:
    """Create a fully-populated fake GenAI span."""
    attrs: dict[str, Any] = {
        "gen_ai.system": system,
        "gen_ai.request.model": request_model,
        "gen_ai.response.model": response_model,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.response.finish_reason": finish_reason,
    }
    if response_id is not None:
        attrs["gen_ai.response.id"] = response_id
    return _FakeSpan(
        name=span_name,
        attributes=attrs,
        context=_FakeSpanContext(span_id=_SPAN_ID_INT),
        start_time=start_ns,
        end_time=end_ns,
    )


@pytest.fixture()
def otel_sdk(tmp_path: Any) -> ProbeSDK:
    """ProbeSDK bound to a temp key; dry_run=True to skip HTTP."""
    key_manager = KeyManager(key_path=tmp_path / ".seismograph_id")
    mock_http = MagicMock()
    config = ProbeConfig(
        model_tuple=_MODEL,
        suite_version_hash="sha256-test-otel-adapter",
        gateway_endpoint="http://test-gateway/v1/signals",
        dry_run=True,
    )
    return ProbeSDK(
        config,
        _http_client=mock_http,
        _key_manager=key_manager,
    )


# ---------------------------------------------------------------------------
# OT1: on_end with valid gen_ai span stages a CanaryResult
# ---------------------------------------------------------------------------


def test_otel_on_end_adds_canary_result(otel_sdk: ProbeSDK) -> None:
    """OT1 -- valid GenAI span -> CanaryResult added to aggregator."""
    processor = SeismographSpanProcessor(otel_sdk)
    span = _make_genai_span()
    assert otel_sdk._aggregator.pending_count(_MODEL) == 0
    processor.on_end(span)  # type: ignore[arg-type]
    assert otel_sdk._aggregator.pending_count(_MODEL) == 1


# ---------------------------------------------------------------------------
# OT2: on_end skips non-GenAI span
# ---------------------------------------------------------------------------


def test_otel_non_genai_span_skipped(otel_sdk: ProbeSDK) -> None:
    """OT2 -- span without gen_ai.system is silently skipped."""
    processor = SeismographSpanProcessor(otel_sdk)
    span = _FakeSpan(
        name="http.request",
        attributes={"http.method": "GET", "http.url": "https://x.com"},
        context=_FakeSpanContext(span_id=_SPAN_ID_INT),
    )
    processor.on_end(span)  # type: ignore[arg-type]
    # No result staged across any model_tuple
    assert otel_sdk._aggregator.model_tuples_pending() == []


# ---------------------------------------------------------------------------
# OT3: model_tuple constructed from gen_ai attributes
# ---------------------------------------------------------------------------


def test_otel_model_tuple_constructed(otel_sdk: ProbeSDK) -> None:
    """OT3 -- model_tuple is <system>/<request_model>@<response_model>."""
    processor = SeismographSpanProcessor(otel_sdk)
    span = _make_genai_span(
        system="anthropic",
        request_model="claude-3-5-sonnet",
        response_model="global",
    )
    processor.on_end(span)  # type: ignore[arg-type]
    expected = "anthropic/claude-3-5-sonnet@global"
    assert otel_sdk._aggregator.pending_count(expected) == 1


def test_model_tuple_from_attrs_missing_system() -> None:
    """OT3-b -- _model_tuple_from_attrs returns None with no system."""
    assert _model_tuple_from_attrs({}) is None
    assert _model_tuple_from_attrs({"http.method": "GET"}) is None


# ---------------------------------------------------------------------------
# OT4: output_length from gen_ai.usage.output_tokens
# ---------------------------------------------------------------------------


def test_otel_output_length(otel_sdk: ProbeSDK) -> None:
    """OT4 -- output_length sourced from gen_ai.usage.output_tokens."""
    processor = SeismographSpanProcessor(otel_sdk)
    span = _make_genai_span(output_tokens=256)
    processor.on_end(span)  # type: ignore[arg-type]
    results = otel_sdk._aggregator._pending[_MODEL]
    assert results[0].output_length == 256


# ---------------------------------------------------------------------------
# OT5: latency_ms from span timing
# ---------------------------------------------------------------------------


def test_otel_latency_ms_computed(otel_sdk: ProbeSDK) -> None:
    """OT5 -- latency_ms = (end_time - start_time) // 1_000_000."""
    processor = SeismographSpanProcessor(otel_sdk)
    # 750 ms = 750_000_000 ns
    span = _make_genai_span(
        start_ns=0,
        end_ns=750_000_000,
    )
    processor.on_end(span)  # type: ignore[arg-type]
    results = otel_sdk._aggregator._pending[_MODEL]
    assert results[0].latency_ms == 750


def test_otel_latency_ms_none_times(otel_sdk: ProbeSDK) -> None:
    """OT5-b -- missing span times yield latency_ms == -1."""
    processor = SeismographSpanProcessor(otel_sdk)
    span = _make_genai_span(start_ns=None, end_ns=None)
    processor.on_end(span)  # type: ignore[arg-type]
    results = otel_sdk._aggregator._pending[_MODEL]
    assert results[0].latency_ms == -1


# ---------------------------------------------------------------------------
# MCP test helpers
# ---------------------------------------------------------------------------

_DRIFTING_MT = "anthropic/claude-3-5-sonnet@global"
_STABLE_MT = "openai/gpt-4o@2025-08"

_WEATHER_PAYLOAD = [
    {
        "model_tuple": _DRIFTING_MT,
        "status": "DRIFTING",
        "recent_json_success_rate": 0.84,
        "recent_avg_output_length": 312.5,
    },
    {
        "model_tuple": _STABLE_MT,
        "status": "STABLE",
        "recent_json_success_rate": 0.99,
        "recent_avg_output_length": None,
    },
]


def _mock_client(payload: Any) -> MagicMock:
    """Return a mock httpx.Client whose GET returns payload."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


# ---------------------------------------------------------------------------
# MC1: STABLE formatted string
# ---------------------------------------------------------------------------


def test_mcp_check_model_weather_stable() -> None:
    """MC1 -- STABLE model returns correctly formatted string."""
    client = _mock_client(_WEATHER_PAYLOAD)
    result = check_model_weather(
        _STABLE_MT,
        base_url="http://test",
        http_client=client,
    )
    assert "STABLE" in result
    assert _STABLE_MT in result
    assert "99%" in result


# ---------------------------------------------------------------------------
# MC2: DRIFTING formatted string with json_success_rate
# ---------------------------------------------------------------------------


def test_mcp_check_model_weather_drifting() -> None:
    """MC2 -- DRIFTING model includes status and success rate."""
    client = _mock_client(_WEATHER_PAYLOAD)
    result = check_model_weather(
        _DRIFTING_MT,
        base_url="http://test",
        http_client=client,
    )
    assert "DRIFTING" in result
    assert _DRIFTING_MT in result
    assert "84%" in result
    # avg output length present
    assert "312.5 tokens" in result


# ---------------------------------------------------------------------------
# MC3: unknown model returns "No data found"
# ---------------------------------------------------------------------------


def test_mcp_check_model_weather_unknown() -> None:
    """MC3 -- model not in payload returns informative message."""
    client = _mock_client(_WEATHER_PAYLOAD)
    result = check_model_weather(
        "cohere/command-r@2025-01",
        base_url="http://test",
        http_client=client,
    )
    assert "No data found" in result
    assert "cohere/command-r@2025-01" in result


# ---------------------------------------------------------------------------
# MC4: HTTP error propagated
# ---------------------------------------------------------------------------


def test_mcp_check_model_weather_http_error() -> None:
    """MC4 -- non-2xx response raises httpx.HTTPStatusError."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500",
        request=MagicMock(),
        response=MagicMock(),
    )
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    with pytest.raises(httpx.HTTPStatusError):
        check_model_weather(
            _STABLE_MT,
            base_url="http://test",
            http_client=client,
        )


# ---------------------------------------------------------------------------
# MC5: _parse_weather_list handles missing optional fields
# ---------------------------------------------------------------------------


def test_mcp_parse_weather_list_missing_optionals() -> None:
    """MC5 -- optional fields absent in JSON default to None.

    Adversarial: gateway returns a minimal payload with only the
    required model_tuple and status fields.
    """
    minimal = [{"model_tuple": "x/y@z", "status": "STABLE"}]
    entries = _parse_weather_list(minimal)
    assert len(entries) == 1
    assert entries[0].model_tuple == "x/y@z"
    assert entries[0].status == "STABLE"
    assert entries[0].recent_json_success_rate is None
    assert entries[0].recent_avg_output_length is None
