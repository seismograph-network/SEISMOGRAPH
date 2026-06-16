"""
seismograph.engine.detector
============================
Single-metric CUSUM change-point detector with per-(model_tuple,
metric_name) state management.

Algorithm
---------
Page-CUSUM over standardised observations z = (x - mu0) / sigma0:

    S+(n) = max(0, S+(n-1) + z(n) - k)   -- detects positive shifts
    S-(n) = max(0, S-(n-1) - z(n) - k)   -- detects negative shifts

Alert fires when S+(n) > h or S-(n) > h.

Default parameters (h=5.0, k=0.5) are conservative starting points
calibrated for standardised unit-variance observations.  Formal
threshold decisions must be recorded in data/drift_labels/ before any
production deployment.

Baseline phase
--------------
Each (model_tuple, metric_name) stream accumulates
_MetricState.MIN_BASELINE_SAMPLES observations to estimate mu0 and
sigma0 before CUSUM becomes active.  Observations during the baseline
phase never generate alerts.

Architectural notes
-------------------
This module implements single-metric time-series detection.  Cross-
observer agreement gating (ensuring a single org never promotes a
public alert) is handled in engine/correlation.py (AgreementScorer).
The two layers are intentionally separate: detector.py fires per-org
candidate alerts; correlation.py decides whether to surface them.

#SG-TRACE: REQ-ENGINE-006
#   | assumption: CUSUM h and k calibrated offline on labelled
#     drift_labels/ data; defaults are starting points only
#   | test: test_cusum_threshold_calibration
#SG-TRACE: REQ-ENGINE-009
#   | assumption: baseline of MIN_BASELINE_SAMPLES=10 is sufficient
#     for stable mu0/sigma0 estimates for Phase 0 mock data;
#     Phase 1 will tune this on real probe traffic
#   | test: test_cusum_baseline_stability
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------


@dataclass
class DriftAlert:
    """Emitted by CUSUMDetector when a change point is detected.

    Fields
    ------
    timestamp_ns:
        Monotonic nanosecond timestamp of the observation that tripped
        the threshold.
    model_tuple:
        The model identifier being monitored,
        e.g. "openai/gpt-4o@2025-08".
    metric_name:
        Which metric crossed the threshold,
        e.g. "json_success_rate".
    direction:
        "positive" if S+ > h (upward shift detected),
        "negative" if S- > h (downward shift detected).
    cusum_score:
        The CUSUM accumulator value at the time of alert.
    threshold:
        The h value that was exceeded.
    window_count:
        Total observations fed to this (model_tuple, metric_name)
        stream since last reset, including baseline samples.

    #SG-TRACE: REQ-ENGINE-010
    #   | assumption: direction field is sufficient to distinguish
    #     degradation (negative) from unexpected improvement (positive)
    #   | test: test_drift_alert_direction
    """

    timestamp_ns: int
    model_tuple: str
    metric_name: str
    direction: str  # "positive" | "negative"
    cusum_score: float
    threshold: float
    window_count: int


# ---------------------------------------------------------------------------
# Per-stream CUSUM state (private)
# ---------------------------------------------------------------------------


class _MetricState:
    """CUSUM state for one (model_tuple, metric_name) pair.

    Baseline accumulation phase: first MIN_BASELINE_SAMPLES observations
    are used to compute mu0 and sigma0.  No alert can fire during this
    phase.  After baseline is finalised, each subsequent observation
    updates the CUSUM accumulators.

    #SG-TRACE: REQ-ENGINE-009
    #   | assumption: sigma0 clamped to 1.0 when near-zero to prevent
    #     division by zero on constant series (e.g. mock data with
    #     identical values)
    #   | test: test_cusum_constant_series_no_error
    """

    MIN_BASELINE_SAMPLES: int = 10

    def __init__(self, h: float, k: float) -> None:
        self.h = h
        self.k = k
        self._buf: list[float] = []  # baseline accumulation buffer
        self._mu0: float | None = None
        self._sigma0: float | None = None
        self._s_pos: float = 0.0
        self._s_neg: float = 0.0
        self._n: int = 0  # total observations (incl. baseline)

    @property
    def baseline_ready(self) -> bool:
        """True once mu0 and sigma0 have been estimated."""
        return self._mu0 is not None

    def _finalize_baseline(self) -> None:
        """Estimate mu0 and sigma0 from the buffered baseline window."""
        n = len(self._buf)
        mu = sum(self._buf) / n
        variance = (
            sum((x - mu) ** 2 for x in self._buf) / (n - 1) if n > 1 else 0.0
        )
        sigma = math.sqrt(variance)
        # Clamp sigma to prevent division-by-zero on constant series
        if sigma < 1e-9:
            sigma = 1.0
        self._mu0 = mu
        self._sigma0 = sigma

    def update(
        self,
        value: float,
        model_tuple: str,
        metric_name: str,
        timestamp_ns: int,
    ) -> DriftAlert | None:
        """Process one observation.

        Returns a DriftAlert if a threshold is exceeded, else None.
        During the baseline phase always returns None.
        """
        self._n += 1

        # ---- Baseline accumulation phase ---------------------------------
        if not self.baseline_ready:
            self._buf.append(value)
            if len(self._buf) >= self.MIN_BASELINE_SAMPLES:
                self._finalize_baseline()
            return None

        # ---- CUSUM update ------------------------------------------------
        assert self._mu0 is not None and self._sigma0 is not None
        z = (value - self._mu0) / self._sigma0

        # Page-CUSUM accumulators
        self._s_pos = max(0.0, self._s_pos + z - self.k)
        self._s_neg = max(0.0, self._s_neg - z - self.k)

        # Check for positive shift
        if self._s_pos > self.h:
            return DriftAlert(
                timestamp_ns=timestamp_ns,
                model_tuple=model_tuple,
                metric_name=metric_name,
                direction="positive",
                cusum_score=self._s_pos,
                threshold=self.h,
                window_count=self._n,
            )

        # Check for negative shift
        if self._s_neg > self.h:
            return DriftAlert(
                timestamp_ns=timestamp_ns,
                model_tuple=model_tuple,
                metric_name=metric_name,
                direction="negative",
                cusum_score=self._s_neg,
                threshold=self.h,
                window_count=self._n,
            )

        return None


# ---------------------------------------------------------------------------
# CUSUMDetector -- public API
# ---------------------------------------------------------------------------


class CUSUMDetector:
    """Multi-stream CUSUM detector.

    Maintains independent _MetricState instances keyed by
    (model_tuple, metric_name).  Each stream has its own baseline,
    mu0/sigma0 estimates, and S+/S- accumulators.

    Usage
    -----
    detector = CUSUMDetector(h=5.0, k=0.5)
    alert = detector.update("openai/gpt-4o@2025-08",
                            "json_success_rate",
                            0.65)
    if alert:
        # hand alert to AgreementScorer in correlation.py

    #SG-TRACE: REQ-ENGINE-006
    #   | assumption: h=5.0 and k=0.5 are reasonable defaults for
    #     standardised observations; must be tuned for production
    #   | test: test_cusum_stable_window_no_false_positive
    #SG-TRACE: REQ-ENGINE-011
    #   | assumption: reset() is called by the caller after a confirmed
    #     public alert to restart accumulation post-changepoint
    #   | test: test_cusum_reset_clears_state
    """

    def __init__(
        self,
        h: float = 5.0,
        k: float = 0.5,
        baseline_samples: int | None = None,
    ) -> None:
        """Initialise the detector.

        Parameters
        ----------
        h:
            Detection threshold.  Alert fires when S+ > h or S- > h.
        k:
            Slack parameter (allowable drift before accumulation
            starts).  Typically 0.5 standard deviations.
        baseline_samples:
            Override the per-stream baseline window size.  Defaults to
            _MetricState.MIN_BASELINE_SAMPLES (10).  Use a larger value
            when the expected inter-observation noise is high relative
            to the drift signal (e.g., daily probes over 30 days).
        """
        self.h = h
        self.k = k
        self._baseline_samples: int = (
            baseline_samples
            if baseline_samples is not None
            else _MetricState.MIN_BASELINE_SAMPLES
        )
        self._states: dict[tuple[str, str], _MetricState] = {}

    def update(
        self,
        model_tuple: str,
        metric_name: str,
        value: float,
        timestamp_ns: int | None = None,
    ) -> DriftAlert | None:
        """Feed one scalar observation to the appropriate stream.

        Creates a new stream on first call for this
        (model_tuple, metric_name) pair.

        Parameters
        ----------
        model_tuple:
            Model identifier, e.g. "openai/gpt-4o@2025-08".
        metric_name:
            Metric being tracked, e.g. "json_success_rate".
        value:
            The observed metric value.
        timestamp_ns:
            Monotonic nanosecond timestamp.  Defaults to
            time.monotonic_ns() if not supplied.

        Returns
        -------
        DriftAlert or None
            Alert if the CUSUM threshold was exceeded; None otherwise.
            None is always returned during the baseline phase.
        """
        key = (model_tuple, metric_name)
        if key not in self._states:
            state = _MetricState(h=self.h, k=self.k)
            state.MIN_BASELINE_SAMPLES = self._baseline_samples
            self._states[key] = state
        ts = timestamp_ns if timestamp_ns is not None else time.monotonic_ns()
        return self._states[key].update(value, model_tuple, metric_name, ts)

    def reset(
        self,
        model_tuple: str,
        metric_name: str | None = None,
    ) -> None:
        """Reset CUSUM state for a model_tuple.

        Parameters
        ----------
        model_tuple:
            Which model tuple to reset.
        metric_name:
            If provided, reset only this metric stream.
            If None, reset all streams for model_tuple.
        """
        if metric_name is not None:
            self._states.pop((model_tuple, metric_name), None)
        else:
            keys = [k for k in self._states if k[0] == model_tuple]
            for k in keys:
                del self._states[k]

    @property
    def tracked_streams(self) -> list[tuple[str, str]]:
        """Return all (model_tuple, metric_name) pairs being tracked."""
        return list(self._states.keys())
