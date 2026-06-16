# KEYSTONE REPORT — SESSION 013
## Task: P3-001 Multi-Tenant Data Isolation
**Phase:** 3 — Enterprise Plane
**Date:** 2026-06-12
**Accountable Director:** Tatiana
**Lead Technical Co-Pilot:** Claude (Sonnet 4.6)

---

## 1. Provenance

| Artifact | Origin |
|---|---|
| `gateway/schema.py` | AI-generated (full rewrite) |
| `probe/privacy.py` | AI-generated (full rewrite) |
| `engine/models.py` | AI-generated (full rewrite) |
| `engine/repository.py` | AI-generated (full rewrite) |
| `engine/clickhouse.py` | AI-generated (full rewrite) |
| `gateway/main.py` | AI-generated (full rewrite) |
| `probe/sdk.py` | AI-generated (full rewrite) |
| `tests/test_enterprise.py` | AI-generated (new file) |
| `tests/test_storage.py` line 391 | AI-patched (column count update) |

All files written via RULE-1 bash heredoc (`python3 -B - << 'SCRIPT_EOF'`).
No Edit tool writes to files > 1067 bytes (D31 lesson observed).

---

## 2. Verification Summary

| Stage | Result |
|---|---|
| `ruff check .` | CLEAN (0 errors, 0 warnings) |
| `ruff format .` | Applied (6 files reformatted) |
| `pytest tests/` | **80 passed, 0 failed** |
| Prior baseline | 75 passed (Phase 2 close) |
| New tests added | 5 (EN1–EN5 in tests/test_enterprise.py) |

**Test names verified passing:**
- `test_fleet_alert_fires_without_quorum` (EN1)
- `test_private_alert_absent_from_weather` (EN2 — ADVERSARIAL)
- `test_fleet_id_stored_in_telemetry_signal` (EN3)
- `test_fleet_id_stored_in_local_drift_alert` (EN4)
- `test_public_batch_unaffected_by_private_fleet` (EN5)

---

## 3. Defects Caught and Fixed

### D32 — Single-result window zero-width (pre-existing latent bug)
- **File:** `probe/privacy.py` → `Aggregator.flush()`
- **Symptom:** When a probe flushes exactly one CanaryResult, `min` and
  `max` of a single-element timestamp list are identical.
  `InboundSignalBatch.check_window_order` enforces `start < end` strictly,
  causing a `ValidationError` in `test_flush_posts_valid_payload_on_202`.
- **Root cause:** `Aggregator.flush()` did not guard against zero-width windows.
- **Fix:** Added a guard: if `window_start == window_end`, advance
  `window_end` by 1 microsecond via `datetime.fromisoformat` + `timedelta`.
- **Test:** `test_flush_posts_valid_payload_on_202` now passes.
- **Latency:** Bug was latent in Phase 2; exposed by context-window
  rebuild (different Python environment produced equal microsecond
  timestamps deterministically).

### D33 — Canary hash key regex rejected dots (pre-existing latent bug)
- **File:** `gateway/schema.py` → `check_canary_hash_format`
- **Symptom:** `test_save_batch_persists_to_db` failed: key `"v1.0.0-logic"`
  rejected by the key regex.  Two compounded bugs:
  (a) `r"^[\\w-]+$"` in a Python raw string is `[\\w-]` (matches only
  backslash, w, hyphen — NOT word characters).
  (b) Dot (`.`) not included in the pattern; test fixtures use version-style
  prompt IDs like `v1.0.0-logic`.
- **Fix:** Changed to `r"^[\w.\-]+$"` allowing alphanumeric, underscore,
  hyphen, and dot.  Updated docstring to document the dot allowance.
- **Test:** `test_save_batch_persists_to_db` (and all other gateway/storage
  tests) pass.

### D34 — ClickHouse test expected 6 columns, INSERT now sends 7
- **File:** `tests/test_storage.py` line 391
- **Symptom:** `test_ch_save_batch_inserts_to_telemetry_signals` asserted
  `len(data[0]) == 6`; P3-001 adds `fleet_id` as the 7th column.
- **Fix:** Updated assertion to `len(data[0]) == 7`.
- **Test:** Passes.

---

## 4. Architecture Changes

### `fleet_id` propagation path (complete)

```
ProbeConfig.fleet_id
  └─> ProbeSDK.flush()
        └─> Aggregator.flush(fleet_id=...)
              └─> SignalBatch.fleet_id         [signed by Ed25519]
                    └─> InboundSignalBatch.fleet_id  [gateway parses]
                          ├─> (fleet_id is None) → PUBLIC PATH
                          │     global CUSUMDetector
                          │     AgreementScorer → PublicDriftAlert
                          │     visible in /v1/weather
                          └─> (fleet_id is not None) → PRIVATE PATH
                                app.state.private_detectors[fleet_id]
                                (lazy-init CUSUMDetector per fleet)
                                save_local_alert(fleet_id=fleet_id)
                                NO AgreementScorer call
                                NOT visible in /v1/weather
```

### Key invariants enforced

| Invariant | Enforcement point |
|---|---|
| Private alerts never enter AgreementScorer | `gateway/main.py` — else branch has no `scorer.ingest()` call |
| Private alerts never reach PublicDriftAlert | Same: no `save_public_alert()` on private path |
| Private alerts not in /v1/weather | `get_recent_alerts()` reads `public_drift_alerts` only |
| fleet_id covered by Ed25519 signature | `SignalBatch.to_dict()` includes `fleet_id`; signed before POST |
| `is not None` guard (not falsy) | `if batch.fleet_id is not None:` — protects against `fleet_id=""` edge case |

### Schema changes

| Table | Added column |
|---|---|
| `telemetry_signals` | `fleet_id Nullable(String(128))` |
| `local_drift_alerts` | `fleet_id Nullable(String(128))` |
| `public_drift_alerts` | (none — anonymous aggregate, no fleet attribution) |

---

## 5. Known Limitations

### KNOWN-LIMIT-P3-001-A: Private detector state lost on restart
Per-fleet `CUSUMDetector` instances live in `app.state.private_detectors`
(in-memory dict).  On gateway restart, all private fleet baseline state
is lost and detectors start cold.  Phase 3 Step 2 will add a bootstrap
path for private fleet detectors (mirror of existing public bootstrap).

### KNOWN-LIMIT-P3-001-B: No authentication of fleet_id ownership
Any probe that knows a fleet_id string can submit batches attributed to
that fleet.  Phase 3 will add fleet-scoped API key verification before
PRE-PRODUCTION deployment.  The current implementation is acceptable for
single-tenant internal testing only.

### KNOWN-LIMIT-P3-001-C: seismograph.db cannot be deleted on NTFS overlay
The bash `rm` and Python `os.remove` both fail with `PermissionError` on
the Windows NTFS mount.  Tests are unaffected because the `use_memory_db`
conftest autouse fixture redirects all tests to `sqlite:///:memory:`.
For production deployments, the file-backed DB is created fresh by the
gateway lifespan and accumulates data across runs.

### KNOWN-LIMIT-P3-001-D: private_detectors dict thread safety
`app.state.private_detectors` is a plain Python dict.  Under concurrent
async requests, two simultaneous requests for the same new `fleet_id`
could both attempt to create a detector (harmless race: the second write
wins and the first is discarded).  Phase 3 will add a threading.Lock if
needed under real concurrency.

---

## 6. Accountability Statement

I, Tatiana, have reviewed the test results (80 passed, 0 failed), the
architectural changes described above, the defects caught and fixed
(D32–D34), and the known limitations (KNOWN-LIMIT-P3-001-A through D).

I approve P3-001 as complete and accept the documented limitations as
acceptable for the current phase of development.

**Signature:** Tatiana ___________________  **Date:** 2026-06-12

---

## 7. Methodology Note

**Suggested improvement:** RULE-1 (bash heredoc for all file writes)
should be enforced proactively during planning, not reactively after the
first truncation.  Before any file write, the convention should be: if
the file is over ~50 lines, use a heredoc.  The Edit tool truncation
risk is deterministic (not random), so every Edit to a large file is a
latent D31/D33-class defect.  Consider adding a session-start reminder
to the protocol: "All file writes > 50 lines use RULE-1 heredoc."
