"""
tests.test_privacy
==================
Unit and integration tests for the P2-004 DP composition accounting layer.

Covers:
  DPAccountant unit tests (DP1-DP6):
    Budget math, accumulation, exhaustion, reset logic (time-travel).
  ProbeSDK.flush() integration tests (DP7-DP8):
    HTTP not called on budget exhaustion, aggregator cleared, graceful
    return; normal flush path unaffected with fresh budget.

Test inventory
--------------
DP1  test_dp_accountant_initial_state
     Fresh accountant: current_spend=0, remaining=daily_budget.
DP2  test_dp_accountant_spend_accumulates
     Two spend() calls accumulate correctly.
DP3  test_dp_accountant_raises_on_budget_exceeded
     spend() raises PrivacyBudgetExceededError when over budget.
DP4  test_dp_accountant_state_unchanged_on_exceeded
     State is NOT modified when PrivacyBudgetExceededError is raised.
DP5  test_dp_accountant_reset_not_triggered_within_24h
     reset_if_needed() returns False and does NOT reset within 24h.
DP6  test_dp_accountant_resets_after_24h
     reset_if_needed() returns True and resets after time-travel past 24h.
DP7  test_flush_noop_on_budget_exceeded
     INTEGRATION: ProbeSDK.flush() with exhausted budget -> HTTP NOT called,
     aggregator cleared, returns {"status": "budget_exceeded"}.
DP8  test_flush_proceeds_with_fresh_budget
     INTEGRATION: ProbeSDK.flush() with fresh budget -> HTTP IS called,
     returns {"status": "ok"}.
DP9  test_dp_accountant_exact_budget_boundary
     Spending exactly equal to daily_budget (not exceeding) is allowed.
DP10 test_dp_accountant_invalid_inputs
     Negative / zero epsilon and non-positive daily_budget raise ValueError.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from probe.privacy import (
    DPAccountant,
    PrivacyBudgetExceededError,
    recommended_flush_interval_seconds,
)
from probe.sdk import FLUSH_EPSILON, ProbeConfig, ProbeSDK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODEL = "openai/gpt-4o@2025-08"


def _sdk_with_one_pending_span(
    key_manager: object,
    http_client: object,
    accountant: DPAccountant | None = None,
) -> ProbeSDK:
    """Build a ProbeSDK with one finished span staged in the aggregator."""
    config = ProbeConfig(
        model_tuple=MODEL,
        suite_version_hash="a" * 64,
        gateway_endpoint="http://localhost:8000/v1/signals",
        daily_epsilon_budget=10.0,
    )
    sdk = ProbeSDK(
        config,
        _http_client=http_client,
        _key_manager=key_manager,
        _accountant=accountant,
    )
    span = sdk.start_canary_span(prompt_count=1)
    span.attributes["gen_ai.usage.output_tokens"] = 128
    span.attributes["gen_ai.response.json_valid"] = True
    sdk.finish_canary_span(status_code="OK")
    return sdk


# ---------------------------------------------------------------------------
# DP1 -- initial state
# ---------------------------------------------------------------------------


def test_dp_accountant_initial_state() -> None:
    """DP1: fresh DPAccountant has zero spend and full remaining budget.

    #SG-TRACE: REQ-PRIV-011 | test: test_dp_accountant_initial_state
    """
    acc = DPAccountant(daily_budget=10.0)
    assert acc.current_spend == 0.0
    assert acc.daily_budget == 10.0
    assert acc.remaining == 10.0
    assert isinstance(acc.window_start_time, datetime)
    assert acc.window_start_time.tzinfo is not None


# ---------------------------------------------------------------------------
# DP2 -- spend accumulation
# ---------------------------------------------------------------------------


def test_dp_accountant_spend_accumulates() -> None:
    """DP2: two spend() calls accumulate correctly.

    #SG-TRACE: REQ-PRIV-011 | test: test_dp_accountant_spend_accumulates
    """
    acc = DPAccountant(daily_budget=10.0)
    acc.spend(2.0)
    assert acc.current_spend == pytest.approx(2.0)
    assert acc.remaining == pytest.approx(8.0)

    acc.spend(3.0)
    assert acc.current_spend == pytest.approx(5.0)
    assert acc.remaining == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# DP3 -- budget exceeded raises
# ---------------------------------------------------------------------------


def test_dp_accountant_raises_on_budget_exceeded() -> None:
    """DP3: spend() raises PrivacyBudgetExceededError when over budget.

    With daily_budget=4.0 and FLUSH_EPSILON=2.0:
      - flush 1 (spend 2.0): ok
      - flush 2 (spend 2.0): ok (total 4.0 == budget)
      - flush 3 (spend 2.0): raises (4.0 + 2.0 > 4.0)

    #SG-TRACE: REQ-PRIV-011
    #   | assumption: > comparison (not >=) means exactly hitting
    #     the budget is allowed; exceeding it is not
    #   | test: test_dp_accountant_raises_on_budget_exceeded
    """
    acc = DPAccountant(daily_budget=4.0)
    acc.spend(2.0)
    acc.spend(2.0)
    assert acc.current_spend == pytest.approx(4.0)

    with pytest.raises(PrivacyBudgetExceededError) as exc_info:
        acc.spend(2.0)

    assert "exhausted" in str(exc_info.value).lower()
    assert "budget" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# DP4 -- state unchanged on exceeded
# ---------------------------------------------------------------------------


def test_dp_accountant_state_unchanged_on_exceeded() -> None:
    """DP4: current_spend is NOT modified when the budget is exceeded.

    Preserves an all-or-nothing spend contract: a failed spend() call
    must leave the accountant in its pre-call state.

    #SG-TRACE: REQ-PRIV-011
    #   | test: test_dp_accountant_state_unchanged_on_exceeded
    """
    acc = DPAccountant(daily_budget=5.0)
    acc.spend(4.0)
    spend_before = acc.current_spend

    with pytest.raises(PrivacyBudgetExceededError):
        acc.spend(2.0)  # 4.0 + 2.0 = 6.0 > 5.0

    assert acc.current_spend == pytest.approx(spend_before), (
        "current_spend must not change on a failed spend()"
    )


# ---------------------------------------------------------------------------
# DP5 -- reset NOT triggered within 24h
# ---------------------------------------------------------------------------


def test_dp_accountant_reset_not_triggered_within_24h() -> None:
    """DP5: reset_if_needed() returns False and leaves spend intact.

    #SG-TRACE: REQ-PRIV-011
    #   | test: test_dp_accountant_reset_not_triggered_within_24h
    """
    acc = DPAccountant(daily_budget=10.0)
    acc.spend(6.0)

    reset = acc.reset_if_needed()

    assert reset is False
    assert acc.current_spend == pytest.approx(6.0), (
        "current_spend must not change when window has not expired"
    )


# ---------------------------------------------------------------------------
# DP6 -- time-travel: reset after 24h
# ---------------------------------------------------------------------------


def test_dp_accountant_resets_after_24h() -> None:
    """DP6: reset_if_needed() resets spend after time-travel past 24h.

    Manipulates window_start_time directly to simulate 25 hours elapsed.
    No wall-clock wait required.

    #SG-TRACE: REQ-PRIV-011
    #   | test: test_dp_accountant_resets_after_24h
    """
    acc = DPAccountant(daily_budget=10.0)
    acc.spend(6.0)
    assert acc.current_spend == pytest.approx(6.0)

    # Time-travel: pretend window started 25 hours ago
    acc.window_start_time = datetime.now(timezone.utc) - timedelta(hours=25)

    reset = acc.reset_if_needed()

    assert reset is True, "reset_if_needed() must return True after 24h"
    assert acc.current_spend == 0.0, (
        "current_spend must reset to 0.0 after window expiry"
    )
    # New window_start_time must be recent (within last 5 seconds)
    delta = datetime.now(timezone.utc) - acc.window_start_time
    assert delta.total_seconds() < 5.0, (
        "window_start_time must be refreshed to approximately now"
    )


# ---------------------------------------------------------------------------
# DP9 -- exact budget boundary is allowed
# ---------------------------------------------------------------------------


def test_dp_accountant_exact_budget_boundary() -> None:
    """DP9: spending exactly equal to daily_budget is allowed (> not >=).

    #SG-TRACE: REQ-PRIV-011
    #   | assumption: condition is current_spend + epsilon > daily_budget
    #     so spending exactly the budget ceiling is permitted
    #   | test: test_dp_accountant_exact_budget_boundary
    """
    acc = DPAccountant(daily_budget=2.0)
    acc.spend(2.0)  # 0 + 2.0 == 2.0, NOT > 2.0 -> must succeed
    assert acc.current_spend == pytest.approx(2.0)
    assert acc.remaining == pytest.approx(0.0)

    # Now any further spend must raise
    with pytest.raises(PrivacyBudgetExceededError):
        acc.spend(0.001)


# ---------------------------------------------------------------------------
# DP10 -- invalid inputs
# ---------------------------------------------------------------------------


def test_dp_accountant_invalid_inputs() -> None:
    """DP10: zero/negative epsilon and non-positive daily_budget raise.

    #SG-TRACE: REQ-PRIV-011
    #   | test: test_dp_accountant_invalid_inputs
    """
    acc = DPAccountant(daily_budget=10.0)

    with pytest.raises(ValueError, match="positive"):
        acc.spend(0.0)

    with pytest.raises(ValueError, match="positive"):
        acc.spend(-1.0)

    with pytest.raises(ValueError, match="positive"):
        DPAccountant(daily_budget=0.0)

    with pytest.raises(ValueError, match="positive"):
        DPAccountant(daily_budget=-5.0)


# ---------------------------------------------------------------------------
# DP7 -- INTEGRATION: flush() budget exceeded -> HTTP not called
# ---------------------------------------------------------------------------


def test_flush_noop_on_budget_exceeded(tmp_path, caplog) -> None:
    """DP7 INTEGRATION: exhausted budget -> HTTP not called, queue cleared.

    Sets up a ProbeSDK with a DPAccountant at 0.0 remaining epsilon.
    Adds one pending span result.  Calls flush().

    Asserts:
      - HTTP client post() is NOT called.
      - Aggregator is cleared (pending_count == 0 after flush).
      - Return value is {"status": "budget_exceeded"}.
      - Warning is logged.

    #SG-TRACE: REQ-PRIV-011 | test: test_flush_noop_on_budget_exceeded
    """
    from probe.crypto import KeyManager

    key_manager = KeyManager(key_path=tmp_path / ".seismograph_id")

    # Build an accountant that is completely exhausted
    exhausted_acc = DPAccountant(daily_budget=2.0)
    exhausted_acc.spend(2.0)  # fully spent: remaining = 0.0

    mock_http = MagicMock()

    sdk = _sdk_with_one_pending_span(
        key_manager=key_manager,
        http_client=mock_http,
        accountant=exhausted_acc,
    )

    # Confirm there is pending data before the flush
    assert sdk._aggregator.pending_count(MODEL) == 1

    with caplog.at_level(logging.WARNING, logger="probe.sdk"):
        result = sdk.flush()

    # HTTP must NOT have been called
    mock_http.post.assert_not_called()

    # Aggregator must be cleared
    assert sdk._aggregator.pending_count(MODEL) == 0, (
        "Aggregator must be empty after budget-exceeded flush"
    )

    # Return value
    assert result == {"status": "budget_exceeded"}, (
        f"Expected budget_exceeded status, got {result!r}"
    )

    # Warning must be logged
    warning_msgs = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("budget" in m.lower() for m in warning_msgs), (
        "Expected a budget-exceeded warning in logs"
    )


# ---------------------------------------------------------------------------
# DP8 -- INTEGRATION: flush() succeeds with fresh budget
# ---------------------------------------------------------------------------


def test_flush_proceeds_with_fresh_budget(tmp_path) -> None:
    """DP8 INTEGRATION: fresh budget -> HTTP IS called, returns ok.

    Confirms the normal flush path is unaffected by the budget gate.

    #SG-TRACE: REQ-PRIV-011 | test: test_flush_proceeds_with_fresh_budget
    """
    from probe.crypto import KeyManager

    key_manager = KeyManager(key_path=tmp_path / ".seismograph_id")

    mock_http = MagicMock()
    mock_http.post.return_value.status_code = 202
    mock_http.post.return_value.json.return_value = {
        "status": "accepted",
        "batch_id": "mock-batch",
        "result_count": 1,
        "alerts": [],
    }

    # Fresh accountant with full budget
    fresh_acc = DPAccountant(daily_budget=10.0)

    sdk = _sdk_with_one_pending_span(
        key_manager=key_manager,
        http_client=mock_http,
        accountant=fresh_acc,
    )

    result = sdk.flush()

    # HTTP must have been called exactly once
    mock_http.post.assert_called_once()

    # Budget deducted
    assert fresh_acc.current_spend == pytest.approx(FLUSH_EPSILON)

    # Return value
    assert result["status"] == "ok"
    assert len(result["batches"]) == 1


# ---------------------------------------------------------------------------
# DP-PERSIST -- persistent budget survives process restart
# ---------------------------------------------------------------------------


def test_dp_accountant_persistent_budget(tmp_path) -> None:
    """DP-PERSIST: budget state persists across DPAccountant instances.

    Simulates a process restart: spend 2.0 on the first instance, then
    instantiate a new DPAccountant with the same file and verify that
    current_spend is restored to 2.0 rather than starting at 0.0.

    This is the fix for KNOWN-LIMIT-005 (P3-003 tech debt).

    #SG-TRACE: REQ-PRIV-011
    #   | assumption: _persist() writes after spend(); _load() reads in
    #     __init__; file is written atomically via os.replace()
    #   | test: test_dp_accountant_persistent_budget
    """
    storage = str(tmp_path / ".seismograph_dp_test.json")

    # First instance: spend 2.0 and let it persist
    acct1 = DPAccountant(daily_budget=10.0, storage_path=storage)
    acct1.spend(2.0)
    assert acct1.current_spend == pytest.approx(2.0)
    del acct1

    # Second instance: must restore spend from file
    acct2 = DPAccountant(daily_budget=10.0, storage_path=storage)
    assert acct2.current_spend == pytest.approx(2.0), (
        "DP-PERSIST FAIL: current_spend was not restored after restart; "
        f"got {acct2.current_spend}"
    )
    assert acct2.remaining == pytest.approx(8.0), (
        "DP-PERSIST FAIL: remaining budget incorrect after restore"
    )


def test_dp_accountant_persistent_budget_corrupt_file(
    tmp_path,
) -> None:
    """DP-PERSIST-ADV: corrupted storage file -> graceful fallback to 0.0.

    If the JSON file is unreadable or contains invalid data, DPAccountant
    must start fresh (current_spend=0.0) rather than raising an exception.

    #SG-TRACE: REQ-PRIV-011
    #   | assumption: _load() wraps all I/O in try/except; corrupt file
    #     is silently ignored and defaults are preserved
    #   | test: test_dp_accountant_persistent_budget_corrupt_file
    """
    storage = str(tmp_path / ".seismograph_dp_corrupt.json")
    with open(storage, "w") as fh:
        fh.write("NOT VALID JSON {{{{")

    acct = DPAccountant(daily_budget=10.0, storage_path=storage)
    assert acct.current_spend == 0.0, (
        "DP-PERSIST-ADV FAIL: corrupt file must not raise; "
        f"got current_spend={acct.current_spend}"
    )


# ---------------------------------------------------------------------------
# DP-PACE -- transmission pacing helper (P2-012)
# ---------------------------------------------------------------------------


def test_recommended_flush_interval_seconds() -> None:
    """DP-PACE: pacing interval = window / floor(budget / flush_epsilon).

    Confirms the helper spreads the daily budget across the window and
    guards invalid inputs.

    #SG-TRACE: REQ-PRIV-012 | test: test_recommended_flush_interval_seconds
    """
    # Default budget: 10.0 / 2.0 = 5 flushes -> 86400 / 5 = 17280s (4.8h).
    assert recommended_flush_interval_seconds(10.0, 2.0) == pytest.approx(17280.0)

    # Smaller budget -> fewer flushes -> longer interval.
    assert recommended_flush_interval_seconds(4.0, 2.0) == pytest.approx(43200.0)

    # Budget below one flush still yields a single flush/day (no div-by-zero).
    assert recommended_flush_interval_seconds(1.0, 2.0) == pytest.approx(86400.0)

    # Custom window is honoured.
    assert recommended_flush_interval_seconds(
        10.0, 2.0, window_seconds=3600.0
    ) == pytest.approx(720.0)

    # Invalid inputs raise.
    with pytest.raises(ValueError):
        recommended_flush_interval_seconds(0.0, 2.0)
    with pytest.raises(ValueError):
        recommended_flush_interval_seconds(10.0, 0.0)
