# SEISMOGRAPH Architecture
# Status: COMPLETE -- Phase 0. Updated by Atlas agent (P0-007, Session 002).
# Last updated: 2026-06-11

---

## 1. System purpose

SEISMOGRAPH is a federated, privacy-preserving early-warning network that detects
semantic behavioural drift in third-party LLM/agent APIs by correlating lightweight
canary probe signals across organisations.

It answers: **"Is it me, my prompt, or did the model silently change underneath me?"**

The system operates on a strict privacy boundary: raw prompts and raw model outputs
never leave the probe perimeter. Only cryptographically hashed identifiers,
distributional statistics, and differentially-private noised aggregates are
transmitted to the central correlation engine.

---

## 2. Architectural invariants

| Invariant | Rule |
|---|---|
| Privacy by construction | Raw prompts/outputs never leave the probe perimeter. Only hashes, distributional features, and DP-noised aggregates are transmitted. Verified on every PR by Aegis agent. |
| OTel-native first | All instrumentation via OpenTelemetry GenAI semantic conventions (gen_ai.*, mcp.*). No proprietary tap. |
| Content-addressed baselines | Every canary suite version is immutably hash-addressed (SHA-256). Baseline corpus is append-only. Never mutate a historical baseline -- create a new versioned snapshot. |
| Correlation-first alerts | A single-org signal is never promoted to a public drift alert. Cross-observer agreement scoring gates every alert (QUORUM_MIN=2). |
| Canary suite cost cap | <=200 prompts at temperature 0. Cost per probe per day target < bash.10 at current provider pricing. |
| PEP8 / code style | All Python adheres to PEP8. Two blank lines between top-level functions (E302). TypeScript follows ESLint standard config. |

---

## 3. Component overview



---

## 4. Data flow and privacy boundary

The privacy boundary is the most important structural property of SEISMOGRAPH.
It is enforced at the probe layer before any data leaves the probe perimeter.



Raw prompts and raw outputs are destroyed at the CanarySuiteRunner boundary.
They never appear in SignalBatch, InboundSignalBatch, or any downstream store.

---

## 5. Canary suite versioning

Each canary suite is a versioned, content-addressed collection of CanaryPrompt
objects managed by CanarySuiteRegistry (probe/canary_suite.py).

Version addressing:
  - Each suite version is identified by a SHA-256 hash of the JSON-serialised,
    canonically sorted list of all (prompt_text, expected_format, metadata) tuples.
  - The hash is computed by CanarySuiteVersion.from_prompts() and stored as
    CanarySuiteVersion.version_hash.
  - Registry is strictly append-only: CanarySuiteRegistry.register() accepts new
    versions; no method exists to mutate or delete an existing version.
  - Retrieving by hash: CanarySuiteRegistry.get_version(hash) returns the frozen
    suite or raises KeyError for unknown hashes.

Staleness rule:
  - The dashboard surfaces a staleness warning if the active baseline for any
    model tuple has not received a suite update in > 30 days.
  - A stale baseline does NOT generate a false drift alert; it generates a
    "staleness" warning class, distinct from a DriftAlert.

Cost cap:
  - The v1.0.0 suite (probe/canary.py) contains a small representative corpus.
  - Hard invariant: no suite version may exceed 200 prompts at temperature 0.
  - Cost target: < bash.10 per probe per day at current provider pricing.
  - Provider ToS compliance check: REQUIRED before any production canary corpus
    is designed. This check has not yet been performed (Phase 0 uses mock execution
    only -- execute_canary() in probe/canary.py returns synthetic results without
    making any real API calls).

---

## 6. Change-point detection

SEISMOGRAPH uses two-layer detection:

### Layer 1 -- Per-org, per-metric: CUSUMDetector (engine/detector.py)

Page-CUSUM over standardised observations z = (x - mu0) / sigma0:

    S+(n) = max(0, S+(n-1) + z(n) - k)   -- detects positive shifts
    S-(n) = max(0, S-(n-1) - z(n) - k)   -- detects negative shifts

Alert fires when S+(n) > h or S-(n) > h.

Phase 0 calibrated parameters:
  - h = 5.0  (detection threshold; conservative starting point)
  - k = 0.5  (allowance parameter; suppresses small fluctuations)
  - baseline_samples = 30  (minimum observations before CUSUM activates)

Baseline estimation:
  - Each (model_tuple, metric_name) stream accumulates baseline_samples
    observations to compute mu0 (mean) and sigma0 (standard deviation).
  - sigma0 is clamped to a minimum of 1e-6 to prevent ZeroDivisionError
    on constant series.
  - Observations during the baseline phase do not generate alerts.

Why baseline_samples=30:
  - Defect D9 (Session 001): using 10 samples with SEED=42 produced
    sigma0=0.00228 vs. the true noise level of ~0.006 -- a 2.6x underestimate.
    This caused false positives on pre-bug data (2025-07-17 in the backtest).
  - 30 samples gives sigma0=0.00437 -- 1.4x underestimate, acceptable.
  - Calibration record: data/drift_labels/cusum_phase0_calibration.md.

### Layer 2 -- Cross-org quorum: AgreementScorer (engine/correlation.py)

See Section 7. The detector layer fires per-org candidate alerts; the
correlation layer decides whether to surface them publicly.

### NOTE on engine/ module split

engine/detector.py  -- LIVE implementation. CUSUMDetector here is the
                       production-quality Page-CUSUM detector used in the
                       backtest and all Phase 0 verification.

engine/correlation.py -- Contains:
  (a) AgreementScorer -- LIVE, used in quorum checks.
  (b) CUSUMDetector   -- STUB ONLY. This class raises NotImplementedError
                         and exists as an interface contract. All CUSUM
                         logic lives in engine/detector.py.
  (c) BayesianOnlineDetector -- STUB ONLY. Phase 1 implementation target.

Future developers: do NOT wire engine/correlation.py CUSUMDetector into
production code. Always import from engine.detector.

### BayesianOnlineDetector (Phase 1)

Bayesian online change-point detection (Adams & MacKay 2007).
Tracks posterior probability of a change point at each time step.
Implementation deferred to Phase 1. Stub in engine/correlation.py raises
NotImplementedError.

---

## 7. Cross-observer agreement scoring

Every DriftAlert candidate is tagged with the originating org_id (pseudonymous
identifier bound to the probe's Ed25519 public key -- see Section 10).

AgreementScorer (engine/correlation.py) gates public alert promotion:

  QUORUM_MIN = 2  (class constant; Phase 1 open decision: raise to 3?)

  promote_to_public_alert(model_tuple) algorithm:
    1. Collect all ChangePointResult objects for the given model_tuple.
    2. Filter to results where change_detected == True.
    3. Collect the union of contributing_orgs across matching results.
    4. Return True only if len(distinct orgs) >= QUORUM_MIN.

  Single-org signals:
    - Never promoted. Stored as private fleet data only.
    - Never surfaced on the public dashboard.

  Replay / Sybil deduplication:
    - Phase 0: signal replay is prevented by org_id set deduplication within
      a single scoring round (ADV2 in the adversarial test suite).
    - Phase 2: full Sybil resistance via Ed25519 reputation weighting.

  Open decision: QUORUM_MIN=2 is the Phase 0 minimum. Phase 1 milestone
  requires Tatiana approval before changing this value.

---

## 8. OpenTelemetry integration

All probe instrumentation MUST use OpenTelemetry GenAI semantic conventions.
No proprietary tap is permitted.

Semantic convention namespaces in use:
  - gen_ai.*  -- LLM call attributes (model, prompt token count, etc.)
  - mcp.*     -- MCP adapter spans

Phase 0 status: probe/sdk.py is a typed stub (scaffold only). OTel spans are
NOT yet emitted. The stub defines the interface contract for Phase 1 wiring.

Phase 1 plan:
  - Wire OTelSpanContext.start_span() to the OpenTelemetry SDK.
  - Export via OTLP gRPC to a configurable endpoint (ProbeConfig.otel_endpoint).
  - Every canary execution emits one root span with gen_ai.* attributes.
  - Privacy invariant: span attributes must NEVER include raw prompt text or
    raw model output. Only hashed identifiers and aggregate metrics are permitted.

Phase 2 plan:
  - MCP adapter spans (mcp.*) for agent-based probe workflows.
  - Span correlation across org boundaries (federated trace context).

---

## 9. Storage

| Phase | Store | Notes |
|---|---|---|
| 0 | In-memory (Python dicts) | _MetricState per (model_tuple, metric_name) in engine/detector.py; _pending dict in AgreementScorer |
| 1 | Postgres (time-series) | Feature vectors, baseline snapshots, alert log |
| 2 | ClickHouse (columnar) | High-throughput ingestion, cross-org aggregation queries |

Phase 0 in-memory state is ephemeral. No persistence across process restarts.
This is intentional for the backtest and validation phase.

---

## 10. Security model

### Authentication -- Ed25519 probe identity (REQ-PRIV-002, APPROVED 2026-06-10)

Each probe installation generates a unique Ed25519 keypair at install time.
The probe signs every outbound InboundSignalBatch with its private key.
The central ingestion engine holds only the public key and builds a reputation
score against that key over time.

Design properties:
  - Pseudonymous federation: the engine identifies probes by public key, not
    by organisation name or identity. The org-to-key mapping is held only by
    the probe operator.
  - Sybil resistance: new keys start with zero reputation. Low-reputation keys
    are down-weighted in AgreementScorer before being promoted. Keys that inject
    fabricated feature vectors are detectable by statistical outlier analysis.
  - Key revocation: not yet designed (Phase 2).

Phase 0 implementation status:
  - gateway/auth.py verify_signature() returns True (stub).
  - TODO(Phase 2): Implement Ed25519 verification using
    cryptography.hazmat.primitives.asymmetric.ed25519.

### Differential privacy -- Laplace mechanism (REQ-PRIV-009)

See Section 4 for per-metric epsilon and sensitivity values.
Current epsilon=2.0 per flush window. Tatiana approval required before
changing this value. Sequential composition budget (REQ-PRIV-010) is a
Phase 1 design item.

### Sybil resistance

  Phase 0: org_id set deduplication within a scoring round.
  Phase 2: signature verification + reputation weighting across rounds.

### Aegis agent responsibility

Aegis agent owns probe/privacy.py and gateway/ingest.py.
All security fixes to these files go through Aegis. No other agent
modifies these files.

---

## 11. Open architectural decisions (require Tatiana approval before closing)

| Decision | Status | Phase |
|---|---|---|
| DP epsilon-budget value | epsilon=2.0 locked for Phase 0; Phase 1 will refine per-metric | 1 |
| Sequential composition accounting (REQ-PRIV-010) | Not yet designed | 1 |
| QUORUM_MIN above 2 | Open (currently QUORUM_MIN=2) | 1 |
| CUSUM threshold recalibration on real probe traffic | Deferred; Phase 0 uses synthetic calibration | 1 |
| OTel exporter target host/port | TBD; ProbeConfig.otel_endpoint placeholder | 1 |
| Provider ToS compliance check | REQUIRED before any real canary corpus design | 0/1 |
| Laplace noise scale refinement (delta_f = MAX/n for large batches) | Phase 0 uses conservative global MAX | 1 |
| Key revocation design | Not yet designed | 2 |

---

## 12. Phase 0 validation result

**SEISMOGRAPH detected the Anthropic Claude 3.5 Sonnet silent degradation
38 days before the official postmortem.**

Details:
  Script:          scripts/anthropic_backtest.py (SEED=42, reproducible)
  Report:          notebooks/anthropic_backtest_report.md
  Calibration:     data/drift_labels/cusum_phase0_calibration.md

  Model:           anthropic/claude-3-5-sonnet@global
  Baseline start:  2025-07-01  (35-day warm-up, baseline_samples=30)
  Bug introduced:  2025-08-05  (~0.8% misrouting -- subtle Phase 1 signal)
  Escalation:      2025-08-29  (~16% misrouting -- visible to users)
  Official PM:     2025-09-17

  CUSUM params:    h=5.0, k=0.5
  Baseline stats:  mu0=0.9903, sigma0=0.00437 (json_success_rate)

  First alert:     2025-08-10  (S- = 7.278, threshold h=5.0)
  Lead over escalation:  19 days
  Lead over postmortem:  38 days
  Detected in:     Phase 1 (0.8% misrouting only -- pre-user-visible)

Assertions C1-C6: all PASS.

This result validates the Phase 0 thesis on synthetic data. Real lead time
depends on actual probe traffic volume, number of participating orgs, and
live DP noise variance. A single-org synthetic simulation. Live alert
requires quorum >= 2 distinct orgs via AgreementScorer before public
promotion.

---

## 13. File index

| Path | Owner | Purpose | Status |
|---|---|---|---|
| probe/__init__.py | Canary agent | Package init, privacy boundary declaration | Complete |
| probe/canary_suite.py | Canary agent | CanarySuiteRegistry, CanarySuiteVersion, CanaryPrompt | Complete |
| probe/canary.py | Canary agent | v1.0.0 suite, CanaryResult, execute_canary (mock) | Complete |
| probe/privacy.py | Aegis agent | SignalBatch (frozen), Aggregator (Laplace DP noise, epsilon=2.0) | Complete |
| probe/sdk.py | Atlas agent | OTel-native probe SDK stub (P0-007 scaffold) | Stub (P0-008 wires OTel in Phase 1) |
| engine/__init__.py | Seismo agent | Package init | Complete |
| engine/detector.py | Seismo agent | CUSUMDetector (live Page-CUSUM), DriftAlert, _MetricState | Complete |
| engine/correlation.py | Seismo agent | AgreementScorer (live), CUSUMDetector stub, BayesianOnlineDetector stub | Partial (Phase 1) |
| gateway/__init__.py | Aegis agent | Package init, security boundary declaration | Complete |
| gateway/schema.py | Aegis agent | InboundSignalBatch (Pydantic v2, ConfigDict frozen+forbid) | Complete |
| gateway/auth.py | Aegis agent | Ed25519 stub (verify_signature returns True) | Stub (Phase 2) |
| gateway/ingest.py | Aegis agent | receive_batch mock (202/400/401) | Complete (mock) |
| scripts/anthropic_backtest.py | Seismo agent | Phase 0 backtest (SEED=42, baseline_samples=30) | Complete |
| notebooks/anthropic_backtest_report.md | Seismo agent | Backtest results, CUSUM trace, 38-day lead | Complete |
| data/drift_labels/cusum_phase0_calibration.md | Seismo agent | h=5.0 k=0.5 calibration record | Complete |
| dashboard/ | TBD | TypeScript dashboard (Phase 1) | Not started |
| memory/project_open_tasks.md | Director | Canonical backlog | Live |
| memory/project_session_log.md | Director | Monotonic session log | Live |
| kernel.log | All agents | Monotonic nanosecond operation log | Live |
| graph.json | Atlas agent | Dependency graph (empty {}; Phase 1 Atlas task) | Stub |
| pyproject.toml | All agents | Ruff config, Python target, UP017 ignore | Complete |
| SEISMOGRAPH_Architecture.md | Atlas agent | This document | Complete (P0-007) |
| KEYSTONE_REPORT_SESSION_001.md | Director | Audit trail for Sessions 001-002 | Live |
| memory/SESSION_002_HANDOVER.md | Director | Handover document for Session 002 | Complete |
