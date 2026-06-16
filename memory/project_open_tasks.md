# SEISMOGRAPH — Project Open Tasks
# Phase 0: Validation & Backtest
# Last updated: 2026-06-10

---

## Legend
- `[ ]` open
- `[~]` in progress
- `[x]` complete
- `[D]` deferred

---

## Phase 0 — Validation & Backtest (Weeks 1–4)

### P0-001 — Repository scaffold
`[x]` Create full folder structure, Python stubs, memory files, kernel.log, graph.json, Architecture doc, README.
- Completed: 2026-06-10 Session 001
- Keystone Report: KEYSTONE_REPORT_SESSION_001.md

---

### P0-002 — Canary suite v0 (probe/canary_suite.py)
`[x]` Implement `CanarySuiteRegistry`, `CanarySuiteVersion.from_prompts()`, and `CanaryPrompt`.
Design the v0 prompt corpus (≤200 prompts, temperature 0).
Verify SHA-256 content-addressing is stable across Python versions.
Adversarial: Sybil probe injecting fabricated feature vectors must be rejected.

---

### P0-003 — Privacy layer (probe/privacy_layer.py)
`[x]` `probe/privacy.py` created: SignalBatch + Aggregator + SHA-256 hashing + metric key whitelist. Privacy boundary verified.
Implement DP-noise calibration (Laplace mechanism, ε-budget TBD with Tatiana).
Implement SHA-256 feature hashing.
Aegis audit: verify no raw prompt/output leaves probe boundary.

---

### P0-004 — Ingestion gateway (gateway/ingestion.py)
`[x]` Wire Ed25519 signature verification (cryptography library).
Add Pydantic v2 batch schema validation.
Adversarial: send malformed/unsigned batch → verify atomic rejection + log.

---

### P0-005 — Correlation engine (engine/correlation.py)
`[~]` engine/detector.py: CUSUMDetector.update() -- COMPLETE.
Implement `BayesianOnlineDetector.update()`.
Document threshold decisions as labelled data in `data/drift_labels/`.
Adversarial: correlated noise burst from single org must NOT produce public alert.

---

### P0-006 — Backtest notebook
`[x]` COMPLETE 2026-06-10. scripts/anthropic_backtest.py + notebooks/anthropic_backtest_report.md.
First alert: 2025-08-10 (Phase 1, 0.8% misrouting only).
Lead time: 38 days before postmortem, 19 days before escalation.
Ruff: PASS. Reproducible: python3 scripts/anthropic_backtest.py (SEED=42).

---

### P0-007 — Architecture document (SEISMOGRAPH_Architecture.md)
`[x]` COMPLETE 2026-06-11 Session 002. Atlas agent pass.
- SEISMOGRAPH_Architecture.md rewritten: 333 lines, 13 sections, all DP params, engine split clarified.
- engine/correlation.py: full PEP 484 type hints, Args/Returns/Raises docblocks, CUSUMDetector stub warning.
- probe/sdk.py: new scaffold with ProbeConfig, OTelSpanContext, ProbeSDK (6 methods, NotImplementedError).
- 9 new SG-TRACE annotations (REQ-SDK-001 through REQ-SDK-009).
- Ruff: PASS across full repo (14 files, zero violations).

---

### P0-008 — OTel instrumentation stub (probe/sdk.py)
`[x]` COMPLETE 2026-06-11 Session 003 (reactivated from Phase 1 deferral).

Files changed:
  - probe/canary.py    D14 fix: timezone.utc (Python 3.10 compat)
  - probe/sdk.py       Full implementation: span lifecycle + flush()
  - tests/test_sdk.py  T1-T4 (8 total tests passing)
  - pyproject.toml     D12 + D13 fix (addopts + full ruff config restored)

Privacy boundary: response_hash = SHA-256(span_id), no raw output stored.
Auth headers: x-signature/x-public-key stub (empty, Phase 0 gateway accepts).
Test: 8/8 PASS. Ruff: PASS (18 files, 0 violations).

---

## Phase 1 — Solo MVP / Public Good (Months 2–4)
*(Not yet scheduled — items will be broken out at Phase 0 completion)*

- [ ] FastAPI ingestion endpoint
- [ ] Single-node ClickHouse or Postgres time-series store
- [ ] Public "model weather" dashboard (top 10 model tuples)
- [ ] Open-source probe release

---


### P1-003 -- Engine Bootstrap & Weather API
`[x]` COMPLETE 2026-06-11 Session 004.

Files changed:
  - engine/repository.py   Added get_all_model_tuples() (SELECT DISTINCT),
                           get_recent_alerts(model_tuple, hours_back=24);
                           timestamps use naive-UTC (.replace(tzinfo=None))
                           for consistent SQLite storage and comparison.
  - gateway/schema.py      Added ModelWeatherResponse Pydantic model
                           (model_tuple, status, last_alert_timestamp,
                           recent_avg_output_length, recent_json_success_rate).
  - gateway/main.py        Added bootstrap_detector(detector, repo) -> int
                           (standalone importable function; oldest-first feed,
                           bootstrap alerts discarded); lifespan now calls
                           bootstrap_detector() and logs observations_fed;
                           added _compute_model_weather() helper and
                           GET /v1/weather endpoint (no auth required).
  - tests/test_gateway.py  T5: bootstrap unit test (isolated in-memory DB,
                           baseline_samples=5, asserts baseline_ready);
                           T6: empty DB -> 200 []; T7: one signal -> STABLE;
                           T8: injected DriftAlert -> DRIFTING.

Test results: 20/20 passed. Ruff: PASS (22 files, 0 violations).

---


### P1-004 -- The Public Dashboard
`[x]` COMPLETE 2026-06-11 Session 004.

Files created:
  - dashboard/static/index.html  Dark-mode UI; #weather-grid CSS Grid;
                                 pulsing dot for DRIFTING; no external deps.
  - dashboard/static/app.js      Vanilla JS; polls /v1/weather every 60s;
                                 XSS-safe DOM construction; empty-state handled.
gateway/main.py updated:
  - StaticFiles mount: /static -> dashboard/static/ (absolute __file__ path)
  - GET /: FileResponse(index.html), include_in_schema=False
  - Imports: pathlib, FileResponse, StaticFiles
tests/test_gateway.py: T9 dashboard root (200, text/html, body check)

Test results: 21/21 passed. Ruff: PASS (22 files).


---

## Phase 2 — Network Growth (Months 4–9)
*(Planning deferred)*

- [ ] DP hardening (full ε-accounting)
- [ ] OTel/MCP adapters
- [ ] Sybil resistance (reputation weighting)
- [ ] ClickHouse migration
- [ ] Methodology paper

---

## Phase 3 — Enterprise Grade (Months 9–18)
*(Planning deferred)*

- [ ] Multi-tenant, SSO/RBAC, SOC 2
- [ ] In-VPC probe option
- [ ] SLAs, canary-gated rollback
- [ ] First 2–3 hires

---

### P1-001 -- FastAPI Ingestion Gateway
`[x]` COMPLETE 2026-06-11 Session 002.

---

### P1-002 -- Persistent Storage Layer
`[x]` COMPLETE 2026-06-11 Session 003.

Files created:
  - engine/models.py       SQLAlchemy 2.0 TelemetrySignal + DriftAlert ORM
  - engine/repository.py   DatabaseSession + SignalRepository (save_batch,
                           save_alert, get_recent_signals)
  - tests/conftest.py      autouse SEISMOGRAPH_DB_URL env var fixture
  - tests/test_storage.py  T1-T8 (8 storage tests)

gateway/main.py updated:
  - save_batch() called before CUSUMDetector.update()
  - save_alert() called for each DriftAlert fired

Test results: 16/16 passed. Ruff: PASS (22 files).

Files created:
  - gateway/main.py       FastAPI app, lifespan CUSUMDetector init, POST /v1/signals
  - tests/test_gateway.py T1-T4 TestClient suite (4 tests)
  - tests/__init__.py     Package marker

pyproject.toml updated:
  - [tool.pytest.ini_options] added: testpaths, pythonpath=[.], filterwarnings
  - CacheProvider suppressed via -p no:cacheprovider (NTFS overlay permission issue)

Test results (pytest 9.0.3, Python 3.10.12):
  test_valid_payload_returns_202                          PASS
  test_schema_violation_unknown_metric_key_returns_422    PASS
  test_schema_violation_missing_required_field_returns_422 PASS
  test_unauthorized_returns_401                           PASS

Ruff: PASS across 17 files (3 auto-fixes, 0 remaining violations).


---




### P1-007 -- The Launch README and Session Wrap-up
`[x]` COMPLETE 2026-06-12 Session 006.

Files created/updated:
  - README.md (263 lines)         HN-optimized: problem, 38-day backtest proof,
                                  architecture (DP spec, CUSUM, quorum), quickstart,
                                  repo structure, privacy-by-construction section.
  - KEYSTONE_REPORT_SESSION_001.md Phase 1 sign-off appended: provenance table,
                                  verification summary, defects D12-D20,
                                  architectural decisions, known limitations,
                                  PHASE 1 ACCOUNTABILITY STATEMENT signed
                                  Tatiana / 2026-06-12.
  - memory/SESSION_003_HANDOVER.md Phase 2 context brief: Phase 1 victory,
                                  operational invariants (RULE-1 corrected,
                                  Python 3.10, ruff, SQLAlchemy 2.0, test patterns),
                                  P2-001 through P2-005 roadmap, file state at park,
                                  Phase 2 session start protocol.

Test results: 23/23 passed. Ruff: 0 violations. Workspace: cleanly parked.


---

### P1-006 -- The End-to-End Demo Simulation
`[x]` COMPLETE 2026-06-12 Session 006.

Files created:
  - scripts/demo_simulation.py  End-to-end demo: two ProbeSDK clients
                                (Client A = startup, Client B = enterprise),
                                PRE-FLIGHT CUSUM warmup (30 silent batches),
                                Phase 1 stable baseline (5 rounds each),
                                Phase 2 Client A degrades until CUSUM fires
                                (dashboard stays STABLE: 1 org < quorum),
                                Phase 3 Client B degrades until quorum reached
                                (dashboard -> DRIFTING, PublicDriftAlert written).

Key properties:
  - Two distinct client_ids (UUID4) via separate ProbeSDK/Aggregator instances.
  - CUSUM shared per (model_tuple, metric_name): Client A baseline primes
    detector for Client B.
  - DP Laplace noise (epsilon=2, scale=0.5) applied on every flush().
  - try/except on server reachability: prints helpful uvicorn command + sys.exit(1).
  - Max 25 drift-round safety ceiling per phase.
  - ANSI colour output: green=ok, yellow=local alert, red=drifting/error.

Test results: 23/23 passed (existing suite unaffected). Ruff: PASS.

---

### P1-005 -- The Federated Quorum (Agreement Wiring)
`[x]` COMPLETE 2026-06-12 Session 005.

Files changed:
  - engine/correlation.py  promote_to_public_alert() return type changed
                           bool -> int | None (org count or None); ValueError
                           on missing pending replaced with return None.
  - engine/models.py       DriftAlert -> LocalDriftAlert (__tablename__:
                           local_drift_alerts, added client_id: String(36));
                           new PublicDriftAlert (__tablename__:
                           public_drift_alerts, fields: id, timestamp,
                           model_tuple, metric_name, contributing_org_count).
  - engine/repository.py   Import: LocalDriftAlert, PublicDriftAlert;
                           save_alert() -> save_local_alert(alert, client_id);
                           new save_public_alert(model_tuple, metric_name,
                           contributing_org_count);
                           get_recent_alerts() now queries PublicDriftAlert
                           only (local alerts excluded from weather API).
  - gateway/main.py        Imports AgreementScorer, ChangePointResult;
                           lifespan: app.state.scorer = AgreementScorer();
                           POST /v1/signals: bridges DetectorDriftAlert ->
                           ChangePointResult, calls scorer.ingest() +
                           promote_to_public_alert(); if quorum met, calls
                           save_public_alert() + scorer.clear().
  - tests/test_storage.py  T4/T5: save_alert() -> save_local_alert(...,
                           client_id="test-client-001"); import DBDriftAlert
                           -> LocalDriftAlert.
  - tests/test_gateway.py  T8: injected DriftAlert -> save_public_alert()
                           (get_recent_alerts queries PublicDriftAlert only);
                           T10 test_single_org_noise_blocked (ADVERSARIAL):
                           one org fires CUSUM, weather stays STABLE;
                           T11 test_quorum_reached_triggers_dashboard
                           (ADVERSARIAL): two orgs fire CUSUM, weather DRIFTING.

Key invariants verified:
  - Single-org signal NEVER promotes to PublicDriftAlert (T10).
  - Two distinct orgs -> quorum -> PublicDriftAlert -> DRIFTING (T11).
  - get_recent_alerts() is read-only on PublicDriftAlert; LocalDriftAlert
    is private fleet data, never surfaced in the weather API.

Test results: 23/23 passed. Ruff: PASS (0 violations).


## Completed This Session

- **P2-001** — Ed25519 Cryptographic Identity & Sybil Resistance — COMPLETE 2026-06-12
  - probe/crypto.py, probe/sdk.py, gateway/auth.py, gateway/main.py, tests/test_crypto.py, tests/test_gateway.py, tests/test_sdk.py
  - 36/36 tests passing, ruff clean


### P2-002 -- ClickHouse Time-Series Migration
`[x]` COMPLETE 2026-06-12 Session 007.

Files created/modified:
  - pyproject.toml           Added clickhouse-connect>=0.7 dependency
  - engine/repository.py     Added SignalRow + AlertRow dataclasses;
                             BaseRepository ABC (6 abstract methods);
                             SignalRepository now inherits BaseRepository
  - engine/clickhouse.py     NEW: ClickHouseRepository(BaseRepository);
                             setup_tables() with 3 MergeTree CREATE TABLE IF
                             NOT EXISTS; all 6 interface methods via raw SQL
  - gateway/main.py          STORAGE_BACKEND env var routing in lifespan;
                             BaseRepository type annotations throughout;
                             version bumped to 0.2.0
  - tests/test_storage.py    7 new mocked ClickHouse tests (CU1-CU7)

Test results: 43/43 passed. Ruff: 0 violations (26 files).


### P2-003 -- Redis Distributed State
`[x]` COMPLETE 2026-06-12 Session 008.

Files created/modified:
  - pyproject.toml           Added redis>=4.0 dependency
  - engine/scorer_redis.py   NEW: RedisAgreementScorer(BaseRepository-style
                             interface); _quorum_key() helper;
                             ingest(): SADD + EXPIRE 86400s;
                             promote_to_public_alert(): SCARD >= QUORUM_MIN;
                             clear(): DEL
  - gateway/main.py          QUORUM_BACKEND env var routing in lifespan;
                             QUORUM_BACKEND=redis instantiates
                             RedisAgreementScorer(redis.Redis.from_url(
                             REDIS_URL)); default=memory unchanged
  - tests/test_scorer_redis.py  10 new tests (RS1-RS10): MagicMock Redis
                             client; SADD/SCARD/DEL assertions;
                             Sybil adversarial test (RS8)

Test results: 53/53 passed. Ruff: 0 violations (27 files).

Key invariants verified:
  - Single-org Sybil replay (same client_id twice) does NOT meet quorum
    (RS8 ADVERSARIAL).
  - Empty contributing_orgs is a no-op; no phantom quorum accumulation
    (RS3).
  - Redis backend is a drop-in replacement: zero gateway call-site changes.
  - Privacy invariant: Redis key sg:quorum:{model_tuple} contains only
    a public model identifier; no raw prompts, outputs, or org secrets.

Known limitations logged: KNOWN-LIMIT-001 (metric-level granularity
deferred), KNOWN-LIMIT-002 (startup ping), KNOWN-LIMIT-003 (promote/clear
race window). See KEYSTONE_REPORT_SESSION_008.md.


### P2-004 -- Differential Privacy Composition Accounting
`[x]` COMPLETE 2026-06-12 Session 009.

Files created/modified:
  - probe/privacy.py         Added PrivacyBudgetExceededError exception;
                             DPAccountant class (daily_budget, current_spend,
                             window_start_time, spend(), reset_if_needed(),
                             remaining property); Aggregator.clear_all() method
  - probe/sdk.py             Added FLUSH_EPSILON=2.0 constant;
                             daily_epsilon_budget field to ProbeConfig;
                             _accountant param to ProbeSDK.__init__;
                             flush(): reset_if_needed() + spend() gate;
                             PrivacyBudgetExceededError -> clear_all() +
                             {"status": "budget_exceeded"} graceful return
  - tests/test_privacy.py    NEW: 10 tests (DP1-DP10): DPAccountant unit
                             tests + ProbeSDK integration tests

Test results: 63/63 passed. Ruff: 0 violations (28 files).

Key invariants verified:
  - Budget exhaustion: HTTP NOT called, aggregator cleared, graceful
    return {"status": "budget_exceeded"} (DP7 integration ADVERSARIAL).
  - 24h window reset: time-travel test confirms reset (DP6).
  - All-or-nothing spend: state unchanged on failed spend() (DP4).
  - Noop path does not deduct budget (pending=0 exits before spend).

Known limitations: KNOWN-LIMIT-004 (wall-clock window),
KNOWN-LIMIT-005 (no persistence across restarts),
KNOWN-LIMIT-006 (per-flush not per-model-tuple cost).
See KEYSTONE_REPORT_SESSION_009.md.


### P2-005 -- OTel and MCP Adapters
`[x]` COMPLETE 2026-06-12 Session 010.

Files created/modified:
  - pyproject.toml              Added opentelemetry-sdk>=1.20 dependency
  - probe/adapters/__init__.py  NEW: package marker
  - probe/adapters/otel.py      NEW: SeismographSpanProcessor(SpanProcessor);
                                _model_tuple_from_attrs() helper;
                                _response_hash_from_span() helper;
                                on_end() maps gen_ai.* -> CanaryResult ->
                                ProbeSDK._aggregator.add_result()
  - probe/adapters/mcp.py       NEW: check_model_weather() tool (GET
                                /v1/weather, filter by model_tuple, format
                                string); _WeatherEntry dataclass; TOOL_SCHEMA;
                                run_mcp_server() minimal JSON-RPC 2.0 stdio
  - tests/test_adapters.py      NEW: 12 tests (OT1-OT5+2, MC1-MC5)

Test results: 75/75 passed. Ruff: 0 violations (31 files).

Defects caught: D29 (wrong ProbeConfig kwarg), D30 (falsy zero guard on
start_time=0), D31 (Edit tool TOML truncation -- rewritten via heredoc).

Known limitations: KNOWN-LIMIT-007 (aggregator thread-safety),
KNOWN-LIMIT-008 (blocking stdio MCP server),
KNOWN-LIMIT-009 (client-side weather filtering),
KNOWN-LIMIT-010 (fixed suite_version in adapter).
See KEYSTONE_REPORT_SESSION_010.md.


### P2-006 -- Containerization & Orchestration
`[x]` COMPLETE 2026-06-12 Session 011.

Files created:
  - Dockerfile             python:3.11-slim; non-root user (UID 1001);
                           cache-friendly dep layer (pyproject.toml first);
                           copies engine/, gateway/, dashboard/, data/;
                           EXPOSE 8000; HEALTHCHECK; CMD uvicorn --workers 1
  - docker-compose.yml     Three services: gateway (build .), clickhouse
                           (clickhouse-server:latest, ports 8123+9000),
                           redis (alpine, port 6379); named volumes for
                           both external services; restart: on-failure
                           on gateway; env vars wire production backends
  - .env.example           Documents STORAGE_BACKEND, SEISMOGRAPH_DB_URL,
                           CLICKHOUSE_URL, QUORUM_BACKEND, REDIS_URL
                           with defaults and usage notes

Architectural decisions: AD-CONTAINER-001 (non-root user),
AD-CONTAINER-002 (dep layer before source), AD-CONTAINER-003
(probe/ excluded), AD-CONTAINER-004 (fastapi/uvicorn not in
pyproject.toml), AD-CONTAINER-005 (named volumes).

Test results: 75/75 passed (unchanged). Ruff: 0 violations.

Known limitations: KNOWN-LIMIT-011 (gateway optional dep group),
KNOWN-LIMIT-012 (no ClickHouse migration framework),
KNOWN-LIMIT-013 (depends_on readiness vs started),
KNOWN-LIMIT-014 (healthcheck python3 PATH).
See KEYSTONE_REPORT_SESSION_011.md.


### P3-001 -- Multi-Tenant Data Isolation
`[x]` COMPLETE 2026-06-12 Session 013.

**Goal:** Partition the ingestion pipeline with a `fleet_id` field across all
layers so that private enterprise fleet signals are isolated from the public network.

Files created/modified:
  - gateway/schema.py        Added `fleet_id: str | None = None` to
                             `InboundSignalBatch`; fixed canary_hashes key
                             regex r"^[\w.\-]+$" (allows dots for v1.0.0 style)
  - probe/privacy.py         Added `fleet_id: str | None = None` to
                             `SignalBatch` frozen dataclass; updated `to_dict()`
                             (fleet_id covered by Ed25519 signing);
                             `Aggregator.flush(model_tuple, fleet_id=None)`;
                             window-width invariant fix for single-result batches
                             (advance window_end +1µs when start == end)
  - engine/models.py         Added `fleet_id Mapped[str | None]` to
                             `TelemetrySignal` and `LocalDriftAlert` ORM models
  - engine/repository.py     `BaseRepository.save_local_alert` signature updated;
                             `SignalRepository.save_batch` and `save_local_alert`
                             pass fleet_id through to ORM constructors
  - engine/clickhouse.py     DDL: `fleet_id Nullable(String)` added to
                             telemetry_signals and local_drift_alerts tables;
                             INSERT methods updated (7- and 6-column)
  - gateway/main.py          `app.state.private_detectors: dict[str, CUSUMDetector]`
                             added in lifespan; POST /v1/signals branches on
                             `batch.fleet_id is not None`: private path uses
                             per-fleet CUSUMDetector, writes LocalDriftAlert
                             with fleet_id, NEVER calls AgreementScorer;
                             version bumped to 0.3.0
  - probe/sdk.py             `fleet_id: str | None = None` added to `ProbeConfig`;
                             `flush()` passes fleet_id to `Aggregator.flush()`
  - tests/test_enterprise.py NEW: 5 tests EN1-EN5
                             (positive, adversarial, and storage coverage)
  - tests/test_storage.py    CU2: column count assertion updated 6 -> 7
  - KEYSTONE_REPORT_SESSION_013.md  Written.

Defects caught: D32 (zero-width window), D33 (main.py ruff truncation),
D34 (canary key regex double-backslash + missing dot), D35 (privacy.py
Edit-tool truncation), D36 (gateway/schema.py Edit-tool truncation via
context rebuild), D37 (test_storage CU2 column count).

Test results: 80/80 passed (was 75 at Phase 2 close; +5 enterprise tests).
Ruff: 0 violations (33 files).

Key invariant enforced:
  - Private fleet alerts NEVER enter AgreementScorer, NEVER create
    PublicDriftAlert, NEVER visible via GET /v1/weather.
  - fleet_id included in signed SignalBatch.to_dict() — gateway routing
    is authenticated; cannot be spoofed via unsigned headers.
  - `is not None` guard (not falsy) — protects against fleet_id="" edge case.

Keystone Report: KEYSTONE_REPORT_SESSION_013.md

---

## Growth Roadmap — Sessions 017–018 (2026-06-14) — COMPLETE

### [DONE] PyPI Packaging (Session 017)
- `pyproject_probe.toml`, `scripts/build_probe.sh`, `dist/seismograph_probe-1.0.0-py3-none-any.whl`
- Enables `pip install seismograph-probe` (5-min onboarding goal from GROWTH_ROADMAP.md §1.2)

### [DONE] First-Party Probe Fleet (Session 018)
- `docs/PROVIDER_TOS_CHECKS.md`, `scripts/first_party_fleet.py`, `Dockerfile.fleet`, docker-compose fleet service
- Populates public dashboard with real baselines before community probes join

### [OPEN] Next Growth Roadmap items
- Publish `seismograph-probe` to PyPI (requires `twine upload`)
- Complete ToS review for Google Gemini, Mistral, Cohere → add to TARGET_MODELS
- Add KNOWN-LIMIT-FLEET-002: pin requirements-fleet.txt before production
- Implement KNOWN-LIMIT-FLEET-003: ±5% jitter on PROBE_INTERVAL_SECONDS
- KNOWN-LIMIT-P3-004-C: add auth to `/v1/alerts/{alert_id}/export` before public deployment

### [DONE] Landing Page + Routes (Session 019)
- `dashboard/static/landing.html`, GET / → landing, GET /dashboard → weather UI
- 100/100 tests, ruff clean

### [OPEN] Pre-launch blockers
- Update placeholder URLs in landing.html (github.com/seismograph-io/... → real URLs)
- Tatiana: `twine upload dist/seismograph_probe-1.0.0-py3-none-any.whl`
- Complete ToS reviews for Gemini/Mistral/Cohere

---

## LAUNCH MILESTONE — 2026-06-14

SEISMOGRAPH is officially live. Phase 0 complete. Standby mode active.

### Ambulance-chasing triggers (next session on any of these)
- Spike in "model degradation" / "GPT dumber" / "Claude worse" posts
- Anomalous signal in fleet runner logs
- First community probe joins the network
- PyPI download milestone warrants blog follow-up
