# SESSION 012 HANDOVER — Phase 3 Entry Brief
# SEISMOGRAPH | Director: Tatiana | Lead Technical Co-Pilot: Claude
# Generated: 2026-06-12 | Phase 2 declared 100% COMPLETE by Tatiana
# ============================================================

## STATUS SNAPSHOT

- **Current phase:** 3 — Enterprise Plane (entering)
- **Test baseline:** 75/75 PASSED, 31 Python files, 0 ruff violations
- **Ruff:** `~/.local/bin/ruff`
- **Pytest:** `PATH="$HOME/.local/bin:$PATH" pytest -p no:cacheprovider`
- **Repo root:** `D:\Dev\Projects\SEISMOGRAPH`
  Linux sandbox: `/sessions/sharp-peaceful-feynman/mnt/SEISMOGRAPH/`

---

## OPERATIONAL INVARIANTS — READ BEFORE TOUCHING ANY FILE

### RULE-1: All file writes via bash heredoc — NO EXCEPTIONS

Edit/Write tools truncate NTFS overlay files at ~1067 bytes.
Every file creation or modification MUST use either:

```
python3 -B - << 'SCRIPT_EOF'
with open("/sessions/.../SEISMOGRAPH/path.py", "w") as fh:
    fh.write(content)
SCRIPT_EOF
```

Or for very large files, write to /tmp then copy:
```
cat > /tmp/write_file.py << 'PYEOF'
<code here>
PYEOF
python3 -B /tmp/write_file.py
```

D31 (Session 010): Edit tool truncated pyproject.toml at line 53.
Never use Edit/Write on any file in this repo.

Verify line lengths before writing (ruff enforces 79-char limit on ALL
lines including docstrings):
```python
long = [(i+1, len(l), l) for i, l in enumerate(lines) if len(l) > 79]
```

### Python version: 3.10 sandbox, 3.11 target

- pyproject.toml: `requires-python = ">=3.11"`
- Sandbox executes on Python 3.10.12
- Use `timezone.utc` not `datetime.UTC` (UP017 suppressed in ruff)
- `is not None` guard for any numeric variable that may be zero
  (D30 lesson — `if span.start_time:` fails when start_time == 0)

### Pydantic v2

- `InboundSignalBatch`: `extra="forbid"` — unknown fields → 422
- `SignalBatch.__post_init__`: metric key whitelist enforced

### Constructor patterns (D28, D29 defect history)

```python
KeyManager(key_path=Path(...))              # NOT key_file=
ProbeConfig(
    model_tuple=...,
    suite_version_hash=...,                 # required
    gateway_endpoint="http://host/v1/signals",  # NOT gateway_url
)
```

---
## PHASE 2 VICTORY — COMPLETE SYSTEM STATE

### P2-001 Ed25519 Cryptographic Identity (Session 007)
- `probe/crypto.py`: KeyManager — generates/loads Ed25519 keypair;
  `sign(bytes) -> hex`; `public_key_hex` property.
- `gateway/auth.py`: `verify_batch_signature(batch, sig_hex, pubkey_hex)`
  — verifies over `canonical_json(batch)` bytes.
- `probe/sdk.py`: flush() signs each SignalBatch; `x-signature` and
  `x-public-key` headers.
- `gateway/main.py`: 401 on unsigned/invalid batches.
- Tests: test_crypto.py + test_sdk.py + test_gateway.py. 36/36 at close.

### P2-002 ClickHouse Time-Series Migration (Session 007)
- `engine/repository.py`: SignalRow + AlertRow dataclasses; BaseRepository
  ABC (6 abstract methods: save_batch, save_local_alert, save_public_alert,
  get_recent_signals, get_recent_alerts, get_all_model_tuples).
  SignalRepository(BaseRepository) for SQLite.
- `engine/clickhouse.py`: ClickHouseRepository(BaseRepository);
  setup_tables() — 3 MergeTree tables, CREATE TABLE IF NOT EXISTS.
- `gateway/main.py`: STORAGE_BACKEND env var → clickhouse|sqlite routing.
- Tests: test_storage.py CU1-CU7 (mocked ClickHouse). 43/43 at close.

### P2-003 Redis Distributed State (Session 008)
- `engine/scorer_redis.py`: RedisAgreementScorer — Redis Set keyed
  `sg:quorum:{model_tuple}`; SADD + EXPIRE 86400s; SCARD for quorum;
  DEL on clear. QUORUM_MIN=2 (from AgreementScorer).
- `gateway/main.py`: QUORUM_BACKEND=redis → RedisAgreementScorer;
  REDIS_URL default redis://localhost:6379/0.
- Tests: RS1-RS10. RS8 ADVERSARIAL: same org_id twice → set dedup → 1
  member → quorum blocked. 53/53 at close.
- KNOWN-LIMIT-003: promote/clear race — Lua script required (P3-003a).

### P2-004 DP Composition Accounting (Session 009)
- `probe/privacy.py`: PrivacyBudgetExceededError; DPAccountant —
  daily_budget=10.0, spend(ε) raises before modifying state,
  reset_if_needed() wall-clock 24h, remaining property.
  Aggregator.clear_all().
- `probe/sdk.py`: FLUSH_EPSILON=2.0; _accountant injectable seam;
  flush() returns {"status": "budget_exceeded"} on exhaustion.
  Noop path (empty queue) skips accountant entirely.
- Tests: DP1-DP10. 63/63 at close.
- KNOWN-LIMIT-005: budget not persisted across probe restart (P3-003b).

### P2-005 OTel and MCP Adapters (Session 010)
- `probe/adapters/otel.py`: SeismographSpanProcessor(SpanProcessor).
  on_end() maps: gen_ai.system/request.model@response.model → model_tuple;
  SHA-256(gen_ai.response.id or span_id_int.to_bytes(8,"big")) → hash;
  gen_ai.usage.output_tokens → output_length;
  finish_reason=="stop" → json_valid;
  (end_time-start_time)//1_000_000 → latency_ms.
  CRITICAL: use `is not None` guard (not truthiness) for timing fields.
- `probe/adapters/mcp.py`: check_model_weather(model_tuple, base_url,
  http_client) → formatted string; TOOL_SCHEMA dict; run_mcp_server()
  — JSON-RPC 2.0 stdio (initialize/tools/list/tools/call).
- Tests: OT1-OT5+2, MC1-MC5. 75/75 at close.

### P2-006 Containerization & Orchestration (Session 011)
- `Dockerfile`: python:3.11-slim; seismograph user UID 1001; dep layer
  (pyproject.toml first); COPY engine/ gateway/ dashboard/ data/;
  probe/ excluded; HEALTHCHECK GET /v1/weather; CMD uvicorn --workers 1.
- `docker-compose.yml`: gateway (build., env vars, restart:on-failure),
  clickhouse (latest, 8123+9000, named vol), redis (alpine, 6379,
  appendonly, named vol). depends_on: service_started (KL-013).
- `.env.example`: STORAGE_BACKEND, SEISMOGRAPH_DB_URL, CLICKHOUSE_URL,
  QUORUM_BACKEND, REDIS_URL — all documented with defaults.

---
## CURRENT FILE INVENTORY (31 Python files + 3 infra)

```
probe/__init__.py
probe/canary.py           CanaryResult, SUITE_VERSION, CANARY_SUITE_V1
probe/canary_suite.py     CanarySuiteRegistry, CanarySuiteVersion
probe/crypto.py           KeyManager (Ed25519, PEM)
probe/privacy.py          SignalBatch, Aggregator, DPAccountant,
                          PrivacyBudgetExceededError, EPSILON=2.0
probe/sdk.py              ProbeSDK, ProbeConfig, FLUSH_EPSILON=2.0
probe/adapters/__init__.py
probe/adapters/otel.py    SeismographSpanProcessor(SpanProcessor)
probe/adapters/mcp.py     check_model_weather(), run_mcp_server()

engine/__init__.py
engine/correlation.py     AgreementScorer (LIVE), CUSUMDetector (stub),
                          BayesianOnlineDetector (stub, P0-005 deferred)
engine/detector.py        CUSUMDetector LIVE (Page-CUSUM, h=5.0, k=0.5)
engine/models.py          LocalDriftAlert, PublicDriftAlert (SQLAlchemy)
engine/repository.py      BaseRepository ABC, SignalRepository (SQLite),
                          SignalRow, AlertRow
engine/clickhouse.py      ClickHouseRepository(BaseRepository)
engine/scorer_redis.py    RedisAgreementScorer

gateway/__init__.py
gateway/auth.py           verify_batch_signature(), canonical_json()
gateway/main.py           FastAPI, POST /v1/signals, GET /v1/weather,
                          GET / dashboard, lifespan env-var routing
gateway/schema.py         InboundSignalBatch, ModelWeatherResponse (v2)

dashboard/static/index.html   dark-mode dashboard
dashboard/static/app.js       60s poll, DRIFTING pulse

tests/__init__.py, conftest.py
tests/test_canary.py
tests/test_correlation.py
tests/test_crypto.py
tests/test_gateway.py     T1-T11 (inc. quorum adversarial T10-T11)
tests/test_privacy.py     DP1-DP10
tests/test_scorer_redis.py RS1-RS10 (inc. Sybil RS8)
tests/test_sdk.py
tests/test_storage.py     CU1-CU7 (mocked ClickHouse)
tests/test_adapters.py    OT1-OT5+2, MC1-MC5

Dockerfile, docker-compose.yml, .env.example
pyproject.toml            deps: clickhouse-connect>=0.7, cryptography>=41.0,
                          opentelemetry-sdk>=1.20, redis>=4.0
```

---

## PHASE 3 ROADMAP — ENTERPRISE PLANE

### P3-001: Multi-Tenant Data Isolation

Goal: Each org sees only their own signals + anonymised public aggregates.

Build:
- Add `org_id: String(36)` to `signals` and `local_drift_alerts` in both
  SQLite (engine/models.py Alembic migration) and ClickHouse (ALTER TABLE
  ADD COLUMN or new DDL in setup_tables()).
- BaseRepository interface: save_batch(batch, org_id); get_recent_signals
  (model_tuple, org_id=None). None = global public view.
- Gateway: org_id derived from verified Ed25519 public key registry
  (new KeyRegistry table: pubkey_hex → org_id mapping).
- New endpoints: GET /v1/weather?scope=private (auth required, per-org);
  GET /v1/weather (public, quorum-verified only — unchanged).
- Adversarial: org_id=forged must be rejected by signature verification;
  key registry is the authoritative mapping source.
- KNOWN-LIMIT-011 resolved here: add [project.optional-dependencies.gateway]
  to pyproject.toml with fastapi, uvicorn, httpx.
- KNOWN-LIMIT-012 resolved here: add migration framework for ClickHouse DDL.

### P3-002: Automated Canary-Gated Rollback Webhooks

Goal: PublicDriftAlert → POST to subscriber CI/CD endpoints within 5s.

Build:
- WebhookSubscription model: org_id, model_tuple_pattern (glob),
  endpoint_url, secret (HMAC-SHA256), event_types.
- WebhookDispatcher.dispatch(alert: PublicDriftAlert) — HMAC-signed POST;
  3 retries with exponential backoff; non-blocking (BackgroundTasks).
- POST /v1/webhooks (auth required): register subscription.
- Adversarial: subscriber returns 500 or times out → ingest path unblocked;
  failed delivery logged with alert_id for re-delivery.

### P3-003: Phase 2 Tech Debt

3a. Lua atomic promote+clear for Redis (KNOWN-LIMIT-003):
    Replace SCARD+DEL with redis.eval() Lua script.
    sg_promote_and_clear.lua: SCARD → if >= quorum: DEL, return count.
    Test: 100 concurrent callers → exactly 1 PublicDriftAlert.

3b. Persist DP budget across restarts (KNOWN-LIMIT-005):
    RedisDPAccountant(DPAccountant): store current_spend +
    window_start_time in Redis (TTL=90000s). Injectable via _accountant.
    Test: probe spends 9.0ε, restarts, tries 2.0ε → raises.

3c. Metric-level quorum (KNOWN-LIMIT-001):
    Key: sg:quorum:{model_tuple}:{metric_name}.
    Requires ChangePointResult.metric_name field addition.
    TATIANA APPROVAL REQUIRED before touching ChangePointResult interface.

### P3-004: Audit-Grade Incident Export (SOC 2)

Build:
- AuditLog table: id UUID, alert_id FK, exported_at, sha256_hash,
  Ed25519 signature of export payload (gateway key).
- GET /v1/audit/incidents?from=ISO&to=ISO → JSON-L, each line signed.
  Auth: admin API key (separate from probe Ed25519 keys).
- GET /v1/audit/incidents/{alert_id} → single signed record.
- Adversarial: tampered ClickHouse record → 409 Conflict,
  "integrity violation" error.

---

## KNOWN LIMITATIONS TABLE

| ID | Description | Resolution Target |
|---|---|---|
| KL-001 | Metric-level quorum granularity | P3-003c (Tatiana approval req.) |
| KL-002 | No Redis startup ping | P3-001 infra |
| KL-003 | promote/clear race in Redis | P3-003a (Lua script) |
| KL-004 | DP window wall-clock (hibernate risk) | P3-003b |
| KL-005 | DP budget lost on probe restart | P3-003b (RedisDPAccountant) |
| KL-006 | FLUSH_EPSILON per-flush not per-model | Phase 3 review |
| KL-007 | Aggregator not thread-safe | P3 probe hardening |
| KL-008 | MCP server blocking stdio | P3 MCP SDK migration |
| KL-009 | Weather filter is client-side O(n) | P3-001 (query param) |
| KL-010 | Fixed suite_version in OTel adapter | P3 configurable param |
| KL-011 | fastapi/uvicorn not in pyproject.toml | P3-001 |
| KL-012 | No ClickHouse migration framework | P3-001 |
| KL-013 | depends_on service_started not healthy | P3 infra |
| KL-014 | HEALTHCHECK uses python3 PATH | P3 infra |

---

## SESSION START PROTOCOL (Phase 3)

1. `cat memory/project_open_tasks.md` — load Phase 3 backlog.
2. Re-read this file (SESSION_012_HANDOVER.md).
3. Run `PATH="$HOME/.local/bin:$PATH" pytest -q` — confirm 75/75 baseline.
4. State: "Last done: P2-006 complete, Phase 2 signed off 2026-06-12.
   First Phase 3 task: P3-001 Multi-Tenant Data Isolation.
   Ready to begin — confirm or redirect."

## SESSION END PROTOCOL (Phase 3)

1. Explicitly check every task proposed at session start.
2. "Not done — [task] still pending. Continue or defer?"
3. Never wrap up silently.
4. Log outcomes to project_session_log.md only after Tatiana confirms.

---

## PHASE 2 SIGN-OFF (on record)

Phase 2 declared 100% COMPLETE by Tatiana on 2026-06-12.
Keystone Report + signature: memory/KEYSTONE_REPORT_SESSION_011.md,
Section 6 (Phase 2 Formal Completion Declaration).
