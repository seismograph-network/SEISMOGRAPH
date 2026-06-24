"""
tests.test_sdk
==============
Unit tests for probe/sdk.py -- ProbeSDK span lifecycle and flush().

Test contract
-------------
T1  test_span_lifecycle_creates_canary_result
    Start a span, inject gen_ai.* attributes, finish it.
    Assert: one CanaryResult staged in Aggregator with correct fields.

T2  test_flush_posts_valid_payload_on_202
    Full lifecycle (span + flush) with injected mock httpx client.
    Assert: POST called once at correct URL; body sent as content= bytes
    with real Ed25519 signature headers (128/64-char hex); payload
    parses as valid InboundSignalBatch; flush() returns ok.

T3  test_flush_noop_on_empty_aggregator
    Call flush() with no prior span execution.
    Assert: returns {"status": "noop"} without any HTTP call.

T4  test_flush_raises_on_non_202
    Inject a mock client returning HTTP 500.
    Assert: flush() raises RuntimeError containing the status code.

Fixtures
--------
key_manager (tmp_path):
    Injects a KeyManager writing to tmp_path / ".seismograph_id" so
    tests never write to the real working directory.

#SG-TRACE: REQ-SDK-007 | test: test_span_lifecycle_creates_canary_result
#SG-TRACE: REQ-SDK-009 | test: test_flush_posts_valid_payload_on_202
#SG-TRACE: REQ-AUTH-002 | test: test_flush_posts_valid_payload_on_202 (T2)
"""

from __future__ import annotations

import json as _json
from unittest.mock import MagicMock

import pytest
from gateway.schema import InboundSignalBatch
from probe.crypto import KeyManager
from probe.sdk import FLUSH_EPSILON, ProbeConfig, ProbeSDK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_TUPLE = "anthropic/claude-3-5-sonnet@global"
_SUITE_HASH = "a" * 64  # valid 64-char hex for ProbeConfig
_GATEWAY_URL = "http://localhost:8000/v1/signals"


def _make_config(**kwargs) -> ProbeConfig:
    """Return a ProbeConfig with test defaults."""
    defaults = dict(
        model_tuple=_MODEL_TUPLE,
        suite_version_hash=_SUITE_HASH,
        gateway_endpoint=_GATEWAY_URL,
    )
    defaults.update(kwargs)
    return ProbeConfig(**defaults)


def _make_mock_client(status_code: int = 202) -> MagicMock:
    """Return a mock httpx.Client whose post() returns a fake response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {
        "status": "accepted",
        "batch_id": "00000000-0000-0000-0000-000000000001",
        "result_count": 1,
        "alerts": [],
    }
    mock_response.text = "accepted"

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def key_manager(tmp_path):
    """Inject a KeyManager writing to tmp_path to avoid .seismograph_id."""
    return KeyManager(key_path=tmp_path / ".seismograph_id")


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------


def test_span_lifecycle_creates_canary_result(key_manager):
    """T1: start_canary_span + finish_canary_span stages a CanaryResult."""
    config = _make_config()
    sdk = ProbeSDK(config, _key_manager=key_manager)

    assert sdk.current_span() is None

    span = sdk.start_canary_span(prompt_count=3)
    assert sdk.current_span() is span
    assert span.model_tuple == _MODEL_TUPLE
    assert span.prompt_count == 3
    assert span.status_code == "UNSET"

    span.attributes["gen_ai.usage.output_tokens"] = 256
    span.attributes["gen_ai.response.json_valid"] = True
    span.attributes["gen_ai.prompt_id"] = "v1.0.0-format"

    sdk.finish_canary_span(status_code="OK")

    assert sdk.current_span() is None
    assert sdk._aggregator.pending_count(_MODEL_TUPLE) == 1

    results = sdk._aggregator._pending[_MODEL_TUPLE]
    assert len(results) == 1
    r = results[0]

    assert r.model_tuple == _MODEL_TUPLE
    assert r.suite_version == "v1.0.0"
    assert r.prompt_id == "v1.0.0-format"
    assert r.output_length == 256
    assert r.json_valid is True
    assert r.latency_ms >= 0
    assert len(r.response_hash) == 64
    assert all(c in "0123456789abcdef" for c in r.response_hash)
    assert not hasattr(r, "raw_output")


# ---------------------------------------------------------------------------
# T2
# ---------------------------------------------------------------------------


def test_flush_posts_valid_payload_on_202(key_manager):
    """T2: flush() POSTs signed canonical JSON; handles 202.

    Verifies body sent as content= bytes (not json= dict), headers carry
    real Ed25519 signature, and payload parses as InboundSignalBatch.
    """
    mock_client = _make_mock_client(status_code=202)
    config = _make_config()
    sdk = ProbeSDK(config, _http_client=mock_client, _key_manager=key_manager)

    span = sdk.start_canary_span(prompt_count=1)
    span.attributes["gen_ai.usage.output_tokens"] = 128
    span.attributes["gen_ai.response.json_valid"] = False
    sdk.finish_canary_span(status_code="OK")

    result = sdk.flush()

    assert mock_client.post.call_count == 1
    call_args = mock_client.post.call_args
    url = call_args[0][0] if call_args[0] else call_args.args[0]
    kwargs = call_args.kwargs
    assert url == _GATEWAY_URL

    # Body must be bytes (content=), not dict (json=)
    posted_content = kwargs.get("content", b"")
    assert isinstance(posted_content, bytes), (
        "flush() must send body as content= bytes"
    )
    posted_json = _json.loads(posted_content)

    # Signature headers must be real hex (not stub empty strings)
    headers = kwargs.get("headers", {})
    sig_hex = headers.get("x-signature", "")
    pub_hex = headers.get("x-public-key", "")
    assert len(sig_hex) == 128, (
        f"x-signature must be 128 hex chars, got {len(sig_hex)}"
    )
    assert len(pub_hex) == 64, (
        f"x-public-key must be 64 hex chars, got {len(pub_hex)}"
    )
    bytes.fromhex(sig_hex)
    bytes.fromhex(pub_hex)

    # Schema validation (adversarial gate)
    batch = InboundSignalBatch.model_validate(posted_json)
    assert batch.model_tuple == _MODEL_TUPLE
    assert batch.result_count >= 1

    assert result["status"] == "ok"
    assert len(result["batches"]) == 1
    assert result["batches"][0]["status"] == "accepted"


# ---------------------------------------------------------------------------
# T3
# ---------------------------------------------------------------------------


def test_flush_noop_on_empty_aggregator(key_manager):
    """T3: flush() returns noop when no spans have been closed."""
    mock_client = _make_mock_client()
    config = _make_config()
    sdk = ProbeSDK(config, _http_client=mock_client, _key_manager=key_manager)

    result = sdk.flush()

    assert result == {"status": "noop"}
    mock_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# T4
# ---------------------------------------------------------------------------


def test_flush_raises_on_non_202(key_manager):
    """T4: flush() raises RuntimeError when gateway returns non-202."""
    mock_client = _make_mock_client(status_code=500)
    mock_client.post.return_value.text = "internal server error"
    config = _make_config()
    sdk = ProbeSDK(config, _http_client=mock_client, _key_manager=key_manager)

    span = sdk.start_canary_span(prompt_count=1)
    span.attributes["gen_ai.usage.output_tokens"] = 64
    sdk.finish_canary_span()

    with pytest.raises(RuntimeError) as exc_info:
        sdk.flush()

    assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T5 -- transmission pacing (P2-012): throttle within interval
# ---------------------------------------------------------------------------


def _stage_one_span(sdk: ProbeSDK, tokens: int = 64) -> None:
    """Open + close one canary span so a CanaryResult is staged."""
    span = sdk.start_canary_span(prompt_count=1)
    span.attributes["gen_ai.usage.output_tokens"] = tokens
    span.attributes["gen_ai.response.json_valid"] = True
    sdk.finish_canary_span(status_code="OK")


def test_flush_throttle_accumulates_within_interval(key_manager):
    """T5: a second flush within min_flush_interval accumulates, not sends.

    The first flush transmits and spends epsilon. A second flush arriving
    immediately is throttled: no HTTP call, no extra epsilon, and the newly
    staged result stays in the Aggregator for the next batch.
    """
    mock_client = _make_mock_client(status_code=202)
    config = _make_config(min_flush_interval_seconds=3600.0)
    sdk = ProbeSDK(config, _http_client=mock_client, _key_manager=key_manager)

    # Round 1: stage + flush -> transmits.
    _stage_one_span(sdk)
    first = sdk.flush()
    assert first["status"] == "ok"
    assert mock_client.post.call_count == 1
    assert sdk._accountant.current_spend == pytest.approx(FLUSH_EPSILON)

    # Round 2: stage + flush immediately -> throttled (accumulating).
    _stage_one_span(sdk)
    second = sdk.flush()
    assert second["status"] == "accumulating"
    assert second["staged_results"] == 1
    # No second HTTP call, no extra epsilon spent.
    assert mock_client.post.call_count == 1
    assert sdk._accountant.current_spend == pytest.approx(FLUSH_EPSILON)
    # The result is retained for the next transmission (not dropped).
    assert sdk._aggregator.pending_count(_MODEL_TUPLE) == 1


# ---------------------------------------------------------------------------
# T6 -- transmission pacing: resumes after the interval elapses
# ---------------------------------------------------------------------------


def test_flush_resumes_after_interval_elapses(key_manager):
    """T6: once min_flush_interval has elapsed, flush transmits again.

    Simulates elapsed time by back-dating the last-transmission marker.
    """
    mock_client = _make_mock_client(status_code=202)
    config = _make_config(min_flush_interval_seconds=100.0)
    sdk = ProbeSDK(config, _http_client=mock_client, _key_manager=key_manager)

    _stage_one_span(sdk)
    sdk.flush()
    assert mock_client.post.call_count == 1

    # Pretend 1000s passed since the last transmission (> 100s interval).
    assert sdk._last_flush_monotonic is not None
    sdk._last_flush_monotonic -= 1000.0

    _stage_one_span(sdk)
    result = sdk.flush()
    assert result["status"] == "ok"
    assert mock_client.post.call_count == 2
    assert sdk._accountant.current_spend == pytest.approx(2 * FLUSH_EPSILON)


# ---------------------------------------------------------------------------
# T7 -- pacing disabled by default: every flush transmits
# ---------------------------------------------------------------------------


def test_flush_not_throttled_when_pacing_disabled(key_manager):
    """T7: with min_flush_interval_seconds=0 (default), no throttling."""
    mock_client = _make_mock_client(status_code=202)
    config = _make_config()  # min_flush_interval_seconds defaults to 0.0
    sdk = ProbeSDK(config, _http_client=mock_client, _key_manager=key_manager)

    _stage_one_span(sdk)
    assert sdk.flush()["status"] == "ok"
    _stage_one_span(sdk)
    assert sdk.flush()["status"] == "ok"
    assert mock_client.post.call_count == 2
