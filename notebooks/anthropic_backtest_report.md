# SEISMOGRAPH Backtest Report
## Anthropic Claude 3.5 Sonnet -- Aug-Sep 2025

**Generated:** 2026-06-10 | **Seed:** 42 | **CUSUM:** h=5.0, k=0.5

---

## The Question

In Q3 2025, Anthropic published a postmortem describing a silent
degradation in Claude 3.5 Sonnet caused by a load-balancer
misconfiguration that misrouted a fraction of requests to an
incompatible model configuration.

The bug was introduced around **2025-08-05** (~0.8% of traffic).
By **2025-08-29** the misrouting rate had escalated to ~16%,
producing erratic output lengths consistent with a 1M-token context
window mismatch. The degradation was publicly disclosed on
**2025-09-17**.

**Would a deployed SEISMOGRAPH probe have detected this earlier?**

---

## Simulation Setup

Daily canary probes synthesized with seeded Gaussian noise:

| Phase | Date range | json_success_rate | sigma |
|---|---|---|---|
| Baseline | 2025-07-01 -- 2025-08-04 | 0.99 | 0.006 |
| Phase 1 (0.8%) | 2025-08-05 -- 2025-08-28 | 0.982 | 0.006 |
| Phase 2 (16%) | 2025-08-29 -- 2025-09-17 | 0.84 | 0.015 |

Detector: Page-CUSUM, S+/S- per (model_tuple, metric_name) stream.
Baseline estimated from first 10 daily observations.
Alert threshold: h=5.0 standard deviations.

---

## Result

| | |
|---|---|
| **First DriftAlert** | **2025-08-10** |
| Metric | `json_success_rate` |
| Direction | negative |
| CUSUM S- score | 7.2784 (threshold h=5.0) |
| Baseline mu0 | 0.9903 |
| Baseline sigma0 | 0.00437 |
| Detected in Phase 1 (subtle 0.8%) | True |
| **Lead over escalation** | **19 days** |
| **Lead over postmortem** | **38 days** |

> SEISMOGRAPH would have alerted on **2025-08-10**,
> **38 days before** the official postmortem.
> The signal was detected in the **subtle Phase 1** window
> (19 days before the visible escalation).

---

## CUSUM Trace (detection window)

| Date | Phase | json_rate | avg_len | S- | S+ | |
|---|---|---|---|---|---|---|
| 2025-08-03 | Baseline | 0.9902 | 455.1 | 0.000 | 0.228 |  |
| 2025-08-04 | Baseline | 0.9881 | 464.5 | 0.000 | 0.000 |  |
| 2025-08-05 | Phase 1 (0.8%) | 0.9855 | 505.0 | 0.598 | 0.000 |  |
| 2025-08-06 | Phase 1 (0.8%) | 0.9857 | 431.8 | 1.142 | 0.000 |  |
| 2025-08-07 | Phase 1 (0.8%) | 0.9786 | 426.2 | 3.309 | 0.000 |  |
| 2025-08-08 | Phase 1 (0.8%) | 0.9877 | 432.8 | 3.396 | 0.000 |  |
| 2025-08-09 | Phase 1 (0.8%) | 0.9816 | 465.7 | 4.889 | 0.000 |  |
| 2025-08-10 | Phase 1 (0.8%) | 0.9777 | 439.7 | 7.278 | 0.000 | **ALERT** |
| 2025-08-11 | Phase 1 (0.8%) | 0.971 | 419.9 | 11.204 | 0.000 |  |
| 2025-08-12 | Phase 1 (0.8%) | 0.9786 | 457.4 | 13.380 | 0.000 |  |
| 2025-08-13 | Phase 1 (0.8%) | 0.9892 | 446.5 | 13.136 | 0.000 |  |
| 2025-08-14 | Phase 1 (0.8%) | 0.9836 | 451.2 | 14.172 | 0.000 |  |
| 2025-08-15 | Phase 1 (0.8%) | 0.9885 | 469.3 | 14.077 | 0.000 |  |
| 2025-08-16 | Phase 1 (0.8%) | 0.9836 | 421.7 | 15.097 | 0.000 |  |

---

## Known Limitations

1. **Synthetic data only.** Real probe noise may differ.
   Actual lead time could be shorter or longer.

2. **Single observer.** Real deployment requires quorum >= 2
   distinct orgs via AgreementScorer before public alert.

3. **No DP noise in simulation.** Live probes apply Laplace
   noise (epsilon=2.0) which adds variance; may delay alert
   by 1-3 days.

4. **CUSUM not recalibrated** on real traffic (h=5.0, k=0.5
   are Phase 0 defaults).

---

*Reproducible: `python3 scripts/anthropic_backtest.py`*