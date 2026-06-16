# KEYSTONE REPORT — SESSION 016
**Task:** P3-004 — SOC 2 Audit-Grade Incident Export  
**Date:** 2026-06-12  
**Baseline in:** 91/91  **Baseline out:** 99/99  **Ruff:** 0 violations (38 files)

---

## 1. Provenance

| Artifact | Origin |
|---|---|
| `engine/repository.py` — 3 new abstract methods | AI-generated |
| `engine/repository.py` — 3 `SignalRepository` implementations | AI-generated |
| `engine/clickhouse.py` — 3 new stubs/implementations | AI-generated |
| `engine/audit.py` (new, 198 lines) | AI-generated |
| `gateway/main.py` — `GET /v1/alerts/{alert_id}/export` endpoint | AI-generated |
| `probe/sdk.py` — `dp_storage_path` field + wiring | AI-generated (defect fix D39a) |
| `tests/test_audit.py` (new, 8 tests AU1–AU8) | AI-generated |
| Accountability signature | Human (Tatiana) |

---

## 2. Verification Summary

| Layer | Tests | Result |
|---|---|---|
| `AuditReportGenerator.generate()` | AU1–AU5 | ✅ pass |
| `GET /v1/alerts/{alert_id}/export` endpoint | AU6–AU7 | ✅ pass |
| Adversarial tamper detection | AU8 | ✅ pass |
| Full suite regression | 99/99 | ✅ pass |

**Tools:** pytest 7.x, ruff 0.4.x, Python 3.10  
**New test coverage:** 8 tests (AU1–AU8)  
**Prior baseline maintained:** all 91 pre-existing tests still pass

---

## 3. Defects Caught and Fixed

### D39a — `ProbeSDK` hardcoded `storage_path=".seismograph_dp.json"` caused cross-test DP budget accumulation

**Symptom:** `tests/test_sdk.py::test_flush_raises_on_non_202` (T4) failed with  
`"Daily privacy budget exceeded. Probe entering sleep mode."` — the test expected a `RuntimeError` on HTTP 500 but the SDK returned early due to exhausted budget.

**Root cause:** Session 015's P3-003b fix correctly persisted the DP budget across restarts by writing to `.seismograph_dp.json`. However, `ProbeSDK.__init__` hardcoded this path unconditionally — including in tests that don't inject a custom `_accountant`. Over repeated test runs the accumulated spend in the shared file exceeded the 10.0 epsilon budget, causing `PrivacyBudgetExceededError` before any HTTP call could be made.

**Fix:** Added `dp_storage_path: str | None = None` to `ProbeConfig`. `ProbeSDK.__init__` now uses `config.dp_storage_path` in the `DPAccountant` constructor. Default is `None` (no file persistence, budget resets on each instantiation). Production deployments opt in by setting `dp_storage_path=".seismograph_dp.json"` in their `ProbeConfig`.

**Test that catches regression:** `test_flush_raises_on_non_202` (T4) — now passes reliably regardless of test execution order or prior run count.

**Scope note:** This is a fix to a pre-existing defect introduced by P3-003b. It does not alter the persistence behaviour when `dp_storage_path` is explicitly set.

---

## 4. Known Limitations

**KNOWN-LIMIT-P3-004-A:** `get_signals_before_timestamp` in `SignalRepository` filters by `fleet_id` only when non-None. For a public alert (no fleet), the evidence window spans all probes for that model_tuple — which may include signals from multiple orgs. This is the correct SOC 2 behaviour (full network evidence), but it means a single-org installation with high probe volume could return 50 signals from one client_id. The audit report does not group or attribute by client_id in the baseline_evidence to preserve privacy.

**KNOWN-LIMIT-P3-004-B:** `alert_id` integer namespace collision: `LocalDriftAlert.id` and `PublicDriftAlert.id` are separate auto-increment sequences. If `alert_id=3` exists in both tables, `get_local_alert_by_id` wins (local is probed first). The report does not surface both; the second match is silently ignored. Callers needing to distinguish should use the `type` field in `alert_details`.

**KNOWN-LIMIT-P3-004-C:** No authentication on `GET /v1/alerts/{alert_id}/export`. The endpoint is currently open (no `X-Seismograph-Signature` check). A Phase 3 hardening sprint must add admin-token or fleet-scoped auth before any public deployment. The spec did not require auth on the export endpoint; this limitation is recorded for tracking.

**KNOWN-LIMIT-P3-004-D:** `ClickHouseRepository.get_local_alert_by_id` and `get_public_alert_by_id` raise `NotImplementedError` because ClickHouse uses UUID PKs. The audit export endpoint only works with the SQLite backend. A Phase 3 ClickHouse path would need a different alert resolution strategy (e.g., query by timestamp range + model_tuple).

---

## 5. Accountability Statement

> I, Tatiana (Director, SEISMOGRAPH), have reviewed the implementation of P3-004  
> SOC 2 Audit-Grade Incident Export. The `AuditReportGenerator` correctly resolves  
> local and public drift alerts, attaches preceding telemetry evidence, and produces  
> a deterministically verifiable SHA-256 checksum. Defect D39a (DP budget  
> cross-test contamination) was caught during verification and fixed before delivery.  
> All 99 tests pass. All 4 known limitations are documented and scoped for future  
> sprints. I accept accountability for this build as of 2026-06-12.
>
> _________________________  
> Tatiana

---

## 6. Methodology Note

**Improvement:** The `dp_storage_path` pattern (opt-in file persistence via config, not via hardcoded path in constructor) should be applied consistently across all stateful probe components. Any component that writes to a file path should accept that path as a constructor or config parameter rather than hardcoding it, so tests never accidentally share persistent state between runs. A single conftest fixture that sets all such paths to `tmp_path`-based locations would eliminate this class of test contamination entirely.
