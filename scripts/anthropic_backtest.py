#!/usr/bin/env python3
"""
scripts/anthropic_backtest.py
==============================
Phase 0 Backtest: Would SEISMOGRAPH have detected the Anthropic
Claude 3.5 Sonnet silent degradation (Aug-Sep 2025) before the
official postmortem published on September 17, 2025?

Timeline (synthesized from public postmortem):
  Jul  1 2025  Baseline monitoring begins (35-day warm-up)
  Aug  5 2025  Silent routing bug introduced (~0.8% misrouting)
  Aug 29 2025  Load-balancer escalation -> ~16% misrouting;
               avg_output_length becomes erratic (1M-token context)
  Sep 17 2025  Official Anthropic postmortem published

Method:
  Synthesize daily SignalBatch payloads (seeded, reproducible).
  Feed json_success_rate and avg_output_length to CUSUMDetector.
  Record first DriftAlert. Compute lead time vs postmortem.

#SG-TRACE: REQ-VALID-001
#   | assumption: synthetic timeline is a feasibility demonstration;
#     real detection depends on actual probe traffic
#   | test: test_backtest_alert_precedes_postmortem
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.detector import CUSUMDetector, DriftAlert  # noqa: E402

# ---------------------------------------------------------------------------
# Timeline constants
# ---------------------------------------------------------------------------

BASELINE_START = date(2025, 7, 1)
BUG_DATE = date(2025, 8, 5)
ESCALATION_DATE = date(2025, 8, 29)
POSTMORTEM_DATE = date(2025, 9, 17)

MODEL = "anthropic/claude-3-5-sonnet@global"
SEED = 42

# ---------------------------------------------------------------------------
# Phase parameters
# ---------------------------------------------------------------------------

# Phase 0 -- stable baseline
BASE_RATE = 0.990
BASE_RATE_NOISE = 0.006
BASE_LEN = 450.0
BASE_LEN_NOISE = 20.0

# Phase 1 -- 0.8% misrouting (subtle)
# Net rate = 0.992*0.990 + 0.008*0.0 = 0.9821
P1_RATE = 0.982
P1_RATE_NOISE = 0.006
P1_LEN = 447.0
P1_LEN_NOISE = 25.0

# Phase 2 -- 16% misrouting (severe, bimodal lengths)
P2_RATE = 0.840
P2_RATE_NOISE = 0.015
P2_LEN_NORMAL = 450.0
P2_LEN_ERRATIC = 1850.0
P2_LEN_NOISE = 30.0

CUSUM_H = 5.0
CUSUM_K = 0.5


def get_phase(d: date) -> int:
    if d < BUG_DATE:
        return 0
    if d < ESCALATION_DATE:
        return 1
    return 2


def simulate_day(d: date, rng: random.Random) -> dict[str, float]:
    """Return synthetic daily metrics for a given date."""
    phase = get_phase(d)
    if phase == 0:
        rate = BASE_RATE + rng.gauss(0, BASE_RATE_NOISE)
        length = BASE_LEN + rng.gauss(0, BASE_LEN_NOISE)
    elif phase == 1:
        rate = P1_RATE + rng.gauss(0, P1_RATE_NOISE)
        length = P1_LEN + rng.gauss(0, P1_LEN_NOISE)
    else:
        rate = P2_RATE + rng.gauss(0, P2_RATE_NOISE)
        if rng.random() < 0.16:
            length = P2_LEN_ERRATIC + rng.gauss(0, P2_LEN_NOISE * 8)
        else:
            length = P2_LEN_NORMAL + rng.gauss(0, P2_LEN_NOISE)
    return {
        "json_success_rate": max(0.0, min(1.0, rate)),
        "avg_output_length": max(0.0, length),
    }


def run() -> None:
    rng = random.Random(SEED)
    detector = CUSUMDetector(h=CUSUM_H, k=CUSUM_K, baseline_samples=30)

    first_alert: DriftAlert | None = None
    first_alert_date: date | None = None

    rows: list[dict] = []
    day = BASELINE_START
    day_num = 0

    while day <= POSTMORTEM_DATE:
        phase = get_phase(day)
        metrics = simulate_day(day, rng)
        ts_ns = day_num * 86_400_000_000_000

        day_alert: DriftAlert | None = None
        for metric_name, value in metrics.items():
            alert = detector.update(
                MODEL, metric_name, value, timestamp_ns=ts_ns
            )
            if alert and first_alert is None:
                first_alert = alert
                first_alert_date = day
                day_alert = alert

        key = (MODEL, "json_success_rate")
        st = detector._states.get(key)
        s_neg = round(st._s_neg, 3) if st and st.baseline_ready else None
        s_pos = round(st._s_pos, 3) if st and st.baseline_ready else None
        mu0 = round(st._mu0, 4) if st and st.baseline_ready else None
        sigma0 = round(st._sigma0, 5) if st and st.baseline_ready else None

        rows.append(
            {
                "date": day,
                "phase": phase,
                "rate": round(metrics["json_success_rate"], 4),
                "length": round(metrics["avg_output_length"], 1),
                "s_neg": s_neg,
                "s_pos": s_pos,
                "mu0": mu0,
                "sigma0": sigma0,
                "alert": day_alert,
            }
        )

        day += timedelta(days=1)
        day_num += 1

    # ------------------------------------------------------------------
    assert first_alert is not None, "FAIL: no alert before postmortem"
    assert first_alert_date < POSTMORTEM_DATE, "FAIL: alert after postmortem"

    detected_in_phase1 = first_alert_date < ESCALATION_DATE
    lead_days = (POSTMORTEM_DATE - first_alert_date).days
    lead_escalation = (ESCALATION_DATE - first_alert_date).days

    # baseline stats from first fully-estimated row
    bs_row = next(r for r in rows if r["mu0"] is not None)
    mu0 = bs_row["mu0"]
    sigma0 = bs_row["sigma0"]

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------
    sep = "=" * 62
    print(sep)
    print("SEISMOGRAPH Backtest -- Anthropic Claude 3.5 Sonnet")
    print("Aug-Sep 2025 Silent Model Degradation")
    print(sep)
    print()
    print(f"  Model:           {MODEL}")
    print(f"  Baseline start:  {BASELINE_START}  (35-day warm-up)")
    print(f"  Bug introduced:  {BUG_DATE}  (~0.8% misrouting)")
    print(f"  Escalation:      {ESCALATION_DATE}  (~16% misrouting)")
    print(f"  Official PM:     {POSTMORTEM_DATE}")
    print()
    print(f"  CUSUM params:    h={CUSUM_H}, k={CUSUM_K}")
    print(f"  Baseline (json): mu0={mu0}, sigma0={sigma0}")
    print()
    print("  CUSUM S- trace around detection (json_success_rate):")
    print(f"    {'Date':<14} {'Phase':<18} {'rate':<8} {'S-':<8} note")
    print(f"    {'-' * 60}")
    for r in rows:
        if not (
            (first_alert_date - timedelta(7))
            <= r["date"]
            <= (first_alert_date + timedelta(5))
        ):
            continue
        ph = ["Stable", "Phase1(0.8%)", "Phase2(16%)"][r["phase"]]
        s_s = f"{r['s_neg']:.3f}" if r["s_neg"] is not None else "(bsln)"
        note = (
            "<<< ALERT"
            if r["alert"]
            else ("[start]" if r["date"] == BUG_DATE else "")
        )
        print(  # noqa: E501
            f"    {str(r['date']):<14} {ph:<18} {r['rate']:<8} {s_s:<8} {note}"
        )  # noqa: E501
    print()
    print("  *** FIRST DRIFT ALERT ***")
    print(f"      Date:     {first_alert_date}")
    print(f"      Metric:   {first_alert.metric_name}")
    print(f"      Direction:{first_alert.direction}")
    cusum_line = f"      S- score: {first_alert.cusum_score:.4f}"  # noqa: E501
    print(cusum_line + f"  (threshold h={CUSUM_H})")
    print(f"      Detected in Phase 1 (subtle): {detected_in_phase1}")
    print()
    print(f"  Lead over escalation:  {lead_escalation} days")
    print(f"  Lead over postmortem:  {lead_days} days")
    print()
    print(sep)
    print(f"  RESULT: SEISMOGRAPH would have alerted {lead_days} days")
    print("          before the official Anthropic postmortem.")
    print(sep)

    # ------------------------------------------------------------------
    # Markdown report
    # ------------------------------------------------------------------
    table_rows = [
        r
        for r in rows
        if (first_alert_date - timedelta(7))
        <= r["date"]
        <= (first_alert_date + timedelta(6))
    ]

    def phase_label(p: int) -> str:
        return ["Baseline", "Phase 1 (0.8%)", "Phase 2 (16%)"][p]

    md = [
        "# SEISMOGRAPH Backtest Report",
        "## Anthropic Claude 3.5 Sonnet -- Aug-Sep 2025",
        "",
        f"**Generated:** 2026-06-10 | **Seed:** {SEED} | "
        f"**CUSUM:** h={CUSUM_H}, k={CUSUM_K}",
        "",
        "---",
        "",
        "## The Question",
        "",
        "In Q3 2025, Anthropic published a postmortem describing a silent",
        "degradation in Claude 3.5 Sonnet caused by a load-balancer",
        "misconfiguration that misrouted a fraction of requests to an",
        "incompatible model configuration.",
        "",
        f"The bug was introduced around **{BUG_DATE}** (~0.8% of traffic).",
        f"By **{ESCALATION_DATE}** the misrouting rate had escalated to ~16%,",
        "producing erratic output lengths consistent with a 1M-token context",
        "window mismatch. The degradation was publicly disclosed on",
        f"**{POSTMORTEM_DATE}**.",
        "",
        "**Would a deployed SEISMOGRAPH probe have detected this earlier?**",
        "",
        "---",
        "",
        "## Simulation Setup",
        "",
        "Daily canary probes synthesized with seeded Gaussian noise:",
        "",
        "| Phase | Date range | json_success_rate | sigma |",
        "|---|---|---|---|",
        f"| Baseline | {BASELINE_START} -- {BUG_DATE - timedelta(1)}"
        f" | {BASE_RATE} | {BASE_RATE_NOISE} |",
        f"| Phase 1 (0.8%) | {BUG_DATE} -- {ESCALATION_DATE - timedelta(1)}"
        f" | {P1_RATE} | {P1_RATE_NOISE} |",
        f"| Phase 2 (16%) | {ESCALATION_DATE} -- {POSTMORTEM_DATE}"
        f" | {P2_RATE} | {P2_RATE_NOISE} |",
        "",
        "Detector: Page-CUSUM, S+/S- per"  # noqa: E501
        " (model_tuple, metric_name) stream.",
        "Baseline estimated from first 10 daily observations.",
        f"Alert threshold: h={CUSUM_H} standard deviations.",
        "",
        "---",
        "",
        "## Result",
        "",
        "| | |",
        "|---|---|",
        f"| **First DriftAlert** | **{first_alert_date}** |",
        f"| Metric | `{first_alert.metric_name}` |",
        f"| Direction | {first_alert.direction} |",
        f"| CUSUM S- score | {first_alert.cusum_score:.4f}"  # noqa: E501
        f" (threshold h={CUSUM_H}) |",
        f"| Baseline mu0 | {mu0} |",
        f"| Baseline sigma0 | {sigma0} |",
        f"| Detected in Phase 1 (subtle 0.8%) | {detected_in_phase1} |",
        f"| **Lead over escalation** | **{lead_escalation} days** |",
        f"| **Lead over postmortem** | **{lead_days} days** |",
        "",
        f"> SEISMOGRAPH would have alerted on **{first_alert_date}**,",
        f"> **{lead_days} days before** the official postmortem.",
        "> The signal was detected in the **subtle Phase 1** window",
        f"> ({lead_escalation} days before the visible escalation).",
        "",
        "---",
        "",
        "## CUSUM Trace (detection window)",
        "",
        "| Date | Phase | json_rate | avg_len | S- | S+ | |",
        "|---|---|---|---|---|---|---|",
    ]

    for r in table_rows:
        sn = f"{r['s_neg']:.3f}" if r["s_neg"] is not None else "(baseline)"
        sp = f"{r['s_pos']:.3f}" if r["s_pos"] is not None else "--"
        flag = "**ALERT**" if r["alert"] else ""
        md.append(
            f"| {r['date']} | {phase_label(r['phase'])} | {r['rate']} "
            f"| {r['length']} | {sn} | {sp} | {flag} |"
        )

    md += [
        "",
        "---",
        "",
        "## Known Limitations",
        "",
        "1. **Synthetic data only.** Real probe noise may differ.",
        "   Actual lead time could be shorter or longer.",
        "",
        "2. **Single observer.** Real deployment requires quorum >= 2",
        "   distinct orgs via AgreementScorer before public alert.",
        "",
        "3. **No DP noise in simulation.** Live probes apply Laplace",
        "   noise (epsilon=2.0) which adds variance; may delay alert",
        "   by 1-3 days.",
        "",
        "4. **CUSUM not recalibrated** on real traffic (h=5.0, k=0.5",
        "   are Phase 0 defaults).",
        "",
        "---",
        "",
        "*Reproducible: `python3 scripts/anthropic_backtest.py`*",
    ]

    report_path = (  # noqa: E501
        Path(__file__).parent.parent
        / "notebooks"
        / "anthropic_backtest_report.md"  # noqa: E501
    )
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text("\n".join(md), encoding="utf-8")
    print("  Report: notebooks/anthropic_backtest_report.md")


if __name__ == "__main__":
    run()
