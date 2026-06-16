# Changelog

All notable changes to SEISMOGRAPH are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0-rc.1] - 2026-06-12

### Summary

First release candidate of SEISMOGRAPH — a federated, privacy-preserving
early-warning network that detects semantic behavioral drift in third-party
LLM/agent APIs by correlating lightweight canary probe signals across
organizations.

---

### Phase 0 — Validation & Backtest Proof

- **CUSUM change-point detector** (`engine/detector.py`): `CUSUMDetector`
  with configurable threshold `h`, slack `k`, and baseline sample count.
  Produces `DriftAlert` dataclass with `cusum_score`, `direction`,
  `threshold`, and `window_count` fields.
- **Backtest notebook** (`notebooks/seismograph_backtest.ipynb`):
  Reproducible proof-of-concept demonstrating detection lead-time advantage
  against the Anthropic August–September 2025 outage postmortem.
  Confirms SEISMOGRAPH surface-level signal precedes public disclosure.
- **Canary suite v0** (`probe/canary_suite.py`): 200-prompt, temperature-0
  canary set with SHA-256 content-addressed versioning. Append-only
  baseline corpus; historical baselines are never mutated.
- **Architecture document** (`SEISMOGRAPH_Architecture.md`): Full system
  design, threat model, privacy invariants, and phase roadmap.
- **OTel instrumentation** (`probe/adapters/otel.py`): OpenTelemetry GenAI
  semantic convention spans (`gen_ai.*`) emitted per canary probe execution.

---

### Phase 1 — Solo MVP / Public Good

- **FastAPI ingestion gateway** (`gateway/main.py`): `POST /v1/signals`
  endpoint accepting `InboundSignalBatch` (Pydantic-validated). Returns
  202 with alert list. Per-batch Ed25519 signature verification stub.
- **SQLite persistence layer** (`engine/repository.py`): `SignalRepository`
  backed by SQLAlchemy ORM. Tables: `telemetry_signals`, `local_drift_alerts`,
  `public_drift_alerts`. `StaticPool` for in-memory test isolation.
- **CUSUMDetector bootstrap** (`gateway/main.py`): Lifespan hook warms all
  known model-tuple detectors from persisted signal history on restart.
- **Federated quorum agreement** (`engine/correlation.py`): `AgreementScorer`
  and `InMemoryAgreementScorer` — promotes a cross-org signal to a
  `PublicDriftAlert` only when `QUORUM_MIN` distinct organizations agree.
  Sybil-resistant by construction: single-org signals never become public.
- **Model Weather dashboard** (`gateway/main.py`, `gateway/static/`):
  `GET /v1/weather` returns per-model STABLE/DRIFTING status. Vanilla JS
  frontend polls the endpoint and renders a live drift map.
- **End-to-end demo** (`scripts/demo_simulation.py`): Scripted 3-org
  federation simulation demonstrating full detect-correlate-publish flow.

---

### Phase 2 — Network Growth & Hardening

- **Ed25519 cryptographic identity** (`probe/crypto.py`): `KeyManager`
  generates/loads Ed25519 keypairs. `sign_batch()` and `verify_signature()`
  sign/verify the canonical JSON of each `InboundSignalBatch`. Sybil
  resistance: forged batches rejected at the gateway signature check.
- **ClickHouse time-series backend** (`engine/clickhouse.py`):
  `ClickHouseRepository(BaseRepository)` — MergeTree tables for
  `telemetry_signals`, `local_drift_alerts`, `public_drift_alerts`.
  Activated via `STORAGE_BACKEND=clickhouse` env var. Backend-neutral
  gateway via `BaseRepository` ABC.
- **Redis distributed state** (`engine/scorer_redis.py`):
  `RedisAgreementScorer` stores per-model quorum sets in Redis with an
  atomic Lua `EVAL` script (`_PROMOTE_LUA_SCRIPT`) eliminating the
  SCARD/DEL race under multi-node gateway deployments. Activated via
  `QUORUM_BACKEND=redis`.
- **Differential privacy composition** (`probe/privacy.py`):
  `DPAccountant` tracks per-probe epsilon spend with a 24-hour rolling
  window. `PrivacyBudgetExceededError` halts flush when daily budget
  exhausted. Budget persists across restarts when `dp_storage_path` is
  configured in `ProbeConfig`.
- **OTel/MCP adapters** (`probe/adapters/`): `OTelAdapter` emits
  `gen_ai.*` spans to any OTLP gRPC collector. `MCPAdapter` maps
  MCP tool-call events to `mcp.*` semantic convention spans.
- **Containerization** (`Dockerfile`, `docker-compose.yml`, `.env.example`):
  Multi-stage Docker build (builder → runtime, non-root user). Compose
  stack wires gateway, Redis, and ClickHouse with health checks and
  named volumes.

---

### Phase 3 — Enterprise Grade

- **Multi-tenant fleet isolation** (`gateway/main.py`, `engine/models.py`):
  `fleet_id` field propagated from `InboundSignalBatch` through
  `TelemetrySignal` and `LocalDriftAlert`. Per-fleet `CUSUMDetector`
  instances in `app.state.private_detectors`. Private alerts never
  surface via `GET /v1/weather`.
- **Asynchronous rollback webhooks** (`engine/webhooks.py`,
  `gateway/main.py`): `POST /v1/webhooks` registers fleet webhook
  endpoints (stored in `WebhookConfig` table). `WebhookDispatcher`
  delivers `DriftNotification` JSON via async `httpx`. Canary-gated
  rollback: webhook fires on every private fleet drift alert, enabling
  automated CI/CD rollback integration.
- **Atomic Redis quorum promotion** (`engine/scorer_redis.py`): Replaces
  non-atomic SCARD+DEL with a single Redis `EVAL` Lua script. Eliminates
  double-promotion race under concurrent multi-node ingestion.
- **Persistent DP budgets** (`probe/privacy.py`, `probe/sdk.py`):
  `DPAccountant` serialises `current_spend` and `window_start_time` to
  a JSON file via atomic `os.replace()`. Budget survives gateway and
  probe process restarts. Opt-in via `ProbeConfig.dp_storage_path`.
- **SOC 2 audit-grade incident export** (`engine/audit.py`,
  `gateway/main.py`): `GET /v1/alerts/{alert_id}/export` returns a
  JSON attachment containing `alert_details`, `baseline_evidence` (up to
  50 telemetry signals preceding the alert), and a `report_checksum`
  (SHA-256 of canonical JSON with sorted keys). Tamper-evident:
  consumers re-serialise and recompute the digest to verify integrity.
  Resolves both `LocalDriftAlert` and `PublicDriftAlert` by id.

---

### Architectural invariants (enforced throughout)

| Invariant | Status |
|---|---|
| Privacy by construction — no raw prompts/outputs ever transmitted | ✅ enforced |
| OTel-native instrumentation (`gen_ai.*`, `mcp.*` conventions) | ✅ enforced |
| Content-addressed baseline corpus (append-only, immutable history) | ✅ enforced |
| Correlation-first alerts — single-org signal never becomes public | ✅ enforced |
| Canary suite cost cap ≤200 prompts, target <$0.10/day | ✅ enforced |
| PEP 8 / ruff clean (0 violations across 38 Python files) | ✅ enforced |

---

### Known limitations as of 1.0.0-rc.1

See individual Keystone Reports (Sessions 001–016) for full detail.
Active open items:

- `KNOWN-LIMIT-001`: Per-metric Redis quorum key granularity deferred
- `KNOWN-LIMIT-004`: Clock skew may shorten effective DP window
- `KNOWN-LIMIT-P3-001-A/B/D`: Fleet auth, bootstrap, and thread safety
- `KNOWN-LIMIT-P3-002-A–E`: Webhook delivery guarantees (at-most-once)
- `KNOWN-LIMIT-P3-004-C`: Audit export endpoint has no authentication
- `KNOWN-LIMIT-P3-004-D`: Audit export not supported on ClickHouse backend

---

### Test coverage

**99 tests, 100% pass rate, 0 ruff violations**

| Module | Tests |
|---|---|
| Storage / repository | 18 |
| Gateway (signals, weather, bootstrap) | 13 |
| ClickHouse backend | 14 |
| Privacy / DP accounting | 12 |
| Probe SDK | 9 |
| Redis agreement scorer | 11 |
| OTel / MCP adapters | 6 |
| Enterprise (fleet isolation) | 8 |
| Webhooks | 8 (WH1–WH8) |
| SOC 2 audit export | 8 (AU1–AU8) |
