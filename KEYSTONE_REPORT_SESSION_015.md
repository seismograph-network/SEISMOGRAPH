# KEYSTONE REPORT — SESSION 015
## Task: P3-003 Distributed Reliability & Tech Debt (Partial)

### Scope: KNOWN-LIMIT-003 (Redis atomic quorum) + KNOWN-LIMIT-005 (persistent DP budgets)

---

## 1. Provenance

| File | Status | Notes |
|---|---|---|
| `engine/scorer_redis.py` | AI-generated (rewrite) | Added `_PROMOTE_LUA_SCRIPT`; refactored `promote_to_public_alert()` to use `eval()` |
| `probe/privacy.py` | AI-generated (edit) | `DPAccountant` accepts `storage_path`; added `_persist()` / `_load()` methods |
| `probe/sdk.py` | AI-generated (edit) | `ProbeSDK.__init__` passes `storage_path=".seismograph_dp.json"` to `DPAccountant` |
| `tests/test_scorer_redis.py` | AI-generated (rewrite) | RS4/RS5/RS6/RS8 updated to mock `eval` instead of `scard`; RS11 added |
| `tests/test_privacy.py` | AI-generated (append) | Added DP-PERSIST and DP-PERSIST-ADV tests |

Human editorial changes: none.

---

## 2. Verification Summary

**Test suite:** 91/91 passed (1 warning: Starlette httpx deprecation, not actionable)

**Ruff:** 0 violations, 36 files formatted/checked

**Tests by component:**

| Component | Tests | Result |
|---|---|---|
| Redis scorer: ingest + expire | RS1, RS2, RS3, RS10 | PASS |
| Redis scorer: atomic promote (Lua EVAL) | RS4, RS5, RS6, RS11 | PASS |
| Redis scorer: clear, Sybil adversarial | RS7, RS8 | PASS |
| Redis scorer: key format | RS9 | PASS |
| DPAccountant: budget persistence (restart) | DP-PERSIST | PASS |
| DPAccountant: corrupt file graceful fallback | DP-PERSIST-ADV | PASS |

---

## 3. Changes in Detail

### KNOWN-LIMIT-003 fix: Redis atomic quorum (engine/scorer_redis.py)

**Root cause of race:** The prior `promote_to_public_alert()` issued a
non-atomic `SCARD` followed by a separate gateway-side `DEL` (via `clear()`).
In a multi-node deployment, two gateway instances could both observe
`SCARD >= QUORUM_MIN` before either called `DEL`, resulting in duplicate
`PublicDriftAlert` records for the same drift event.

**Fix:** Added `_PROMOTE_LUA_SCRIPT` — a Redis Lua script that atomically:
1. Calls `SCARD` on the quorum key.
2. If count >= quorum, calls `DEL` and returns the count.
3. Otherwise returns 0.

`promote_to_public_alert()` now calls `redis_client.eval(_PROMOTE_LUA_SCRIPT,
1, key, self.quorum)`. Redis guarantees Lua script execution is single-threaded
and atomic: no other command can observe the key between the SCARD check and
the DEL.

**Backward compatibility:** `clear()` is retained and remains safe to call
after a successful promotion (Redis DEL on an already-deleted key is a no-op).
The gateway's existing `scorer.clear()` call after `promote_to_public_alert()`
is now a harmless no-op, requiring no change to `gateway/main.py`.

### KNOWN-LIMIT-005 fix: Persistent DP budgets (probe/privacy.py, probe/sdk.py)

**Root cause of limitation:** `DPAccountant` held `current_spend` and
`window_start_time` only in memory. A probe process restart silently reset
the budget to 0.0, allowing more than `daily_budget / FLUSH_EPSILON` flushes
per 24-hour window without detection.

**Fix:**
- `DPAccountant.__init__` accepts `storage_path: str | None = None`.
- `_persist()` writes `{"current_spend": float, "window_start": str}` to
  the file atomically via `os.replace()` on a `.tmp` file (avoids partial
  writes on crash). Called after every `spend()` and after a window reset
  in `reset_if_needed()`.
- `_load()` reads and restores state on `__init__`. Falls back to 0.0 / now
  on any exception (file absent, invalid JSON, missing keys).
- `ProbeSDK` passes `storage_path=".seismograph_dp.json"` by default.

---

## 4. Defects Caught During This Session

None. All tests passed on the first run after implementation.

One ruff-format issue (I001 import sort in test_scorer_redis.py) was caught
by `ruff check` and fixed with `ruff format`. One E501 in test_privacy.py
was fixed by removing the unnecessary `pytest.TempPathFactory` type
annotation from the `tmp_path` fixture parameter.

---

## 5. Known Limitations

**Closed by this session:**
- KNOWN-LIMIT-003: Redis SCARD/DEL race in multi-node deployment. CLOSED.
- KNOWN-LIMIT-005: DP budget lost on process restart. CLOSED.

**Still open:**
- KNOWN-LIMIT-001: Per-metric_name Redis key granularity deferred.
- KNOWN-LIMIT-004: Clock skew may shorten effective DP window.
- KNOWN-LIMIT-P3-001-A through D: Inherited from P3-001.
- KNOWN-LIMIT-P3-002-A through E: Inherited from P3-002.
- Remaining P3-003 scope (BayesianOnlineDetector, KNOWN-LIMITs not covered
  here) is tracked in memory/project_open_tasks.md as P3-003 (partial).

---

## 6. Privacy Invariant Check

- `_persist()` writes only `current_spend` (float) and `window_start`
  (ISO datetime string). No raw prompts, outputs, model tuples, or org
  identifiers are written to the file. PASS
- `_PROMOTE_LUA_SCRIPT` accesses only Redis keys derived from public model
  identifiers. No org secrets in any Redis key or value. PASS

---

## 7. Accountability Statement

I have reviewed the Keystone Report for P3-003 Distributed Reliability &
Tech Debt (Session 015). KNOWN-LIMIT-003 and KNOWN-LIMIT-005 are closed.
91/91 tests pass, ruff clean.

Tatiana ___________________ Date: 2026-06-12

---

## 8. Methodology Note

The atomic Lua pattern (`EVAL` with KEYS/ARGV) is now the standard for any
Redis check-then-act operation in SEISMOGRAPH. Future multi-step Redis
operations (e.g., quorum TTL reset + cardinality check) should follow the
same pattern rather than issuing sequential commands from application code.
