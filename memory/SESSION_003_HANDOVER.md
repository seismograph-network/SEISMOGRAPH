# SEISMOGRAPH -- Phase 2 Handover
# Document: memory/SESSION_003_HANDOVER.md
# Written: 2026-06-12 (Session 006 close)
# Purpose: Dense context brief for the first Phase 2 session.
# Status: WORKSPACE CLEANLY PARKED. 23/23 tests passing. Ruff: 0 violations.

---

## Phase 1 victory -- what is live as of 2026-06-12

Phase 1 (Solo MVP) is 100% COMPLETE, signed off by Tatiana.

### Live capabilities

| Component | File | Status |
|---|---|---|
| FastAPI gateway | gateway/main.py | LIVE |
| Ingestion endpoint | POST /v1/signals | LIVE |
| CUSUM detector | engine/detector.py | LIVE (h=5.0, k=0.5, baseline_samples=30) |
| Agreement scorer | engine/correlation.py | LIVE (QUORUM_MIN=2) |
| Model weather API | GET /v1/weather | LIVE |
| Vanilla JS dashboard | GET / | LIVE (60s polling, dark-mode) |
| SQLite storage | engine/models.py | LIVE (3 tables) |
| Probe SDK | probe/sdk.py | LIVE (OTel spans, Laplace DP noise, flush) |
| End-to-end demo | scripts/demo_simulation.py | LIVE |
| Test suite | tests/ | 23/23 PASS |

### Database schema (current)

```
local_drift_alerts     -- private per-org CUSUM events
  id, timestamp, model_tuple, metric_name, alert_value, client_id

public_drift_alerts    -- quorum-verified public events
  id, timestamp, model_tuple, metric_name, contributing_org_count

telemetry_signals      -- raw DP-noised ingested batches
  id, timestamp, batch_id, client_id, model_tuple,
  suite_version_hash, result_count, metrics (JSON)
```

### Key invariants (enforced, verified by T10/T11)

1. A single-org CUSUM alert NEVER becomes a public alert.
   `get_recent_alerts()` queries ONLY `public_drift_alerts`.
   `local_drift_alerts` is private fleet data -- never surfaced in the weather API.

2. Raw prompts and raw model outputs NEVER leave the probe perimeter.
   Only SHA-256 hashes, distributional features, and Laplace DP-noised
   aggregates are transmitted. Enforced by `probe/privacy.py` Aggregator
   and the gateway `ALLOWED_METRIC_KEYS` whitelist.

3. CUSUM state is per `(model_tuple, metric_name)`, shared across client_ids.
   The baseline is built from any org's stable batches -- intentional for
   cross-org comparability.

4. `promote_to_public_alert()` returns `int | None` (org count or None).
   Never raises ValueError. Returns None when model_tuple has no pending results.

5. Naive UTC timestamps throughout (SQLite has no timezone type).
   Pattern: `datetime.now(timezone.utc).replace(tzinfo=None)`.
   Never use `datetime.UTC` (Python 3.11+ only -- sandbox is 3.10).

---

## Operational invariants -- read before touching any file

### RULE-1: NTFS write path (non-negotiable)

ALL file creation and editing on the SEISMOGRAPH mount uses bash heredoc.
The Write and Edit tools silently truncate files >~1067 bytes on the Windows
NTFS mount. This has been confirmed multiple times and is a hard rule.

Correct pattern:

```bash
python3 -B - << 'SCRIPT_EOF'
content = """...(file content)..."""
with open('/sessions/.../file.py', 'w', newline='\n') as fh:
    fh.write(content)
SCRIPT_EOF
```

If the file content itself contains triple-double-quotes (e.g. docstrings),
split into segments and concatenate using: `tq = '"' * 3` as a variable.
If the file content contains triple-single-quotes, use `"""..."""` delimiter.
Never put the outer heredoc marker word (SCRIPT_EOF) inside the content.

Critical details:
- Use `newline='\n'` in all `open()` calls (NTFS line ending normalization).
- `\n` inside a triple-quoted Python string in the heredoc becomes a literal
  newline in the output file. To get the two-char sequence `\n` in the output,
  use `\\n` in the heredoc, OR restructure to avoid inline \n entirely.
- Never use the Write or Edit tools for any file > ~1KB.

### Ruff

Binary: `/sessions/zealous-inspiring-dijkstra/.local/bin/ruff`
Config: `pyproject.toml` at repo root.
  - `line-length = 79`
  - `target-version = "py311"` (ruff target; runtime is 3.10)
  - `ignore = ["UP017"]` (prevents datetime.UTC upgrade that breaks 3.10)
  - SG-TRACE lines: add `# noqa: E501` if they exceed 79 chars.

Always run both after any Python file change:
```bash
/sessions/zealous-inspiring-dijkstra/.local/bin/ruff check --fix <file>
/sessions/zealous-inspiring-dijkstra/.local/bin/ruff format <file>
```

### Python 3.10 compatibility

- Use `timezone.utc`, NOT `datetime.UTC` (3.11+ only).
- Use `from __future__ import annotations` for `X | Y` union types.
- Import `Optional` from `typing` for runtime use (not just annotations).
- `StaticPool` from `sqlalchemy.pool` for in-memory SQLite in tests.
- `conftest.py` autouse fixture: `SEISMOGRAPH_DB_URL = "sqlite:///:memory:"`.

### Pydantic v2 schemas (gateway/schema.py)

- All schemas: `model_config = ConfigDict(extra="forbid", frozen=True)`.
- `ALLOWED_METRIC_KEYS = frozenset({"avg_output_length", "json_success_rate", "result_count"})`.
- MUST stay in sync with `probe/privacy.py` ALLOWED_METRIC_KEYS.
  New metric keys require updating BOTH files + adding a test.

### SQLAlchemy 2.0 patterns

- `DeclarativeBase`, `Mapped`, `mapped_column` (not legacy `Column`).
- `with self._db.session() as sess:` -- session auto-commits.
- All queries: `select()` + `sess.scalars().all()`.

### Test patterns

- `TestClient(app)` as context manager -- required for lifespan.
- After `with TestClient(app) as c:`, replace detector/scorer for fast tests:
  `app.state.detector = CUSUMDetector(h=5.0, k=0.5, baseline_samples=3)`
  `app.state.scorer = AgreementScorer()`
  Bypasses the 30-batch baseline requirement.
- Reuse VALID_PAYLOAD from test_gateway.py -- do not redefine.

---

## Phase 2 roadmap -- priority order

### P2-001: Ed25519 Sybil Resistance (REQ-PRIV-002) -- SECURITY BLOCKER

Phase 2 priority zero. No public multi-org deployment until done.

Current state: `gateway/auth.py` `verify_signature()` always returns `True`.
Any client can inject arbitrary client_ids and fabricate multi-org quorum.

Implementation plan:
1. `probe/sdk.py`: Generate Ed25519 keypair at SDK init (configurable path).
   Sign every `SignalBatch` with private key.
   Include public key + signature in outbound batch.

2. `gateway/auth.py`: Implement `verify_signature(batch, public_key, sig)`.
   Use `cryptography.hazmat.primitives.asymmetric.ed25519`.
   `cryptography` is already in gateway deps -- just not wired.

3. `engine/reputation.py` (new): Probe reputation scorer.
   New keys start at weight=0.1 (not 0 -- allows gradual trust building).
   Weight increases with consistent, non-anomalous contributions.
   AgreementScorer uses weighted sum, not raw org count.

4. Update `AgreementScorer.promote_to_public_alert()`:
   `sum(reputation[org] for org in agreeing_orgs) >= QUORUM_WEIGHT_THRESHOLD`
   QUORUM_WEIGHT_THRESHOLD value requires Tatiana approval before implementation.

5. Required adversarial test: new key (reputation=0.1) cannot single-handedly
   trigger a public alert even if it fires CUSUM.

Files: `gateway/auth.py`, `gateway/main.py`, `probe/sdk.py`,
`engine/correlation.py`, new `engine/reputation.py`.
Architectural decision REQ-PRIV-002 is APPROVED (Keystone Report Session 001).
Implementation details (key storage, reputation model) need Tatiana sign-off.

---

### P2-002: ClickHouse Migration

SQLite single-writer limitation blocks horizontal gateway scaling.

Migration plan:
1. Add `clickhouse-connect` to dependencies.
2. Create `engine/clickhouse.py` with ClickHouse session/query patterns.
3. Migrate `telemetry_signals` first (append-only, write-heavy).
4. Migrate `public_drift_alerts` (append-only, read-heavy).
5. Keep `local_drift_alerts` in SQLite/Postgres (per-org, never federated).
6. Dispatch backend via `SEISMOGRAPH_DB_URL` env var prefix:
   `clickhouse://` vs `sqlite:///` vs `postgresql://`.

---

### P2-003: Distributed CUSUM / Scorer State (Redis)

Current limitation: gateway restart clears all CUSUM accumulators.
`bootstrap_detector()` restores baseline (mu0, sigma0) but not in-flight S+/S-.
AgreementScorer pending contributors also lost on restart.

Design (Redis):
- Serialize CUSUMDetector state per (model_tuple, metric_name) to Redis hash.
  Restore atomically on startup.
- Serialize AgreementScorer._pending to Redis with 1h TTL per quorum window.
- Adds Redis dependency. Tatiana approval needed.

---

### P2-004: DP Sequential Composition Tracking (REQ-PRIV-010)

Current state: epsilon=2.0 per flush, no cross-flush budget tracking.

Implementation:
1. Add `PrivacyBudget` class to `probe/privacy.py`.
   Track cumulative epsilon per (client_id, calendar_day).
   Refuse to flush when daily budget exhausted.
2. Daily budget value: Tatiana approval needed.
   Strawman: epsilon=10/day -> 5 flushes/day at epsilon=2.0 each.
   For hourly probing: reduce per-flush epsilon to 0.4.

---

### P2-005: OTel/MCP Adapters

OTel exporter: `SeismographSpanExporter` implements
`opentelemetry.sdk.trace.export.SpanExporter`.
Processes completed spans with `gen_ai.*` attributes from any framework.
Target: LangChain, LlamaIndex, any OTel GenAI conventions framework.

MCP tool set:
- `seismograph_flush` -- flush current probe window to gateway
- `seismograph_weather` -- query drift status for a model tuple
- `seismograph_configure` -- set gateway endpoint, model tuple, suite hash

---

## File state at park (2026-06-12)

```bash
cd SEISMOGRAPH
python -m pytest tests/ -q    # expect: 23 passed
ruff check .                  # expect: All checks passed!
ruff format --check .         # expect: All checks passed!
```

Last-modified task per key file:
```
gateway/main.py                  P1-005 (AgreementScorer wiring)
engine/correlation.py            P1-005 (promote_to_public_alert -> int | None)
engine/models.py                 P1-005 (LocalDriftAlert + PublicDriftAlert)
engine/repository.py             P1-005 (save_local_alert, save_public_alert)
tests/test_gateway.py            P1-005 (T10/T11 adversarial quorum tests)
tests/test_storage.py            P1-005 (LocalDriftAlert import)
scripts/demo_simulation.py       P1-006 (two-client quorum demo)
README.md                        P1-007 (launch version)
KEYSTONE_REPORT_SESSION_001.md   P1-007 (Phase 1 sign-off appended)
memory/SESSION_003_HANDOVER.md   P1-007 (this file)
```

---

## How to start a Phase 2 session

1. Read `memory/project_open_tasks.md` (open tasks + phase roadmap).
2. Read this document (SESSION_003_HANDOVER.md).
3. Read `SEISMOGRAPH_Architecture.md` sections 10-11 (security, open decisions).
4. Run `pytest tests/ -q` + `ruff check .` to confirm clean state.
5. Unless Tatiana redirects: start with P2-001 (Ed25519 Sybil resistance).

Do NOT start Phase 2 without:
- Confirming RULE-1 (all files written via bash heredoc).
- Confirming Python 3.10 compatibility constraints (see above).
- Getting Tatiana sign-off on Ed25519 implementation details (key storage,
  reputation model) before writing any code.

---

## Standby mode

Workspace is cleanly parked. All tasks complete. All tests pass.
SEISMOGRAPH Phase 1 is production-ready for single-node solo deployment.

NOT safe for public multi-org deployment until P2-001 (Ed25519) is complete.
A malicious actor can fabricate multi-org quorum agreement trivially until then.

Awaiting Phase 2 session start signal from Tatiana.
