# CUSUM Threshold Calibration Record
# Phase 0 -- Mock data only
# Date: 2026-06-10
# Agent: Seismo

## Parameters
- h (threshold): 5.0
- k (slack): 0.5

## Rationale
Default values for standardised unit-variance observations (Adams & MacKay
convention). h=5.0 corresponds to approximately ARL_0 ~= 500 for i.i.d.
Gaussian noise at k=0.5 (from Page 1954 CUSUM tables).

## Baseline accumulation
- MIN_BASELINE_SAMPLES = 10
- Baseline used to estimate mu0 and sigma0 per (model_tuple, metric_name)
- sigma0 clamped to 1.0 if near-zero (constant series guard)

## Verified behaviour (2026-06-10)
- Stable window (15 obs at baseline distribution): 0 false positives
- 30% absolute drop in json_success_rate: alert at window_count=26
  (first CUSUM observation is count 11, alert at count 26 = 16 post-baseline)
- Positive shift (output_length doubles): alert detected
- Single-org noise burst: NOT promoted to public alert (AgreementScorer quorum=2)

## Known limitations
- Calibrated on mock/synthetic data only. Phase 1 must recalibrate on
  real probe traffic before production deployment.
- Sequential composition of epsilon budgets not yet tracked (REQ-PRIV-010).
- QUORUM_MIN=2 is a conservative Phase 0 default; Phase 1 open decision.

## Status: DEFERRED -- requires real probe traffic for production tuning
