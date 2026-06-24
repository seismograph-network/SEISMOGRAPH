"""
seismograph.probe.privacy
==========================
Privacy and aggregation layer -- the ONLY module that produces objects
permitted to cross the probe perimeter onto the network.

Outbound contract:
  - Only SignalBatch instances are transmitted.
  - No raw text, prompts, outputs, or persistent identifiers.
  - All numeric metrics carry Laplace DP noise (epsilon=2.0 per flush).
  - canary_hashes are SHA-256 digests (non-reversible).

Differential privacy design (epsilon=2.0 per flush window):
  - avg_output_length: clamped to [0, MAX_OUTPUT_LENGTH=8192] before
    averaging (bounded sensitivity delta_f=8192); noise scale b=4096.0.
  - json_success_rate: bounded [0,1] by construction; scale b=0.5.
  - result_count: infrastructure counter, not DP-noised (Phase 0).
  NOTE(REQ-PRIV-010): sensitivity uses global MAX as conservative bound.
  Phase 1 will refine to delta_f=MAX/n for large batch sizes.

Privacy budget (P2-004):
  - DPAccountant enforces a rolling 24-hour epsilon budget per probe.
  - Default daily_budget=10.0 allows 5 flushes/day at epsilon=2.0 each.
  - Budget exceeded -> PrivacyBudgetExceededError; probe enters sleep
    mode.
  - Window auto-resets after 24 hours via reset_if_needed().
  - Sequential composition: epsilon_total = sum(epsilon per flush).
    With 5 flushes * 2.0 = 10.0 epsilon/day at the default budget.

Collection vs transmission cadence (P2-012):
  - Collection (Aggregator.add_result) may run as often as desired; the
    Aggregator accumulates CanaryResults until a flush consumes them.
  - Transmission (flush) is what spends epsilon. To avoid burning the
    daily budget in the first few rounds, transmissions are paced at
    recommended_flush_interval_seconds(); intervening collection rounds
    simply accumulate into the next DP-noised SignalBatch.

#SG-TRACE: REQ-PRIV-001
#   | assumption: SignalBatch is the sole permitted outbound type
#   | test: test_signal_batch_is_only_outbound_type
#SG-TRACE: REQ-PRIV-002
#   | assumption: client_id UUID rotation per Aggregator instance;
#     Ed25519 binding added in Phase 2
#   | test: test_client_id_is_uuid4
#SG-TRACE: REQ-PRIV-009
#   | assumption: Laplace noise with epsilon=2.0 provides epsilon-DP
#     per flush window; sequential composition tracked by DPAccountant
#   | test: test_dp_noise_perturbs_metrics
#SG-TRACE: REQ-PRIV-011
#   | assumption: DPAccountant 24h window is wall-clock based; clock
#     skew or system hibernation may shorten the effective window
#   | test: test_dp_accountant_resets_after_24h
"""

from __future__ import annotations

import json
import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from probe.canary import CanaryResult


# ---------------------------------------------------------------------------
# Differential privacy constants
# ---------------------------------------------------------------------------

EPSILON: float = 2.0
MAX_OUTPUT_LENGTH: int = 8192

_METRIC_SENSITIVITY: dict[str, float] = {
    "avg_output_length": float(MAX_OUTPUT_LENGTH),
    "json_success_rate": 1.0,
}


# ---------------------------------------------------------------------------
# DP noise helper
# ---------------------------------------------------------------------------


def _laplace_noise(scale: float, rng: random.Random) -> float:
    """Sample Laplace(0, scale) via difference of two Exponentials.

    Uses Laplace(0,b) == Exp(1/b) - Exp(1/b) identity (numerically
    stable; avoids log-of-zero).

    #SG-TRACE: REQ-PRIV-009
    #   | assumption: unseeded Random() in production; seeded for tests
    #     (Phase 2 upgrades to secrets.SystemRandom)
    #   | test: test_laplace_noise_distribution
    """
    lam = 1.0 / scale
    return rng.expovariate(lam) - rng.expovariate(lam)


# ---------------------------------------------------------------------------
# Privacy budget tracking (P2-004)
# ---------------------------------------------------------------------------


class PrivacyBudgetExceededError(RuntimeError):
    """Raised when a flush would exceed the daily epsilon budget.

    The probe must enter sleep mode when this is raised -- no HTTP
    request should be made, and the local aggregator queue must be
    cleared to prevent stale data accumulation.

    #SG-TRACE: REQ-PRIV-011
    #   | assumption: caller handles this exception and clears the
    #     aggregator queue; SDK enforces this in flush()
    #   | test: test_dp_accountant_raises_on_budget_exceeded
    """


class DPAccountant:
    """Rolling 24-hour epsilon privacy budget tracker.

    Tracks cumulative epsilon spend within a 24-hour window and raises
    PrivacyBudgetExceededError when a requested spend would exceed the
    daily_budget ceiling.  The window auto-resets via reset_if_needed().

    Sequential composition guarantee:
      With daily_budget=10.0 and flush epsilon=2.0, a probe may flush
      at most 5 times per 24-hour window before entering sleep mode.
      This bounds the total privacy loss from repeated probing to
      epsilon_total = 10.0 per day per model_tuple.

    Privacy note:
      window_start_time is wall-clock UTC.  Clock skew or system
      hibernation may shorten the effective window.  This is documented
      as KNOWN-LIMIT-004 and is acceptable for Phase 2 MVP.

    Attributes:
        daily_budget: Maximum epsilon allowed per 24-hour window.
        current_spend: Accumulated epsilon spend in the current window.
        window_start_time: UTC datetime when the current window started.

    #SG-TRACE: REQ-PRIV-011
    #   | assumption: DPAccountant is per-ProbeSDK-instance; one budget
    #     tracker per probe deployment (not shared across processes)
    #   | test: test_dp_accountant_spend_accumulates
    """

    def __init__(
        self,
        daily_budget: float = 10.0,
        storage_path: str | None = None,
    ) -> None:
        """Initialise the privacy budget tracker.

        If ``storage_path`` is provided and the file exists, the prior
        ``current_spend`` and ``window_start_time`` are restored from it
        (persistent budget across process restarts).  If the file is
        missing or unreadable, the tracker starts fresh.

        Args:
            daily_budget: Maximum total epsilon allowed per day.
                Default 10.0 permits 5 flushes at epsilon=2.0 each.
            storage_path: Optional path to a JSON file for persistent
                budget state.  None disables persistence (default).

        #SG-TRACE: REQ-PRIV-011
        #   | assumption: storage_path is a local filesystem path
        #     writable by the probe process; no network I/O
        #   | test: test_dp_accountant_persistent_budget
        """
        if daily_budget <= 0:
            raise ValueError(
                f"daily_budget must be positive; got {daily_budget}"
            )
        self.daily_budget: float = daily_budget
        self._storage_path: str | None = storage_path
        # Defaults; may be overwritten by _load() below.
        self.current_spend: float = 0.0
        self.window_start_time: datetime = datetime.now(timezone.utc)
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers (storage_path only; no-op when None)
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        """Write current budget state to storage_path as JSON.

        Serialises ``current_spend`` and ``window_start_time`` (ISO-8601
        UTC string).  Silently swallows all I/O exceptions so a disk
        failure never disrupts probe operation.

        #SG-TRACE: REQ-PRIV-011 | test: test_dp_accountant_persistent_budget
        """
        if self._storage_path is None:
            return
        try:
            payload = {
                "current_spend": self.current_spend,
                "window_start": self.window_start_time.isoformat(),
            }
            tmp = self._storage_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._storage_path)
        except Exception:  # noqa: BLE001
            pass  # fail-safe: never crash the probe on a persist error

    def _load(self) -> None:
        """Restore budget state from storage_path if it exists.

        Reads ``current_spend`` and ``window_start_time`` from the JSON
        file written by ``_persist()``.  Falls back to 0.0 / now if the
        file is absent, unreadable, or contains invalid data.

        #SG-TRACE: REQ-PRIV-011 | test: test_dp_accountant_persistent_budget
        """
        if self._storage_path is None:
            return
        try:
            with open(self._storage_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.current_spend = float(data["current_spend"])
            self.window_start_time = datetime.fromisoformat(
                data["window_start"]
            )
        except Exception:  # noqa: BLE001
            pass  # file absent or corrupt: keep defaults set in __init__

    # ------------------------------------------------------------------

    @property
    def remaining(self) -> float:
        """Epsilon remaining in the current 24-hour window."""
        return self.daily_budget - self.current_spend

    def spend(self, epsilon: float) -> None:
        """Deduct epsilon from the current window budget.

        Raises PrivacyBudgetExceededError BEFORE modifying state if the
        requested spend would exceed daily_budget.  State is only updated
        on success, preserving an all-or-nothing spend contract.

        Args:
            epsilon: The privacy cost to deduct.  Must be positive.

        Raises:
            PrivacyBudgetExceededError: If current_spend + epsilon would
                exceed daily_budget.
            ValueError: If epsilon is not positive.

        #SG-TRACE: REQ-PRIV-011
        #   | assumption: spend() is called after reset_if_needed();
        #     caller is responsible for the reset-before-spend ordering
        #   | test: test_dp_accountant_raises_on_budget_exceeded
        """
        if epsilon <= 0:
            raise ValueError(f"epsilon must be positive; got {epsilon}")
        if self.current_spend + epsilon > self.daily_budget:
            raise PrivacyBudgetExceededError(
                f"Daily privacy budget exhausted: "
                f"spent={self.current_spend:.4f} + requested={epsilon:.4f}"
                f" > budget={self.daily_budget:.4f}. "
                "Probe must enter sleep mode until window resets."
            )
        self.current_spend += epsilon
        self._persist()

    def reset_if_needed(self) -> bool:
        """Reset current_spend if 24 hours have elapsed.

        Idempotent: safe to call on every flush() invocation.  Returns
        True if a reset occurred.

        Returns:
            True if the window was reset, False if not yet due.

        #SG-TRACE: REQ-PRIV-011
        #   | assumption: 24-hour window is wall-clock based (UTC);
        #     see KNOWN-LIMIT-004 for hibernation / clock-skew caveat
        #   | test: test_dp_accountant_resets_after_24h
        """
        now = datetime.now(timezone.utc)
        if (now - self.window_start_time) >= timedelta(hours=24):
            self.current_spend = 0.0
            self.window_start_time = now
            self._persist()
            return True
        return False


# ---------------------------------------------------------------------------
# Flush-cadence pacing (decouples collection cadence from transmission)
# ---------------------------------------------------------------------------


def recommended_flush_interval_seconds(
    daily_budget: float,
    flush_epsilon: float = EPSILON,
    window_seconds: float = 86_400.0,
) -> float:
    """Minimum seconds between flushes to spread the daily epsilon budget
    evenly across a 24-hour window.

    A probe may *collect* canary results as often as it likes -- the
    Aggregator accumulates them between flushes -- but each *flush*
    (transmission) spends ``flush_epsilon`` from the DPAccountant.  Without
    pacing, a short collection interval burns the whole daily budget in the
    first few rounds and the probe then sleeps (PrivacyBudgetExceededError)
    for the rest of the window.  Pacing flushes at this interval keeps the
    probe transmitting continuously, within budget, by batching several
    collection rounds into one DP-noised SignalBatch.

    Parameters
    ----------
    daily_budget:
        Total epsilon allowed per 24-hour window (DPAccountant ceiling).
    flush_epsilon:
        Epsilon spent per transmission.  Defaults to the module EPSILON.
    window_seconds:
        Length of the budget window in seconds.  Defaults to 24 hours.

    Returns
    -------
    float
        Seconds between transmissions.  With daily_budget=10.0 and
        flush_epsilon=2.0 this is 86400 / 5 = 17280s (4.8h).

    #SG-TRACE: REQ-PRIV-012
    #   | assumption: collection and transmission cadence are decoupled;
    #     the Aggregator accumulates results across rounds between flushes,
    #     so spacing transmissions never drops collected data
    #   | test: test_recommended_flush_interval_seconds
    """
    if daily_budget <= 0 or flush_epsilon <= 0:
        raise ValueError(
            "daily_budget and flush_epsilon must be positive; "
            f"got daily_budget={daily_budget}, flush_epsilon={flush_epsilon}"
        )
    max_flushes = max(1, int(daily_budget // flush_epsilon))
    return window_seconds / max_flushes


# ---------------------------------------------------------------------------
# Outbound payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalBatch:
    """The sole outbound object emitted by the SEISMOGRAPH probe.

    Numeric metrics carry Laplace DP noise applied by Aggregator.flush().

    Fields
    ------
    batch_id, client_id, window_start, window_end, model_tuple,
    suite_version, metrics, canary_hashes, result_count:
        Standard probe identity and aggregate metric fields.
    fleet_id:
        Optional tenant identifier for private fleet routing.
        None -> public network path; non-None -> private fleet path.
        Included in the signed canonical JSON payload so the gateway
        can route the batch to the correct CUSUMDetector without
        trusting unsigned headers.

    #SG-TRACE: REQ-PRIV-003
    #   | assumption: frozen=True enforces immutability as privacy
    #     invariant
    #   | test: test_signal_batch_immutable
    #SG-TRACE: REQ-PRIV-004
    #   | assumption: canary_hashes are SHA-256; no raw output derivable
    #   | test: test_signal_batch_no_raw_derivable
    #SG-TRACE: REQ-ENT-001
    #   | assumption: fleet_id is included in signed payload so gateway
    #     routing cannot be spoofed via unsigned headers
    #   | test: test_fleet_id_in_signed_payload
    """

    batch_id: str
    client_id: str
    window_start: str
    window_end: str
    model_tuple: str
    suite_version: str
    metrics: dict[str, float]
    canary_hashes: dict[str, str]
    result_count: int
    fleet_id: str | None = None

    _METRIC_KEYS: frozenset[str] = field(
        default=frozenset(
            {"avg_output_length", "json_success_rate", "result_count"}
        ),
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        try:
            uuid.UUID(self.batch_id)
            uuid.UUID(self.client_id)
        except ValueError as exc:
            raise ValueError(
                f"batch_id and client_id must be valid UUIDs: {exc}"
            ) from exc
        if self.result_count < 1:
            raise ValueError("result_count must be >= 1")
        unknown = set(self.metrics) - self._METRIC_KEYS
        if unknown:
            raise ValueError(
                f"metrics contains unknown keys (possible raw-text "
                f"leakage path): {unknown}"
            )

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict for transmission / logging."""
        return {
            "batch_id": self.batch_id,
            "client_id": self.client_id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "model_tuple": self.model_tuple,
            "suite_version": self.suite_version,
            "metrics": dict(self.metrics),
            "canary_hashes": dict(self.canary_hashes),
            "result_count": self.result_count,
            "fleet_id": self.fleet_id,
        }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class Aggregator:
    """Accumulates CanaryResult objects and produces SignalBatch on flush.

    One instance per probe session. client_id is session-scoped UUID4.

    #SG-TRACE: REQ-PRIV-005
    #   | assumption: one Aggregator per session; rotation at session
    #     boundary
    #   | test: test_aggregator_client_id_stable_within_instance
    #SG-TRACE: REQ-PRIV-006
    #   | assumption: flush clears queue atomically after SignalBatch
    #     validates
    #   | test: test_aggregator_flush_clears_queue
    """

    def __init__(self, _rng: random.Random | None = None) -> None:
        """Initialise.

        Parameters
        ----------
        _rng:
            Seeded Random for deterministic DP noise in tests.
            Omit in production (fresh unseeded Random() used).
        """
        self.client_id: str = str(uuid.uuid4())
        self._pending: dict[str, list[CanaryResult]] = {}
        self._rng: random.Random = (
            _rng if _rng is not None else random.Random()
        )

    def add_result(self, result: CanaryResult) -> None:
        """Stage a CanaryResult for the next flush.

        #SG-TRACE: REQ-PRIV-007
        #   | assumption: CanaryResult carries no raw output;
        #     boundary enforced upstream in execute_canary()
        #   | test: test_add_result_accepts_canary_result
        """
        key = result.model_tuple
        if key not in self._pending:
            self._pending[key] = []
        self._pending[key].append(result)

    def clear_all(self) -> None:
        """Discard all staged results across every model_tuple.

        Called by ProbeSDK.flush() when PrivacyBudgetExceededError is
        raised, to prevent unbounded backlog accumulation in sleep mode.
        Safe to call on an already-empty aggregator (no-op).

        #SG-TRACE: REQ-PRIV-011
        #   | assumption: clear_all() is called only on budget
        #     exhaustion; normal flush path uses Aggregator.flush()
        #     per model_tuple
        #   | test: test_flush_clears_aggregator_on_budget_exceeded
        """
        self._pending.clear()

    def flush(
        self,
        model_tuple: str,
        fleet_id: str | None = None,
    ) -> SignalBatch:
        """Aggregate and DP-noise pending results into a SignalBatch.

        Steps: compute raw metrics -> clamp lengths -> apply Laplace
        noise -> clamp to valid ranges -> construct SignalBatch ->
        atomically clear queue.

        Raises ValueError if no results pending for model_tuple.

        Window width invariant
        ----------------------
        The gateway enforces window_start < window_end (strictly).
        When a batch contains a single CanaryResult, min and max
        timestamps are identical.  In that case, window_end is
        advanced by 1 microsecond to satisfy the invariant while
        preserving the original wall-clock reference.

        Parameters
        ----------
        model_tuple:
            Stream to flush.
        fleet_id:
            Optional tenant identifier passed through to SignalBatch.
            None means public network path; non-None means private
            fleet path.  Included in the signed payload so gateway
            routing cannot be spoofed.

        #SG-TRACE: REQ-PRIV-008
        #   | assumption: queue cleared only after SignalBatch validates
        #   | test: test_flush_atomic_on_validation_error
        #SG-TRACE: REQ-PRIV-009
        #   | assumption: epsilon budget is per-flush window;
        #     DPAccountant in ProbeSDK tracks cumulative spend
        #   | test: test_dp_noise_perturbs_metrics
        #SG-TRACE: REQ-ENT-001
        #   | assumption: fleet_id is passed from ProbeConfig through
        #     ProbeSDK.flush() to Aggregator.flush() to SignalBatch
        #   | test: test_fleet_id_in_signed_payload
        """
        if not self._pending.get(model_tuple):
            raise ValueError(
                f"No pending results for model_tuple={model_tuple!r}."
            )

        results = self._pending[model_tuple]

        window_start = min(r.timestamp for r in results)
        window_end = max(r.timestamp for r in results)

        # Gateway requires window_start < window_end (strictly).
        # Single-result batches produce identical timestamps; advance
        # window_end by 1 microsecond to satisfy the invariant.
        if window_start == window_end:
            _ws_dt = datetime.fromisoformat(window_start)
            window_end = (_ws_dt + timedelta(microseconds=1)).isoformat()

        versions = {r.suite_version for r in results}
        if len(versions) > 1:
            raise ValueError(
                f"Mixed suite versions in batch: {versions}. "
                "Flush each version separately."
            )
        suite_version = versions.pop()

        # Clamp lengths before averaging (bounds sensitivity)
        clamped = [
            float(max(0, min(r.output_length, MAX_OUTPUT_LENGTH)))
            for r in results
        ]
        raw_avg = mean(clamped)
        raw_rate = sum(1 for r in results if r.json_valid) / len(results)

        # Apply Laplace noise
        noised_avg = max(
            0.0,
            raw_avg
            + _laplace_noise(
                _METRIC_SENSITIVITY["avg_output_length"] / EPSILON,
                self._rng,
            ),
        )
        noised_rate = max(
            0.0,
            min(
                1.0,
                raw_rate
                + _laplace_noise(
                    _METRIC_SENSITIVITY["json_success_rate"] / EPSILON,
                    self._rng,
                ),
            ),
        )

        metrics: dict[str, float] = {
            "avg_output_length": round(noised_avg, 4),
            "json_success_rate": round(noised_rate, 4),
            "result_count": float(len(results)),
        }

        canary_hashes: dict[str, str] = {
            r.prompt_id: r.response_hash for r in results
        }

        batch = SignalBatch(
            batch_id=str(uuid.uuid4()),
            client_id=self.client_id,
            window_start=window_start,
            window_end=window_end,
            model_tuple=model_tuple,
            suite_version=suite_version,
            metrics=metrics,
            canary_hashes=canary_hashes,
            result_count=len(results),
            fleet_id=fleet_id,
        )

        del self._pending[model_tuple]
        return batch

    def pending_count(self, model_tuple: str) -> int:
        """Return number of staged results for model_tuple."""
        return len(self._pending.get(model_tuple, []))

    def model_tuples_pending(self) -> list[str]:
        """Return model_tuples with staged results."""
        return [k for k, v in self._pending.items() if v]
