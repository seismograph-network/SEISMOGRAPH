# SEISMOGRAPH

[![CI](https://github.com/Tania-coder/SEISMOGRAPH/actions/workflows/ci.yml/badge.svg)](https://github.com/Tania-coder/SEISMOGRAPH/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/seismograph-probe.svg)](https://pypi.org/project/seismograph-probe/)
[![Python](https://img.shields.io/pypi/pyversions/seismograph-probe.svg)](https://pypi.org/project/seismograph-probe/)
[![Tests](https://img.shields.io/badge/tests-107%20passing-brightgreen.svg)](#test-suite)
[![Lint](https://img.shields.io/badge/ruff-0%20violations-brightgreen.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](#license)
[![Live Demo](https://img.shields.io/badge/live%20demo-Model%20Weather-8A2BE2.svg)](https://seismograph-weather.onrender.com/dashboard)

**Created by [Tatiana Radchenko](https://github.com/Tania-coder)**

```bash
pip install seismograph-probe   # the probe SDK — Python 3.11+
```

**▶ Live dashboard:** **[seismograph-weather.onrender.com/dashboard](https://seismograph-weather.onrender.com/dashboard)** — real drift-weather for 4 production models, refreshed live. _(Free host; first load may take ~30s if the instance is asleep.)_

**A federated, privacy-preserving early-warning network for silent LLM API drift.**

> Detected the Anthropic Claude 3.5 Sonnet silent degradation on 2025-08-10 --
> **38 days before the official Sep 17 postmortem** and 19 days before the
> load-balancer escalation became visible. Detection occurred during the
> 0.8% misrouting window, before any user-visible symptoms appeared.

![SEISMOGRAPH Model Weather dashboard — live drift status for four production LLMs](docs/dashboard.png)

<p align="center"><em>Live "model weather" — <a href="https://seismograph-weather.onrender.com/dashboard">open the public dashboard</a> (no login).</em></p>

---

## The problem

Every AI team eventually hits this at 2am:

```
json_parse_errors up 12%.  latency: normal.  uptime: 100%.
My prompt didn't change.  My code didn't change.
Is it me, or did the model silently change underneath me?
```

Provider APIs do not broadcast behavioral changes. Endpoints that return 200
can still produce subtly different outputs -- degraded JSON fidelity, shifted
response length distributions, changed reasoning patterns. Standard monitoring
(latency, error rate, uptime) is **blind to semantic drift**.

SEISMOGRAPH answers the question. Not by trusting a single observer, but by
correlating canary probe signals across independent organisations so that no
single bad actor -- or noisy probe -- can trigger a false alarm.

---

## The proof: Phase 0 backtest

Anthropic published a postmortem on 2025-09-17 describing a silent
context-routing degradation introduced around 2025-08-05. The degradation
began as 0.8% misrouting (Phase 1) and escalated to ~16% on 2025-08-29
(Phase 2) before detection.

**SEISMOGRAPH (simulated, SEED=42, reproducible) would have alerted on 2025-08-10:**

```
CUSUM S- trace -- json_success_rate (anthropic/claude-3-5-sonnet@global)
  Baseline: mu0=0.9903, sigma0=0.00437, h=5.0, k=0.5

  Date        Phase          rate    S-      note
  -------------------------------------------------------
  2025-08-05  Phase1(0.8%)   0.9855  0.598   [bug introduced]
  2025-08-06  Phase1(0.8%)   0.9857  1.142
  2025-08-07  Phase1(0.8%)   0.9786  3.309
  2025-08-08  Phase1(0.8%)   0.9877  3.396
  2025-08-09  Phase1(0.8%)   0.9816  4.889
  2025-08-10  Phase1(0.8%)   0.9777  7.278   <<< FIRST ALERT
  ...
  2025-08-29  Phase2(16%)    --      --      [escalation visible to users]
  2025-09-17  --             --      --      [official postmortem published]

  Lead over escalation:  19 days
  Lead over postmortem:  38 days
```

Reproduce: `python scripts/anthropic_backtest.py`
Full report: `notebooks/anthropic_backtest_report.md`

---

## How it works

### Privacy-first probe SDK

The probe runs inside your infrastructure. It executes a frozen canary suite
(<=200 prompts, temperature 0) against your LLM API endpoint. **Raw prompts
and model outputs never leave your perimeter.**

What gets transmitted:
- SHA-256 hash of each response (not the response itself)
- DP-noised distributional features: `avg_output_length` (Laplace, scale=4096),
  `json_success_rate` (Laplace, scale=0.5), `result_count`
- Canary suite version hash (content-addressed, immutable baselines)
- Probe public key (Ed25519, pseudonymous -- no org identity disclosed)

Epsilon budget: 2.0 per flush via the Laplace mechanism. Sequential
composition tracking is a Phase 2 design item (REQ-PRIV-010).

### Page-CUSUM change-point detection

The gateway ingests probe batches and feeds each DP-noised metric into a
Page-CUSUM detector per `(model_tuple, metric_name)` tuple:

```
S+(n) = max(0, S+(n-1) + z(n) - k)    # upward shifts
S-(n) = max(0, S-(n-1) - z(n) - k)    # downward shifts
Alert when S+ or S- > h
```

Parameters: `h=5.0, k=0.5, baseline_samples=30`. The baseline window
estimates mu0 and sigma0 from the first 30 observations before drift
detection activates. Sigma is clamped at 1e-9 to prevent division by zero
on constant-value streams.

CUSUM state is **shared per (model_tuple, metric_name)** across all client
IDs -- contributing organisations build a shared baseline, which is what
makes cross-org comparison possible.

### Quorum Agreement Scorer

A single-organisation CUSUM alert is **never promoted to a public drift
alert.** This filters probe bugs, network hiccups, and Sybil attacks.

```python
QUORUM_MIN = 2  # minimum distinct org_ids required for a public alert

# Engine logic (engine/correlation.py):
scorer.ingest(ChangePointResult(change_detected=True, contributing_orgs=[client_id]))
org_count = scorer.promote_to_public_alert(model_tuple)
if org_count is not None:          # >= QUORUM_MIN orgs agree
    repo.save_public_alert(...)    # written to public_drift_alerts table
    scorer.clear(model_tuple)
```

The `GET /v1/weather` endpoint queries **only** `PublicDriftAlert`. Local
single-org alerts are private fleet data, never surfaced publicly.

### Storage schema

```
local_drift_alerts   -- private per-org CUSUM events (client_id, cusum_score)
public_drift_alerts  -- quorum-verified events (contributing_org_count)
telemetry_signals    -- raw ingested batches (DP-noised metrics only)
```

---

## Quickstart

**Use just the probe** (publish signals to a gateway):

```bash
pip install seismograph-probe   # Python 3.11+
```

**Run the full stack from source** (gateway + dashboard + tests):

**Requirements:** Python 3.10+, pip

```bash
git clone https://github.com/Tania-coder/SEISMOGRAPH.git
cd SEISMOGRAPH
pip install -e ".[dev]"
```

**Terminal 1 -- start the gateway:**
```bash
uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload
```

**Browser -- model weather dashboard:**
```
http://localhost:8000/dashboard
```

Polls `GET /v1/weather` every 60 seconds. Shows STABLE / DRIFTING per model
tuple with last alert timestamp and recent JSON success rate.

**Terminal 2 -- run the federated quorum demo:**
```bash
python scripts/demo_simulation.py
```

Watch two independent organisations (Client A: startup, Client B: enterprise)
discover a silent model update in real-time. Phase 1 shows a stable baseline.
Phase 2 shows Client A detecting drift while the public dashboard stays STABLE
(quorum not met -- the privacy gate holds). Phase 3 shows Client B confirming
the same degradation, quorum is reached, and the dashboard flips to DRIFTING.

```
  [sunny] -> STABLE  | json_rate=0.951 | last_alert=none
  ...
  [storm] -> DRIFTING | json_rate=0.312 | last_alert=2026-06-12T...
```

---

## Repository structure

```
probe/
  sdk.py          -- ProbeSDK: span lifecycle, DP-noised flush, OTel attrs
  canary.py       -- CANARY_SUITE_V1 (3 prompts, content-addressed)
  privacy.py      -- Aggregator + Laplace DP noise + metric key whitelist

engine/
  detector.py     -- CUSUMDetector (Page-CUSUM, shared per model_tuple)
  correlation.py  -- AgreementScorer (QUORUM_MIN=2, cross-org quorum gate)
  models.py       -- SQLAlchemy 2.0 ORM: LocalDriftAlert, PublicDriftAlert
  repository.py   -- SignalRepository: save/query with naive-UTC timestamps

gateway/
  main.py         -- FastAPI app: POST /v1/signals, GET /v1/weather, GET /
  schema.py       -- Pydantic v2 schemas (extra=forbid, frozen=True)
  auth.py         -- Ed25519 stub (Phase 2: REQ-PRIV-002)

dashboard/static/
  index.html      -- dark-mode UI, CSS Grid weather cards
  app.js          -- vanilla JS, 60s polling, XSS-safe DOM construction

scripts/
  demo_simulation.py      -- federated quorum demo (two ProbeSDK clients)
  anthropic_backtest.py   -- Phase 0 reproducible backtest (SEED=42)

tests/
  test_gateway.py   -- 23 tests: ingestion, CUSUM, quorum, weather, dashboard
  test_storage.py   -- storage layer: save/query LocalDriftAlert + signals
  test_sdk.py       -- probe SDK: span lifecycle, flush, DP noise, dry_run
  conftest.py       -- autouse in-memory SQLite DB fixture
```

---

## Test suite

```
107 passed, 0 failed
ruff: 0 violations across all Python files
```

Key adversarial tests:
- `test_single_org_noise_blocked` (T10): one org fires CUSUM -- weather stays STABLE
- `test_quorum_reached_triggers_dashboard` (T11): two orgs fire CUSUM -- weather DRIFTING

---

## Phase roadmap

| Phase | Status | Milestone |
|-------|--------|-----------|
| 0 -- Validation | **COMPLETE** | 38-day backtest lead time validated |
| 1 -- Solo MVP | **COMPLETE** | FastAPI + SQLite + dashboard + quorum live |
| 2 -- Network growth | Upcoming | Ed25519 Sybil resistance, ClickHouse, DP hardening |
| 3 -- Enterprise | Planned | Multi-tenant, SOC 2, in-VPC probe, SLAs |

---

## Architecture document

`SEISMOGRAPH_Architecture.md` -- 333 lines covering data flow, DP noise spec,
CUSUM calibration rationale, quorum algorithm, OTel integration plan, security
model (Ed25519 pseudonymous federation, Sybil resistance design), and open
decisions with phase assignments.

---

## Privacy by construction

The probe SDK is designed so that violating the privacy boundary requires
actively removing a safety. The `Aggregator` class in `probe/privacy.py`:

1. Hashes every response with SHA-256 before storing it
2. Applies Laplace DP noise to every outgoing metric
3. Enforces an `ALLOWED_METRIC_KEYS` whitelist -- unknown keys are dropped
4. Never stores raw prompt text or raw model output

The gateway enforces a matching `ALLOWED_METRIC_KEYS` frozenset on inbound
batches (422 on unknown keys). There is no code path in the system that
stores or forwards raw prompt or output content.

---

## License

Apache 2.0
