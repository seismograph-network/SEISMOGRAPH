# SEISMOGRAPH — Session 002 Handover
# Written: 2026-06-10 (end of Session 001)
# Purpose: Dense context for next co-pilot session. Read this first.

---

## 1. Project identity

**SEISMOGRAPH** — Federated, privacy-preserving early-warning network that
detects semantic behavioral drift in third-party LLM/agent APIs by correlating
lightweight canary probe signals across organizations.

Core question: "Is it me, my prompt, or did the model silently change under me?"

Stack: Python (probe SDK), TypeScript (dashboard), ClickHouse/Postgres,
OpenTelemetry GenAI, MCP.
Repo: D:/Dev/Projects/SEISMOGRAPH
Director: Tatiana. Co-pilot: Claude (claude-sonnet-4-6).
Phase: 0 (Validation and Backtest).

---

## 2. Phase 0 thesis validation — DONE

**P0-006 result (2026-06-10):**
Script: scripts/anthropic_backtest.py (SEED=42, reproducible)
Report: notebooks/anthropic_backtest_report.md

SEISMOGRAPH (simulated) detected the Anthropic Claude 3.5 Sonnet silent
degradation on **2025-08-10**, which is:
  - **38 days before** the official postmortem (2025-09-17)
  - **19 days before** the visible load-balancer escalation (2025-08-29)
  - Detected during Phase 1 (0.8% misrouting only) -- subtle signal

Method: Page-CUSUM on json_success_rate, h=5.0, k=0.5, baseline_samples=30.
Baseline: Jul 1-30 (30-day warm-up). mu0=0.9903, sigma0=0.00437.
Assertions C1-C6: all PASS. Ruff: PASS.

---

## 3. Environment facts

- Python: 3.10 in the bash sandbox (NOT 3.11 -- do not use datetime.UTC, use
  timezone.utc; pyproject.toml has ignore=["UP017"] to suppress ruff UP017)
- Ruff: installed, configured in pyproject.toml. Run:
    python3 -B -m ruff check --fix <file> && python3 -B -m ruff format <file>
- Pydantic: v2. InboundSignalBatch uses ConfigDict(extra="forbid", frozen=True).
- All imports use python3 -B or PYTHONDONTWRITEBYTECODE=1 to bypass stale .pyc
  files (cannot delete .pyc on Windows NTFS mount from Linux sandbox).
- Bash sandbox mount: /sessions/intelligent-tender-goldberg/mnt/SEISMOGRAPH/
  maps to D:/Dev/Projects/SEISMOGRAPH on Windows.
- Working outputs dir: /sessions/.../outputs (temporary, cleared between sessions)

---

## 4. Hard operational rules (all established by defect; never violate)

RULE-1 (D7+D8 -- CRITICAL): Python files > ~8KB MUST be written via:
  python3 -c "open(path, 'w', encoding='utf-8').write(content)"
  via bash. The Write tool and Edit tool SILENTLY TRUNCATE at ~10KB on
  the Windows NTFS mount. This has bitten us TWICE (privacy.py, detector.py).
  Preemptively use bash writes for any file expected to exceed 8KB.

RULE-2: Ruff before done. Every Python file gets:
  python3 -B -m ruff check --fix <file> && python3 -B -m ruff format <file>
  Never mark a task complete without a clean ruff run.

RULE-3: SG-TRACE annotations use two-line split:
  #SG-TRACE: REQ-XXX-NNN
  #   | assumption: ...
  #   | test: test_...
  Never single-line (E501 violation).

RULE-4: python3 -B everywhere in sandbox (suppress .pyc writes).

RULE-5: Large file writes -- use Python with encoding='utf-8'. Never heredoc
  for files containing triple-quoted strings (shell parsing conflict).

RULE-6 (D9): For daily-probe CUSUM baselines, use baseline_samples=30+.
  The default MIN_BASELINE_SAMPLES=10 under-estimates sigma from daily Gaussian
  noise. A 10-sample baseline with SEED=42 gave sigma0=0.00228 vs true 0.006.

---

## 5. Architectural decisions locked (require Tatiana to reopen)

REQ-PRIV-002 (APPROVED 2026-06-10):
  Ed25519 keypair per probe installation. Central engine builds reputation
  against public key without knowing org identity (pseudonymous federation).
  Sybil resistance via reputation weighting. Implementation: Phase 2.
  Current state: gateway/auth.py verify_signature() returns True (stub).

QUORUM_MIN=2:
  Cross-observer agreement requires >= 2 distinct orgs before public alert.
  Single-org signals are private fleet data only. (AgreementScorer in
  engine/correlation.py.) Phase 1 open decision: raise QUORUM_MIN?

DP epsilon=2.0 per flush (REQ-PRIV-009):
  Sequential composition across flush windows not yet tracked (REQ-PRIV-010,
  Phase 1 design item). Tatiana approval required before changing epsilon.

---

## 6. What is complete (Phase 0 scaffold)

P0-001: Full directory structure + stubs + memory files + root files
P0-002: probe/canary_suite.py (CanarySuiteRegistry), probe/canary.py (v1.0.0)
P0-003: probe/privacy.py (SignalBatch, Aggregator, Laplace DP noise, epsilon=2.0)
P0-004: gateway/schema.py (InboundSignalBatch Pydantic v2), gateway/auth.py
        (Ed25519 stub), gateway/ingest.py (mock receive_batch)
P0-005: engine/detector.py (CUSUMDetector, DriftAlert, _MetricState, Page-CUSUM)
        engine/correlation.py (AgreementScorer, QUORUM_MIN=2)
        data/drift_labels/cusum_phase0_calibration.md
P0-006: scripts/anthropic_backtest.py + notebooks/anthropic_backtest_report.md
        RESULT: 38-day lead time. Phase 0 thesis validated.

---

## 7. Immediate next tasks (Session 002)

### P0-007 — Architecture document (HIGH PRIORITY)
Complete all sections of SEISMOGRAPH_Architecture.md. Current state: stub with
section headers only. Atlas agent responsible. Scope:
  - System overview and data flow diagram (text/mermaid)
  - Component descriptions: probe SDK, privacy layer, gateway, correlation engine
  - Privacy-by-construction guarantees (DP mechanism, hash-only boundary)
  - Federation model (pseudonymous identity, reputation scoring)
  - Phase roadmap (0 through 3)
  - Atlas agent pass: PEP 484 type hints + class/method docstrings in
    engine/correlation.py and probe/sdk.py (sdk.py is stub)

### P0-008 — OTel instrumentation stub (PAUSED)
Create probe/sdk.py with OpenTelemetry GenAI semantic conventions
(gen_ai.*, mcp.*). Tatiana explicitly paused this: "We are pausing the
plumbing (P0-008) to focus purely on the product core thesis."
Do NOT start P0-008 without explicit Tatiana directive.

### P0-005 tail — BayesianOnlineDetector (Phase 1)
engine/correlation.py BayesianOnlineDetector.update() raises NotImplementedError.
Deferred to Phase 1. Do not implement in Phase 0.

---

## 8. Key file locations

| File | Purpose |
|---|---|
| probe/canary.py | CanaryResult, execute_canary (mock), v1.0.0 suite |
| probe/privacy.py | SignalBatch (frozen), Aggregator (DP noise), EPSILON=2.0 |
| engine/detector.py | CUSUMDetector, DriftAlert, _MetricState |
| engine/correlation.py | AgreementScorer, QUORUM_MIN=2, BayesianOnlineDetector stub |
| gateway/schema.py | InboundSignalBatch (Pydantic v2) |
| gateway/auth.py | Ed25519 stub (verify_signature returns True) |
| gateway/ingest.py | receive_batch mock (202/400/401) |
| scripts/anthropic_backtest.py | Phase 0 backtest (SEED=42, baseline_samples=30) |
| notebooks/anthropic_backtest_report.md | Backtest results and CUSUM trace |
| data/drift_labels/cusum_phase0_calibration.md | h=5.0 k=0.5 calibration record |
| KEYSTONE_REPORT_SESSION_001.md | Audit trail for Session 001 |
| SEISMOGRAPH_Architecture.md | Architecture stub (P0-007 target) |
| memory/project_open_tasks.md | Canonical backlog |
| memory/project_session_log.md | Monotonic session log |
| kernel.log | Monotonic nanosecond event log |
| graph.json | Dependency graph (empty {}; Phase 1 Atlas task) |

---

## 9. Test contracts named but not yet implemented

All SG-TRACE test names are forward declarations. No test files exist yet.
The formal test suite is a Phase 1 prerequisite before any deployment.
Named tests: test_signal_batch_is_only_outbound_type, test_dp_noise_perturbs_metrics,
test_cusum_stable_window_no_false_positive, test_cusum_reset_clears_state,
test_backtest_alert_precedes_postmortem, and 17 others across 6 files.

---

*Session 001 safely parked. Next action: P0-007 architecture doc.*
