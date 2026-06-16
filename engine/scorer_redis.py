"""
seismograph.engine.scorer_redis
================================
Redis-backed cross-observer agreement scorer for distributed quorum tracking.

Replaces the in-process AgreementScorer with a Redis-backed implementation
so that multiple gateway instances can coordinate quorum state without all
traffic hitting a single node.

Design
------
Each (model_tuple) maps to a Redis Set keyed ``sg:quorum:{model_tuple}``.
SADD adds a client_id (pseudonymous Ed25519 public key identity) to the set.
Redis set semantics guarantee deduplication: the same client_id SADD'd N
times counts as exactly one member.  SCARD returns the distinct-member count.
A 24-hour EXPIRE is set on every ingest so stale quorum windows self-evict.

Atomic promotion (P3-003 fix for KNOWN-LIMIT-003)
-------------------------------------------------
The original implementation issued a non-atomic SCARD followed by a
separate DEL.  In a multi-node gateway deployment, two instances could
both observe SCARD >= QUORUM_MIN and both promote the same drift event,
producing duplicate PublicDriftAlerts.

``promote_to_public_alert()`` now executes ``_PROMOTE_LUA_SCRIPT`` via
Redis EVAL.  The Lua script atomically: checks SCARD; if >= quorum, DELs
the key and returns the count; otherwise returns 0.  Redis guarantees
Lua script execution is single-threaded and atomic, eliminating the race.

Consequence for callers: ``clear()`` is now a no-op after a successful
``promote_to_public_alert()`` call, because the key has already been
deleted by the Lua script.  ``clear()`` is retained for explicit manual
eviction and is safe to call (Redis DEL on a non-existent key is a no-op).

Privacy invariants
------------------
* Redis key ``sg:quorum:{model_tuple}`` is derived from a public model
  identifier only.  No raw prompts, outputs, or org secrets are present
  in any Redis key or value.
* ``client_id`` values stored in the set are pseudonymous Ed25519 public
  key fingerprints bound at probe installation time.  The Redis store
  never receives the private key or any cleartext org identity.

Interface conformance
---------------------
``RedisAgreementScorer`` exposes the same three public methods as the
in-process ``AgreementScorer``:
  ``ingest(result)``                  -- record one change-point result
  ``promote_to_public_alert(mt)``     -- return org count or None (atomic)
  ``clear(mt)``                       -- evict quorum state for mt

Gateway code can switch implementations via the ``QUORUM_BACKEND`` env var
without any changes to the calling code in gateway/main.py.

#SG-TRACE: REQ-ENGINE-009
#   | assumption: Redis Set SADD deduplication enforces Sybil resistance
#     at the quorum layer; gateway Ed25519 verification is the upstream
#     gate that prevents fabricated client_id injection
#   | test: test_redis_scorer_sybil_resistance
#SG-TRACE: REQ-ENGINE-010
#   | assumption: EXPIRE 86400s (24h) is sufficient quorum window for
#     Phase 2 alert latency targets; configurable in Phase 3
#   | test: test_redis_scorer_expire_called_on_ingest
#SG-TRACE: REQ-ENGINE-011
#   | assumption: Lua EVAL atomicity eliminates SCARD/DEL race in
#     multi-node deployments; Redis guarantees single-threaded Lua
#   | test: test_redis_scorer_promote_uses_lua_eval
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engine.correlation import AgreementScorer, ChangePointResult

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KEY_PREFIX: str = "sg:quorum"
_QUORUM_TTL_SECONDS: int = 86_400  # 24 hours

# ---------------------------------------------------------------------------
# Lua script for atomic quorum check + delete (KNOWN-LIMIT-003 fix)
# ---------------------------------------------------------------------------
# Executed via EVAL.  KEYS[1] = quorum set key.  ARGV[1] = quorum threshold.
# Returns the set cardinality (>= quorum) on promotion, or 0 if below.
# The atomic check+delete prevents duplicate PublicDriftAlert promotion in
# multi-node gateway deployments.
#
# #SG-TRACE: REQ-ENGINE-011
# #   | assumption: Redis Lua execution is single-threaded and atomic;
# #     no other command can observe the key between SCARD and DEL
# #   | test: test_redis_scorer_promote_uses_lua_eval
_PROMOTE_LUA_SCRIPT: str = """
local key    = KEYS[1]
local quorum = tonumber(ARGV[1])
local count  = redis.call('SCARD', key)
if count >= quorum then
    redis.call('DEL', key)
    return count
end
return 0
"""


def _quorum_key(model_tuple: str) -> str:
    """Return the Redis key for the quorum set of a model_tuple.

    Key is derived from a public model identifier only.  No org secrets
    or raw output data appear in any Redis key or value.

    Args:
        model_tuple: Composite model identifier,
            e.g. ``openai/gpt-4o@2025-08``.

    Returns:
        Redis key string, e.g. ``sg:quorum:openai/gpt-4o@2025-08``.
    """
    return f"{_KEY_PREFIX}:{model_tuple}"


# ---------------------------------------------------------------------------
# RedisAgreementScorer
# ---------------------------------------------------------------------------


class RedisAgreementScorer:
    """Redis-backed cross-observer quorum gate.

    Drop-in replacement for ``engine.correlation.AgreementScorer`` that
    stores quorum state in Redis instead of an in-process dict.  Gateway
    restarts no longer wipe pending quorum state.

    ``promote_to_public_alert()`` uses a Lua EVAL script to atomically
    check the quorum set cardinality and delete the key in one operation,
    eliminating the SCARD/DEL race condition present in the Phase 2
    implementation (KNOWN-LIMIT-003).

    Attributes:
        QUORUM_MIN: Minimum number of distinct agreeing orgs required for
            a public alert.  Mirrors ``AgreementScorer.QUORUM_MIN``.

    #SG-TRACE: REQ-ENGINE-009
    #   | assumption: injected redis_client is a connected redis.Redis
    #     (or compatible mock); no lazy-connect logic here
    #   | test: test_redis_scorer_ingest_calls_sadd
    #SG-TRACE: REQ-ENGINE-011
    #   | assumption: redis_client.eval() maps directly to Redis EVAL;
    #     script, numkeys, key, and quorum arg are passed verbatim
    #   | test: test_redis_scorer_promote_uses_lua_eval
    """

    QUORUM_MIN: int = AgreementScorer.QUORUM_MIN  # 2

    def __init__(
        self,
        redis_client: Any,
        quorum: int | None = None,
    ) -> None:
        """Initialise the Redis-backed scorer.

        Args:
            redis_client: A connected ``redis.Redis`` instance (or any
                object exposing ``sadd``, ``expire``, ``eval``, and
                ``delete`` methods).  Injected rather than created here
                so tests can supply a ``MagicMock`` without a live server.
            quorum: Override for the minimum quorum size.  If None,
                defaults to ``QUORUM_MIN`` (currently 2).
        """
        self._redis = redis_client
        self.quorum: int = quorum if quorum is not None else self.QUORUM_MIN

    # ------------------------------------------------------------------
    # Public interface (mirrors AgreementScorer)
    # ------------------------------------------------------------------

    def ingest(self, result: ChangePointResult) -> None:
        """Record a change-point result from a contributing org.

        For each org_id in ``result.contributing_orgs``, performs:

        1. ``SADD sg:quorum:{model_tuple} {org_id}``
           Redis set semantics deduplicate: the same org_id added N times
           still counts as exactly one member.
        2. ``EXPIRE sg:quorum:{model_tuple} 86400``
           Resets the 24-hour TTL on every ingest so active windows do
           not self-evict during sustained drift episodes.

        Args:
            result: A ``ChangePointResult`` from any detector.
                Only ``change_detected == True`` results should be
                ingested; the gateway enforces this upstream.

        #SG-TRACE: REQ-ENGINE-009
        #   | assumption: contributing_orgs is non-empty for any real
        #     DriftAlert; empty list is a no-op (no SADD, no EXPIRE)
        #   | test: test_redis_scorer_ingest_calls_sadd
        """
        if not result.contributing_orgs:
            return
        key = _quorum_key(result.model_tuple)
        for org_id in result.contributing_orgs:
            self._redis.sadd(key, org_id)
        self._redis.expire(key, _QUORUM_TTL_SECONDS)

    def promote_to_public_alert(self, model_tuple: str) -> int | None:
        """Atomically check quorum and delete the set if met.

        Executes ``_PROMOTE_LUA_SCRIPT`` via ``EVAL``.  The Lua script
        atomically checks ``SCARD sg:quorum:{model_tuple}``; if the
        count meets or exceeds ``self.quorum``, it deletes the key and
        returns the count.  If below quorum, it returns 0 without
        modifying the key.

        Atomicity guarantee: Redis executes Lua scripts as a single
        atomic operation.  No other Redis command can observe or modify
        the key between the SCARD check and the DEL, eliminating the
        race condition that could cause duplicate public alerts in
        multi-node gateway deployments (KNOWN-LIMIT-003 fix).

        Caller note: the key is already deleted on a non-None return.
        Calling ``clear()`` afterward is safe (idempotent no-op) but
        not required.

        Args:
            model_tuple: Composite model identifier to evaluate.

        Returns:
            int count of distinct agreeing orgs if >= ``self.quorum``,
            else None.

        #SG-TRACE: REQ-ENGINE-011
        #   | assumption: eval() is called with the exact Lua script,
        #     numkeys=1, the quorum key, and the quorum threshold int
        #   | test: test_redis_scorer_promote_uses_lua_eval
        #   | test: test_redis_scorer_promote_quorum_met
        #   | test: test_redis_scorer_promote_quorum_not_met
        """
        key = _quorum_key(model_tuple)
        result: int = self._redis.eval(
            _PROMOTE_LUA_SCRIPT, 1, key, self.quorum
        )
        return result if result else None

    def clear(self, model_tuple: str) -> None:
        """Delete the quorum set for a model_tuple.

        Calls ``DEL sg:quorum:{model_tuple}``.  After a successful
        ``promote_to_public_alert()`` call the key has already been
        deleted atomically by the Lua script; calling ``clear()`` is
        therefore a safe no-op in that case.  Retained for explicit
        manual eviction and backward compatibility with gateway code.

        Args:
            model_tuple: The model identifier whose quorum set should
                be deleted.

        #SG-TRACE: REQ-ENGINE-009
        #   | assumption: DEL is idempotent; safe to call even after
        #     atomic Lua promotion has already removed the key
        #   | test: test_redis_scorer_clear_calls_delete
        """
        self._redis.delete(_quorum_key(model_tuple))
