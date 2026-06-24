"""
seismograph.engine.correlation
================================
Change-point detection stubs and cross-observer agreement scoring for
semantic drift signals.

This module has two distinct roles:

1. **Interface stubs** -- CUSUMDetector is preserved here as a typed
   interface contract.  The LIVE Page-CUSUM implementation is in
   engine/detector.py.  Do NOT import CUSUMDetector from this module
   for production use.

2. **AgreementScorer (LIVE)** -- Cross-observer quorum gate.  A single-org
   signal is NEVER promoted to a public drift alert.  A minimum of
   QUORUM_MIN distinct org_ids must agree before promote_to_public_alert()
   returns a non-None count.

3. **BayesianOnlineDetector (LIVE)** -- Adams & MacKay 2007 BOCD with
   Normal-Inverse-Gamma conjugate prior.  Phase 0-005 implementation.

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

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FeatureVector:
    """A privacy-preserving feature vector emitted by a probe.

    Contains distributional statistics and/or DP-noised aggregates only.
    Raw prompt text and raw model output are NEVER present here.

    Attributes:
        org_id: Pseudonymous probe identifier bound to an Ed25519 public key.
        model_tuple: Composite model identifier, e.g. "openai/gpt-4o@2025-08".
        suite_version_hash: SHA-256 hash of the canary suite definition.
        values: Ordered list of feature metric values (DP-noised floats).
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

    Attributes:
        model_tuple: Composite model identifier this result relates to.
        change_detected: True if the detector crossed its alert threshold.
        score: The detector statistic at evaluation time.
        threshold: The detection threshold in effect.
        contributing_orgs: Pseudonymous org_ids behind this signal.

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

    WARNING: raises NotImplementedError on every call.
    The LIVE Page-CUSUM implementation is in engine/detector.py.

    #SG-TRACE: REQ-ENGINE-006
    #   | assumption: CUSUM threshold calibrated offline on labelled data
    #   | test: test_cusum_threshold_calibration
    """

    def __init__(
        self,
        threshold: float = 5.0,
        drift_delta: float = 0.5,
    ) -> None:
        self.threshold: float = threshold
        self.drift_delta: float = drift_delta
        self._cusum_pos: float = 0.0
        self._cusum_neg: float = 0.0

    def update(self, value: float) -> bool:
        """Raises NotImplementedError. Use engine.detector.CUSUMDetector."""
        raise NotImplementedError(
            "CUSUMDetector.update -- stub only. "
            "Use engine.detector.CUSUMDetector for production code."
        )

    def reset(self) -> None:
        """Reset S+ and S- accumulators to zero."""
        self._cusum_pos = 0.0
        self._cusum_neg = 0.0


# ---------------------------------------------------------------------------
# BayesianOnlineDetector -- LIVE (Adams & MacKay 2007)
# ---------------------------------------------------------------------------


class BayesianOnlineDetector:
    """Bayesian online change-point detection (Adams & MacKay 2007).

    Maintains a posterior distribution over run lengths r_t (time steps
    since the last change point) using a Normal-Inverse-Gamma (NIG)
    conjugate prior and a constant hazard rate h.

    **Key design invariant (correctness):**
    The changepoint mass uses the *prior* predictive P(x_t | NIG prior),
    while the growth mass uses each run's accumulated posterior predictive.
    This ensures that an observation far from the learned run distribution
    but plausible under the prior raises P(r_t=0) toward 1.0.

    Concretely, the update recursion is:

        P(r_t=0 | x_{1:t}) proportional to
            h * P(x_t | mu0, kappa0, alpha0, beta0)   [fresh prior]

        P(r_t=r+1 | x_{1:t}) proportional to
            P(r_{t-1}=r | x_{1:t-1}) * (1-h)
                * P(x_t | NIG posterior after r obs)

    The predictive P(x_t | NIG params) is Student-t with:
        nu      = 2 * alpha
        loc     = mu
        scale^2 = beta * (kappa+1) / (alpha * kappa)

    Numerical stability: arithmetic uses direct probability space with
    renormalisation at each step; hypotheses below _PRUNE_THRESHOLD are
    discarded to cap memory at O(T/pruning_rate).

    Reference:
        Adams, R. P., & MacKay, D. J. C. (2007). Bayesian Online
        Changepoint Detection. arXiv:0710.3742.

    Default prior (alpha0=2.0, beta0=0.01):
        E[sigma^2] = beta0/(alpha0-1) = 0.01 -> prior std ~ 0.1.
        Suitable for normalised metrics (json_success_rate, etc.).
        Tune beta0 upward for metrics with higher expected variance.

    #SG-TRACE: REQ-ENGINE-007
    #   | assumption: hazard rate is constant; prior is Normal-Inverse-Gamma
    #   | test: test_bayesian_online_detects_mean_shift
    """

    _PRUNE_THRESHOLD: float = 1e-10
    """Hypotheses with posterior probability below this value are discarded."""

    def __init__(
        self,
        hazard_rate: float = 1.0 / 200.0,
        mu0: float = 0.0,
        kappa0: float = 1.0,
        alpha0: float = 2.0,
        beta0: float = 0.01,
        alert_threshold: float = 0.5,
    ) -> None:
        """Initialise the Bayesian online detector.

        Args:
            hazard_rate: Prior probability that any given time step is a
                change point.  Default 1/200 (one change per 200 obs).
            mu0: NIG prior mean.  Set to the expected baseline metric value.
            kappa0: NIG prior pseudo-count for the mean (> 0).
            alpha0: NIG prior shape parameter (> 0).  Default 2.0 gives a
                proper prior with finite E[sigma^2].
            beta0: NIG prior scale parameter (> 0).  Controls expected
                process variance: E[sigma^2] = beta0/(alpha0-1).
                Default 0.01 -> prior std ~ 0.1, suitable for rates in [0,1].
            alert_threshold: Posterior P(changepoint) at or above which a
                change is considered detected.  Stored for caller inspection;
                update() always returns the raw probability.

        Raises:
            ValueError: If alpha0, beta0, or kappa0 are not positive, or
                if hazard_rate is not in the open interval (0, 1).
        """
        if alpha0 <= 0.0 or beta0 <= 0.0 or kappa0 <= 0.0:
            raise ValueError("alpha0, beta0, kappa0 must be > 0")
        if not (0.0 < hazard_rate < 1.0):
            raise ValueError("hazard_rate must be in (0, 1)")

        self.hazard_rate: float = hazard_rate
        self.alert_threshold: float = alert_threshold

        # NIG prior hyperparameters -- fixed, used to seed each new segment.
        self._mu0: float = mu0
        self._kappa0: float = kappa0
        self._alpha0: float = alpha0
        self._beta0: float = beta0

        # Prior predictive scale^2 (precomputed, constant across all steps).
        self._prior_nu: float = 2.0 * alpha0
        self._prior_scale_sq: float = (
            beta0 * (kappa0 + 1.0) / (alpha0 * kappa0)
        )

        # Parallel arrays: index k = run-length hypothesis r_t=k.
        # NIG sufficient statistics accumulated over the run of length k.
        # Initialised with a single hypothesis r_0=0 and prior params.
        self._run_probs: list[float] = [1.0]
        self._mu: list[float] = [mu0]
        self._kappa: list[float] = [kappa0]
        self._alpha: list[float] = [alpha0]
        self._beta: list[float] = [beta0]

    @staticmethod
    def _student_t_logpdf(
        x: float,
        nu: float,
        loc: float,
        scale_sq: float,
    ) -> float:
        """Log-PDF of Student-t(nu, loc, scale_sq) at x.

        Args:
            x: Observation value.
            nu: Degrees of freedom (> 0).
            loc: Location parameter.
            scale_sq: Squared scale parameter (> 0).

        Returns:
            Log probability density (float).
        """
        z = (x - loc) ** 2 / scale_sq
        return (
            math.lgamma((nu + 1.0) / 2.0)
            - math.lgamma(nu / 2.0)
            - 0.5 * math.log(math.pi * nu * scale_sq)
            - (nu + 1.0) / 2.0 * math.log(1.0 + z / nu)
        )

    def _run_predictive(self, x: float, idx: int) -> float:
        """P(x | NIG posterior for run hypothesis at index idx).

        Returns probability (not log), clamped at exp(-700) for stability.
        """
        kappa = self._kappa[idx]
        alpha = self._alpha[idx]
        nu = 2.0 * alpha
        scale_sq = self._beta[idx] * (kappa + 1.0) / (alpha * kappa)
        lp = self._student_t_logpdf(x, nu, self._mu[idx], scale_sq)
        return math.exp(max(lp, -700.0))

    @staticmethod
    def _nig_update(
        x: float,
        mu: float,
        kappa: float,
        alpha: float,
        beta: float,
    ) -> tuple[float, float, float, float]:
        """Closed-form NIG posterior after observing x.

        Args:
            x: New scalar observation.
            mu, kappa, alpha, beta: Current NIG hyperparameters.

        Returns:
            Tuple (mu_new, kappa_new, alpha_new, beta_new).
        """
        kappa_new = kappa + 1.0
        mu_new = (kappa * mu + x) / kappa_new
        alpha_new = alpha + 0.5
        beta_new = beta + kappa * (x - mu) ** 2 / (2.0 * kappa_new)
        return mu_new, kappa_new, alpha_new, beta_new

    def update(self, value: float) -> float:
        """Return posterior probability of a change point at this step.

        Executes one step of the BOCD recursion:
          1. Changepoint mass = h * P(x_t | PRIOR) -- fresh-start predictive.
          2. Growth mass[r] = P(r_{t-1}=r) * (1-h) * P(x_t | run[r] stats).
          3. Normalise; prune hypotheses below _PRUNE_THRESHOLD.
          4. Update NIG sufficient statistics for all surviving hypotheses.

        Using the prior predictive for the changepoint hypothesis (not the
        run-length predictive) is the key correctness invariant: when x_t is
        very unlikely under the learned tight distribution but plausible under
        the wider prior, P(r_t=0) rises toward 1.0.

        Args:
            value: The next scalar observation in the metric stream.

        Returns:
            Posterior probability in [0.0, 1.0] that a change point
            occurred at this time step.  Values above alert_threshold
            indicate a detected regime shift.

        #SG-TRACE: REQ-ENGINE-007
        #   | test: test_bayesian_online_detects_mean_shift
        """
        x = value
        h = self.hazard_rate
        n = len(self._run_probs)

        # --- 1. Changepoint mass: h * P(x | PRIOR) ---
        prior_lp = self._student_t_logpdf(
            x, self._prior_nu, self._mu0, self._prior_scale_sq
        )
        cp_prob = h * math.exp(max(prior_lp, -700.0))

        # --- 2. Growth mass per run-length hypothesis ---
        grow_probs = [
            self._run_probs[r] * (1.0 - h) * self._run_predictive(x, r)
            for r in range(n)
        ]

        # --- 3. Assemble new run-length distribution ---
        new_probs = [cp_prob] + grow_probs

        # --- 4. Normalise ---
        total = sum(new_probs)
        if total <= 0.0:
            # Numerical underflow: full reset to prior
            self._run_probs = [1.0]
            self._mu = [self._mu0]
            self._kappa = [self._kappa0]
            self._alpha = [self._alpha0]
            self._beta = [self._beta0]
            return 0.0

        new_probs = [p / total for p in new_probs]

        # --- 5. Update NIG sufficient statistics ---
        # Index 0 (changepoint): update PRIOR with x (first obs in new segment)
        mn0, kn0, an0, bn0 = self._nig_update(
            x, self._mu0, self._kappa0, self._alpha0, self._beta0
        )
        new_mu = [mn0]
        new_kappa = [kn0]
        new_alpha = [an0]
        new_beta = [bn0]

        # Index r+1 (growth): update existing run stats with x
        for r in range(n):
            mn, kn, an, bn = self._nig_update(
                x, self._mu[r], self._kappa[r], self._alpha[r], self._beta[r]
            )
            new_mu.append(mn)
            new_kappa.append(kn)
            new_alpha.append(an)
            new_beta.append(bn)

        # --- 6. Prune low-probability hypotheses ---
        keep = [
            i for i, p in enumerate(new_probs)
            if p >= self._PRUNE_THRESHOLD
        ]
        if not keep:
            keep = [0]  # always retain changepoint hypothesis

        self._run_probs = [new_probs[i] for i in keep]
        self._mu = [new_mu[i] for i in keep]
        self._kappa = [new_kappa[i] for i in keep]
        self._alpha = [new_alpha[i] for i in keep]
        self._beta = [new_beta[i] for i in keep]

        return new_probs[0]


# ---------------------------------------------------------------------------
# AgreementScorer -- LIVE
# ---------------------------------------------------------------------------


class AgreementScorer:
    """Gates drift alerts behind cross-observer quorum.

    A single-org signal is NEVER promoted to a public drift alert.
    A minimum of QUORUM_MIN distinct org_ids must independently signal
    a change before promote_to_public_alert() returns a non-None count.

    #SG-TRACE: REQ-ENGINE-008
    #   | assumption: org_id deduplication is done by contributing_orgs
    #     set logic here; Sybil resistance handled upstream
    #   | test: test_agreement_scorer_single_org_blocked
    """

    QUORUM_MIN: int = 2
    """Minimum distinct agreeing orgs required for a public alert.

    Phase 1 open decision: raise to 3?  Requires Tatiana approval.
    """

    def __init__(self, quorum: int | None = None) -> None:
        """Initialise the agreement scorer.

        Args:
            quorum: Override for the minimum quorum size.
                Defaults to QUORUM_MIN (2) if None.
        """
        self.quorum: int = quorum if quorum is not None else self.QUORUM_MIN
        self._pending: dict[str, list[ChangePointResult]] = {}

    def ingest(self, result: ChangePointResult) -> None:
        """Record a change-point result from a contributing org.

        Args:
            result: A ChangePointResult from any detector.
        """
        key: str = result.model_tuple
        if key not in self._pending:
            self._pending[key] = []
        self._pending[key].append(result)

    def promote_to_public_alert(self, model_tuple: str) -> int | None:
        """Return org count if quorum met, else None.

        Args:
            model_tuple: The composite model identifier to evaluate.

        Returns:
            int count of distinct agreeing orgs if >= self.quorum, else None.

        #SG-TRACE: REQ-ENGINE-008
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

        Args:
            model_tuple: The model identifier to discard pending results for.
        """
        self._pending.pop(model_tuple, None)
