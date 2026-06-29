# SEISMOGRAPH — Roadmap

SEISMOGRAPH is built in four phases, from a validated thesis to an
enterprise-grade network. Status reflects what is **verified in the codebase**
(107 tests, CI-gated), not aspiration. Maturity is stated honestly: `live`
components are tested and in use; `hardening` and `planned` items are open work.

| Phase | Name | Status |
|---|---|---|
| 0 | Validation & backtest | ✅ Complete |
| 1 | Solo MVP / public good | ✅ Core complete |
| 2 | Network growth | 🟡 In progress |
| 3 | Enterprise grade | ⚪ Planned |

---

## Phase 0 — Validation & backtest ✅

**Goal:** prove that lightweight distributional canaries surface silent drift
earlier than infrastructure metrics.

- [x] Page-CUSUM change-point detector over standardised observations.
- [x] Reproducible backtest of the documented Anthropic Claude 3.5 Sonnet
      degradation (Aug–Sep 2025): first alert **38 days before** the official
      postmortem, in the subtle 0.8%-traffic window (fixed seed, assertions pass).
- [x] Architecture document and calibration record.

**Outcome:** thesis validated on a synthetic reconstruction of a public incident.
Permanently archived: [doi.org/10.5281/zenodo.21045518](https://doi.org/10.5281/zenodo.21045518).

## Phase 1 — Solo MVP / public good ✅

**Goal:** ship an open, free early-warning utility anyone can run.

- [x] Open-source probe SDK — `pip install seismograph-probe`.
- [x] Content-addressed canary suite (≤200 prompts, temperature 0).
- [x] Privacy layer: SHA-256 hashing + differential privacy (ε=2.0).
- [x] Single-node ingestion gateway with signed-batch validation.
- [x] Cross-observer agreement scorer (quorum ≥ 2).
- [x] Free public **"model weather"** dashboard (live).
- [x] Webhooks & alerting (private-fleet path).

## Phase 2 — Network growth 🟡

**Goal:** grow from a single probe into a federated network with hardened trust.

- [ ] Full Ed25519 signature verification + reputation weighting (Sybil resistance).
- [ ] Live OpenTelemetry GenAI span emission (`gen_ai.*`) and MCP adapters (`mcp.*`).
- [ ] Differential-privacy hardening: per-metric sensitivity, sequential
      composition accounting, recalibration on real probe traffic.
- [ ] Bayesian online change-point detector (second detection layer).
- [ ] ClickHouse migration for high-throughput, cross-org ingestion.
- [ ] Methodology paper.

## Phase 3 — Enterprise grade ⚪

**Goal:** make SEISMOGRAPH deployable inside regulated organisations.

- [ ] Multi-tenant isolation (partial: isolation + audit-export landed early).
- [ ] SSO / RBAC.
- [ ] SOC 2 readiness.
- [ ] In-VPC probe option.
- [ ] SLAs and canary-gated rollback.

---

## Principles that do not change across phases

- **Privacy by construction** — raw prompts/outputs never leave the probe perimeter.
- **Correlation-first** — a single observer can never raise a public alert.
- **Content-addressed baselines** — canary suites are immutable and hash-addressed.
- **No overclaiming** — nothing is labelled "verified" until it passes a named test.

Issues and contributions are welcome at
[github.com/Tania-coder/SEISMOGRAPH](https://github.com/Tania-coder/SEISMOGRAPH).
