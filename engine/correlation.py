"""
seismograph.engine.correlation
================================
Change-point detection stubs and cross-observer agreement scoring for
semantic drift signals.

This module has two distinct roles:

1. **Interface stubs** -- CUSUMDetector and BayesianOnlineDetector are
   preserved here as typed interface contracts.  The LIVE Page-CUSUM
   implementation is in engine/detector.py (CUSUMDetector there is the
   production class used in all Phase 0 verification and the backtest).
   Do NOT import the CUSUMDetector from this module for production use.

2. **AgreementScorer (LIVE)** -- Cross-observer quorum gate.  A single-org
   signal is NEVER promoted to a public drift alert.  A minimum of
   QUORUM_MIN distinct org_ids must agree before promote_to_public_alert()
   returns a non-None count.

Algorithms (Phase 0 stubs -- implementations wired in Phase 1/2):
  - CUSUMDetector: interface stub only; live code in engine/detector.py.
  - BayesianOnlineDetector: Bayesian online change-point detection
    (Adams & MacKay 2007).  Phase 1 implementation target.
  - AgreementScorer: cross-observer agreement gate; live for Phase 0.

All threshold decisions must be documented as labelled data in
data/drift_labels/ before any production deployment.

#SG-TRACE: REQ-ENGINE-002
#   | assumption: feature vectors arrive as float arrays with fixed
#     dimensionality per model tuple
#   | test: test_correlation_vector_shape
#SG-TRACE: REQ-ENGINE-003
#   | assumption: cross-observer quorum >= 2 orgs sufficient for Phase 0
#   | test: test_agreement_scorer_quorum
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FeatureVector:
    """A privacy-preserving feature vector emitted by a probe.

    Contains distributional statistics and/or DP-noised aggregates only.
    Raw prompt text and raw model output are NEVER present here -- they
    are destroyed at the Aggregator boundary in probe/privacy.py.

    Attributes:
        org_id: Pseudonymous probe identifier bound to an Ed25519 public
            key.  The engine never learns the underlying organisation
            identity from this field alone.
        model_tuple: Composite model identifier, e.g.
            "openai/gpt-4o@2025-08".
        suite_version_hash: SHA-256 hash of the canary suite definition
            used to generate this vector.  Allows the engine to compare
            only like-for-like probe runs.
        values: Ordered list of feature metric values (DP-noised floats).
            Dimensionality is fixed per model_tuple for a given suite
            version.
        timestamp_ns: Probe wall-clock time in monotonic nanoseconds.

    #SG-TRACE: REQ-ENGINE-004
    #   | assumption: org_id is pseudonymous; actual org identity held
    #     only by ingestion gateway key registry
    #   | test: test_feature_vector_no_raw_content
    """

    org_id: str
    model_tuple: str
    suite_version_hash: str
    values: list[float]
    timestamp_ns: int


@dataclass
class ChangePointResult:
    """Result of a change-point detection run for one model tuple.

    Produced by CUSUMDetector (engine/detector.py) and consumed by
    AgreementScorer.  The change_detected flag is intentionally
    conservative -- false-negatives are preferred over false-positives
    in Phase 0 to avoid alert fatigue.

    Attributes:
        model_tuple: Composite model identifier this result relates to.
        change_detected: True if the detector crossed its alert
            threshold for this stream.
        score: The detector statistic at the time of evaluation (e.g.
            CUSUM S- or S+ value, or Bayesian posterior probability).
        threshold: The detection threshold that was in effect.  Stored
            here so downstream consumers can gauge signal strength.
        contributing_orgs: Pseudonymous org_ids whose signals contributed
            to this result.  Used by AgreementScorer to count distinct
            observers.

    #SG-TRACE: REQ-ENGINE-005
    #   | assumption: change_detected is conservative (false-negative
    #     preferred over false-positive at Phase 0 calibration)
    #   | test: test_cusum_no_false_positive_stable_window
    """

    model_tuple: str
    change_detected: bool
    score: float
    threshold: float
    contributing_orgs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CUSUMDetector -- INTERFACE STUB ONLY
# ---------------------------------------------------------------------------


class CUSUMDetector:
    """Cumulative-sum change-point detector -- INTERFACE STUB.

    WARNING: This class is an interface contract only.  It raises
    NotImplementedError on every call.

    The LIVE Page-CUSUM implementation is in engine/detector.py.
    Import from engine.detector for all production and test code.

    This stub is retained here to:
      (a) document the interface contract for correlation.py consumers,
      (b) allow type-annotation of correlation-layer code without a
          circular import dependency, and
      (c) serve as a placeholder until the two engine modules are
          consolidated in Phase 1.

    See engine/detector.py for full algorithm documentation, including
    the Page-CUSUM formulation, baseline estimation, sigma clamping,
    and the Phase 0 calibration rationale (h=5.0, k=0.5,
    baseline_samples=30).

    #SG-TRACE: REQ-ENGINE-006
    #   | assumption: CUSUM threshold calibrated offline on labelled
    #     drift_labels/ data; defaults are starting points only
    #   | test: test_cusum_threshold_calibration
    """

    def __init__(
        self,
        threshold: float = 5.0,
        drift_delta: float = 0.5,
    ) -> None:
        """Initialise CUSUM accumulators.

        Args:
            threshold: Detection threshold h.  Alert fires when
                S+ or S- exceeds this value.
            drift_delta: Allowance parameter k.  Suppresses small
                fluctuations below this magnitude.
        """
        self.threshold: float = threshold
        self.drift_delta: float = drift_delta
        self._cusum_pos: float = 0.0
        self._cusum_neg: float = 0.0

    def update(self, value: float) -> bool:
        """Update CUSUM with a new scalar observation.

        Args:
            value: The next scalar observation in the metric stream.

        Returns:
            True if a change point is detected (i.e. S+ or S- > h).

        Raises:
            NotImplementedError: Always.  Use engine.detector.CUSUMDetector
                for the live implementation.
        """
        raise NotImplementedError(
            "CUSUMDetector.update -- stub only. "
            "Use engine.detector.CUSUMDetector for production code."
        )

    def reset(self) -> None:
        """Reset S+ and S- accumulators to zero.

        Safe to call at any point; resets both accumulators regardless
        of whether the baseline phase is complete.
        """
        self._cusum_pos = 0.0
        self._cusum_neg = 0.0


# ---------------------------------------------------------------------------
# BayesianOnlineDetector -- INTERFACE STUB (Phase 1)
# ---------------------------------------------------------------------------


class BayesianOnlineDetector:
    """Bayesian online change-point detection (Adams & MacKay 2007).

    Tracks the posterior probability of a change point at each time step
    using a Gaussian-Gaussian conjugate model and a constant hazard rate.

    Phase 1 implementation target.  All methods raise NotImplementedError.

    Reference:
        Adams, R. P., & MacKay, D. J. C. (2007). Bayesian Online
        Changepoint Detection. arXiv:0710.3742.

    #SG-TRACE: REQ-ENGINE-007
    #   | assumption: hazard rate is constant; prior is Gaussian-Gaussian
    #     conjugate (normal-inverse-gamma)
    #   | test: test_bayesian_online_prior_posterior
    """

    def __init__(self, hazard_rate: float = 1.0 / 200.0) -> None:
        """Initialise the Bayesian online detector.

        Args:
            hazard_rate: Prior probability that any given time step is a
                change point.  Default 1/200 encodes a prior expectation
                of one change point per 200 observations.
        """
        self.hazard_rate: float = hazard_rate

    def update(self, value: float) -> float:
        """Return posterior probability of a change point at this step.

        Args:
            value: The next scalar observation in the metric stream.

        Returns:
            Posterior probability in [0.0, 1.0] that a change point
            occurred at this time step.

        Raises:
            NotImplementedError: Always.  Phase 1 implementation pending.
        """
        raise NotImplementedError(
            "BayesianOnlineDetector.update -- Phase 1 implementation pending."
        )


# ---------------------------------------------------------------------------
# AgreementScorer -- LIVE
# ---------------------------------------------------------------------------


class AgreementScorer:
    """Gates drift alerts behind cross-observer quorum.

    A single-org signal is NEVER promoted to a public drift alert.
    A minimum of QUORUM_MIN distinct org_ids must independently signal
    a change before promote_to_public_alert() returns a non-None count.

    This is the live, tested implementation used in Phase 0 adversarial
    verification (ADV1, ADV2, ADV3 in the test suite).

    Design invariants:
      - Privacy: org_ids are pseudonymous keys; AgreementScorer never
        learns the real organisational identity behind them.
      - Monotonic ingestion: ingest() appends results; there is no method
        to remove a previously ingested result within a round.
      - Stateless across rounds: clear() must be called after each alert
        decision to reset state for the next scoring round.

    #SG-TRACE: REQ-ENGINE-008
    #   | assumption: org_id deduplication is done by contributing_orgs
    #     set logic here; replay/Sybil resistance handled upstream in
    #     gateway/ingest.py and Phase 2 reputation weighting
    #   | test: test_agreement_scorer_single_org_blocked
    """

    QUORUM_MIN: int = 2
    """Minimum number of distinct agreeing orgs required for a public alert.

    Phase 1 open decision: raise to 3?  Requires Tatiana approval before
    any change to this constant.
    """

    def __init__(self, quorum: int | None = None) -> None:
        """Initialise the agreement scorer.

        Args:
            quorum: Override for the minimum quorum size.  If None,
                defaults to QUORUM_MIN (currently 2).
        """
        self.quorum: int = quorum if quorum is not None else self.QUORUM_MIN
        self._pending: dict[str, list[ChangePointResult]] = {}

    def ingest(self, result: ChangePointResult) -> None:
        """Record a change-point result from a contributing org.

        Appends result to the pending list for result.model_tuple.
        Multiple calls with the same model_tuple accumulate results;
        they are not deduplicated here -- deduplication is performed
        by the contributing_orgs set logic in promote_to_public_alert().

        Args:
            result: A ChangePointResult from any detector (CUSUM or
                Bayesian).  The result.contributing_orgs list identifies
                the org(s) behind this signal.

        Returns:
            None.
        """
        key: str = result.model_tuple
        if key not in self._pending:
            self._pending[key] = []
        self._pending[key].append(result)

    def promote_to_public_alert(self, model_tuple: str) -> int | None:
        """Return org count if quorum met, else None.

        Collects all ingested ChangePointResult objects for model_tuple,
        filters to those with change_detected == True, unions the
        contributing_orgs sets, and returns the distinct org count only
        if it meets or exceeds self.quorum.

        A single-org signal -- regardless of score strength -- always
        returns None.

        Args:
            model_tuple: The composite model identifier to evaluate,
                e.g. "openai/gpt-4o@2025-08".

        Returns:
            int count of distinct agreeing orgs if >= self.quorum, else None.
            Returns None (not raises) if model_tuple has no pending results.

        #SG-TRACE: REQ-ENGINE-008
        #   | assumption: Sybil probe cannot forge distinct org_ids because
        #     gateway validates Ed25519 signatures per client_id in Phase 2
        #   | test: test_single_org_noise_blocked
        """
        if model_tuple not in self._pending:
            return None
        results: list[ChangePointResult] = self._pending[model_tuple]
        agreeing_orgs: set[str] = set()
        for r in results:
            if r.change_detected:
                agreeing_orgs.update(r.contributing_orgs)
        if len(agreeing_orgs) >= self.quorum:
            return len(agreeing_orgs)
        return None

    def clear(self, model_tuple: str) -> None:
        """Clear pending results for a model tuple after an alert decision.

        Must be called after promote_to_public_alert() returns non-None to
        reset state for the next scoring round.  Safe to call even if
        model_tuple has no pending results (no-op in that case).

        Args:
            model_tuple: The model identifier whose pending results
                should be discarded.

        Returns:
            None.
        """
        self._pending.pop(model_tuple, None)
