"""
tests.test_scorer_redis
========================
Unit tests for RedisAgreementScorer.

All tests use unittest.mock.MagicMock for the Redis client -- no live
Redis server required.  The mock simulates Redis set semantics for the
Sybil resistance test (SADD same client twice -> SCARD returns 1).

Test inventory
--------------
RS1  test_redis_scorer_ingest_calls_sadd
     ingest() calls sadd() with correct key and org_id.
RS2  test_redis_scorer_ingest_calls_expire
     ingest() calls expire() with correct key and TTL (86400s).
RS3  test_redis_scorer_ingest_empty_contributing_orgs_noop
     ingest() with empty contributing_orgs is a no-op (no sadd, no expire).
RS4  test_redis_scorer_promote_quorum_not_met
     eval() returns 0 -> promote_to_public_alert() returns None.
RS5  test_redis_scorer_promote_quorum_met
     eval() returns 2 -> promote_to_public_alert() returns 2.
RS6  test_redis_scorer_promote_quorum_met_custom_threshold
     Custom quorum=3; eval()=0 -> None; eval()=3 -> 3.
RS7  test_redis_scorer_clear_calls_delete
     clear() calls delete() with correct key.
RS8  test_redis_scorer_sybil_resistance
     ADVERSARIAL: same client_id ingested twice; mock eval() returns 0;
     promote_to_public_alert() returns None (below quorum).
RS9  test_redis_scorer_key_format
     _quorum_key() returns the expected sg:quorum:{model_tuple} string.
RS10 test_redis_scorer_ingest_multiple_orgs
     Two distinct org_ids in contributing_orgs -> sadd called twice.
RS11 test_redis_scorer_promote_uses_lua_eval
     promote_to_public_alert() calls eval() with _PROMOTE_LUA_SCRIPT,
     numkeys=1, the correct key, and the quorum threshold.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from engine.correlation import ChangePointResult
from engine.scorer_redis import (
    _PROMOTE_LUA_SCRIPT,
    RedisAgreementScorer,
    _quorum_key,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MODEL = "openai/gpt-4o@2025-08"
CLIENT_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CLIENT_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
EXPECTED_KEY = f"sg:quorum:{MODEL}"
TTL = 86_400


@pytest.fixture()
def mock_redis() -> MagicMock:
    """Return a MagicMock that mimics a redis.Redis client.

    Default eval() return value is 0 (quorum not met).  Tests that need
    a specific cardinality set mock_redis.eval.return_value directly.
    """
    client = MagicMock()
    client.eval.return_value = 0
    return client


@pytest.fixture()
def scorer(mock_redis: MagicMock) -> RedisAgreementScorer:
    """Return a RedisAgreementScorer backed by the mock Redis client."""
    return RedisAgreementScorer(mock_redis)


def _make_result(
    model_tuple: str = MODEL,
    change_detected: bool = True,
    contributing_orgs: list[str] | None = None,
) -> ChangePointResult:
    """Helper: build a ChangePointResult with sensible defaults."""
    return ChangePointResult(
        model_tuple=model_tuple,
        change_detected=change_detected,
        score=7.5,
        threshold=5.0,
        contributing_orgs=contributing_orgs
        if contributing_orgs is not None
        else [CLIENT_A],
    )


# ---------------------------------------------------------------------------
# RS9 -- key format (no Redis mock needed)
# ---------------------------------------------------------------------------


def test_redis_scorer_key_format() -> None:
    """RS9: _quorum_key() produces the expected key format.

    #SG-TRACE: REQ-ENGINE-009
    #   | assumption: key prefix is sg:quorum; model_tuple appended verbatim
    #   | test: test_redis_scorer_key_format
    """
    assert _quorum_key(MODEL) == EXPECTED_KEY
    assert _quorum_key("anthropic/claude-3-opus@2024-02") == (
        "sg:quorum:anthropic/claude-3-opus@2024-02"
    )


# ---------------------------------------------------------------------------
# RS1 -- ingest -> sadd
# ---------------------------------------------------------------------------


def test_redis_scorer_ingest_calls_sadd(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS1: ingest() calls sadd with correct key and org_id.

    #SG-TRACE: REQ-ENGINE-009 | test: test_redis_scorer_ingest_calls_sadd
    """
    scorer.ingest(_make_result())

    mock_redis.sadd.assert_called_once_with(EXPECTED_KEY, CLIENT_A)


# ---------------------------------------------------------------------------
# RS2 -- ingest -> expire
# ---------------------------------------------------------------------------


def test_redis_scorer_ingest_calls_expire(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS2: ingest() calls expire with correct key and 24h TTL.

    #SG-TRACE: REQ-ENGINE-010 | test: test_redis_scorer_expire_called_on_ingest
    """
    scorer.ingest(_make_result())

    mock_redis.expire.assert_called_once_with(EXPECTED_KEY, TTL)


# ---------------------------------------------------------------------------
# RS3 -- ingest with empty contributing_orgs is noop
# ---------------------------------------------------------------------------


def test_redis_scorer_ingest_empty_contributing_orgs_noop(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS3: empty contributing_orgs -> no sadd, no expire.

    #SG-TRACE: REQ-ENGINE-009
    #   | assumption: no-op on empty contributing_orgs prevents phantom
    #     quorum accumulation from incomplete ChangePointResult objects
    #   | test: test_redis_scorer_ingest_empty_contributing_orgs_noop
    """
    scorer.ingest(_make_result(contributing_orgs=[]))

    mock_redis.sadd.assert_not_called()
    mock_redis.expire.assert_not_called()


# ---------------------------------------------------------------------------
# RS4 -- promote: quorum not met
# ---------------------------------------------------------------------------


def test_redis_scorer_promote_quorum_not_met(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS4: eval() returns 0 (single org) -> promote_to_public_alert() None.

    Single-org quorum invariant: one org cannot promote a signal to a
    public alert regardless of CUSUM score.  The Lua script returns 0
    when cardinality is below quorum.

    #SG-TRACE: REQ-ENGINE-011 | test: test_redis_scorer_promote_quorum_not_met
    """
    mock_redis.eval.return_value = 0

    result = scorer.promote_to_public_alert(MODEL)

    mock_redis.eval.assert_called_once_with(
        _PROMOTE_LUA_SCRIPT, 1, EXPECTED_KEY, scorer.quorum
    )
    assert result is None


# ---------------------------------------------------------------------------
# RS5 -- promote: quorum met
# ---------------------------------------------------------------------------


def test_redis_scorer_promote_quorum_met(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS5: eval() returns 2 (two orgs) -> promote_to_public_alert() == 2.

    The Lua script returns the set cardinality when quorum is met and
    atomically deletes the key.

    #SG-TRACE: REQ-ENGINE-011 | test: test_redis_scorer_promote_quorum_met
    """
    mock_redis.eval.return_value = 2

    result = scorer.promote_to_public_alert(MODEL)

    mock_redis.eval.assert_called_once_with(
        _PROMOTE_LUA_SCRIPT, 1, EXPECTED_KEY, scorer.quorum
    )
    assert result == 2


# ---------------------------------------------------------------------------
# RS6 -- promote: custom quorum threshold
# ---------------------------------------------------------------------------


def test_redis_scorer_promote_quorum_met_custom_threshold(
    mock_redis: MagicMock,
) -> None:
    """RS6: quorum=3; eval()=0 -> None; eval()=3 -> 3.

    Verifies the quorum override parameter is forwarded to the Lua EVAL
    call as ARGV[1].

    #SG-TRACE: REQ-ENGINE-011
    #   | assumption: quorum override is passed to eval() as ARGV[1];
    #     Lua script uses tonumber(ARGV[1]) for the comparison
    #   | test: test_redis_scorer_promote_quorum_met_custom_threshold
    """
    scorer3 = RedisAgreementScorer(mock_redis, quorum=3)

    mock_redis.eval.return_value = 0
    assert scorer3.promote_to_public_alert(MODEL) is None
    mock_redis.eval.assert_called_with(_PROMOTE_LUA_SCRIPT, 1, EXPECTED_KEY, 3)

    mock_redis.eval.reset_mock()
    mock_redis.eval.return_value = 3
    assert scorer3.promote_to_public_alert(MODEL) == 3
    mock_redis.eval.assert_called_with(_PROMOTE_LUA_SCRIPT, 1, EXPECTED_KEY, 3)


# ---------------------------------------------------------------------------
# RS7 -- clear -> delete
# ---------------------------------------------------------------------------


def test_redis_scorer_clear_calls_delete(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS7: clear() calls delete() with the correct key.

    After atomic Lua promotion the key is already gone; clear() is a
    safe idempotent no-op in that scenario (Redis DEL on a missing key
    returns 0 without error).

    #SG-TRACE: REQ-ENGINE-009 | test: test_redis_scorer_clear_calls_delete
    """
    scorer.clear(MODEL)

    mock_redis.delete.assert_called_once_with(EXPECTED_KEY)


# ---------------------------------------------------------------------------
# RS8 -- Sybil resistance (ADVERSARIAL)
# ---------------------------------------------------------------------------


def test_redis_scorer_sybil_resistance(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS8 ADVERSARIAL: same client_id ingested twice -> quorum stays unmet.

    Redis set semantics guarantee that SADD of a duplicate member does
    not increase cardinality.  The Lua script therefore returns 0 even
    after two ingests with the same org_id.

    #SG-TRACE: REQ-ENGINE-009
    #   | assumption: Sybil probe submitting duplicate client_id cannot
    #     manufacture quorum; Redis set deduplication is the mechanism
    #   | test: test_redis_scorer_sybil_resistance
    """
    # Lua eval still returns 0 (cardinality stays 1 due to set dedup)
    mock_redis.eval.return_value = 0

    scorer.ingest(_make_result(contributing_orgs=[CLIENT_A]))
    scorer.ingest(_make_result(contributing_orgs=[CLIENT_A]))

    assert mock_redis.sadd.call_count == 2
    mock_redis.sadd.assert_any_call(EXPECTED_KEY, CLIENT_A)

    result = scorer.promote_to_public_alert(MODEL)
    assert result is None, "Single org replayed twice must NOT meet quorum"


# ---------------------------------------------------------------------------
# RS10 -- ingest multiple orgs in one result
# ---------------------------------------------------------------------------


def test_redis_scorer_ingest_multiple_orgs(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS10: two distinct org_ids in contributing_orgs -> sadd called twice.

    #SG-TRACE: REQ-ENGINE-009
    #   | assumption: contributing_orgs may carry >1 org_id in future
    #     federated probe designs; each must be registered separately
    #   | test: test_redis_scorer_ingest_multiple_orgs
    """
    scorer.ingest(_make_result(contributing_orgs=[CLIENT_A, CLIENT_B]))

    assert mock_redis.sadd.call_count == 2
    mock_redis.sadd.assert_any_call(EXPECTED_KEY, CLIENT_A)
    mock_redis.sadd.assert_any_call(EXPECTED_KEY, CLIENT_B)
    mock_redis.expire.assert_called_once_with(EXPECTED_KEY, TTL)


# ---------------------------------------------------------------------------
# RS11 -- promote uses Lua eval with correct script and args
# ---------------------------------------------------------------------------


def test_redis_scorer_promote_uses_lua_eval(
    scorer: RedisAgreementScorer,
    mock_redis: MagicMock,
) -> None:
    """RS11: promote_to_public_alert() calls eval() with correct Lua args.

    Verifies the exact call signature:
      eval(_PROMOTE_LUA_SCRIPT, 1, key, quorum)
    where key = sg:quorum:{model_tuple} and quorum = scorer.quorum.

    This test locks down the contract between the scorer and the Lua
    script so that any inadvertent change to the script or the call
    signature causes an immediate test failure.

    #SG-TRACE: REQ-ENGINE-011
    #   | assumption: eval() args match the Lua KEYS[1] / ARGV[1]
    #     positions; numkeys=1 means KEYS[1]=key, ARGV[1]=quorum
    #   | test: test_redis_scorer_promote_uses_lua_eval
    """
    mock_redis.eval.return_value = 2  # simulates quorum met

    scorer.promote_to_public_alert(MODEL)

    mock_redis.eval.assert_called_once_with(
        _PROMOTE_LUA_SCRIPT,  # exact Lua script string
        1,  # numkeys
        EXPECTED_KEY,  # KEYS[1]
        scorer.quorum,  # ARGV[1]
    )
    # Confirm scard and delete are NOT called directly (Lua handles both)
    mock_redis.scard.assert_not_called()
    mock_redis.delete.assert_not_called()
