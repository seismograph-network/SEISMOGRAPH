# KEYSTONE REPORT — Session 027 (2026-06-30)

**Task:** Track 1b — wire a live `CanaryResult` through the privacy
aggregator (DP noise) and Ed25519 signing into the gateway's
`POST /v1/signals`, so the public "model weather" dashboard surfaces a REAL
model tuple instead of demo data. First end-to-end traversal of the full
privacy + signing + ingestion + dashboard path with live-probe output.
**Branch:** `seismograph/task-live-probe` (live arc; Tatiana commits).
**Director:** Tatiana | **Co-pilot:** Claude (Lead Technical Co-Pilot)

> Earlier S027 work (first live Mistral run + probe hardening) is recorded in
> the addendum to `KEYSTONE_REPORT_SESSION_026.md` (commit de85afe). This
> report covers Track 1b only.

---

## 1. Provenance

| Artifact | Origin |
|---|---|
| `scripts/live_emit.py` (new — `build_signed_request` pure helper + `_post`/`_weather_for` + `main`) | AI |
| `tests/test_live_emit.py` (new — 3 integration tests via real Ed25519 + TestClient) | AI |
| Reused unchanged: `probe/privacy.py` (Aggregator/DP), `probe/crypto.py` (KeyManager/sign), `gateway/*` (ingest/verify/weather) | Human (prior sessions) |
| All commits, pushes, and the local live run against a running gateway | Human (Tatiana) |

No existing module was modified for Track 1b: the live-emit path composes the
already-tested aggregator, crypto, and gateway primitives. This keeps the
privacy and signing invariants exactly as previously verified.

## 2. Verification summary

- **Full suite (in-sandbox): 122 passed** = 119 baseline + 3 new. The full
  suite now runs in-sandbox after installing `opentelemetry-sdk` (see §3 —
  the long-assumed "NTFS truncation" block was in fact a missing optional
  dependency, not file corruption).
- **New integration tests (3/3):**
  - `test_live_emit_round_trip_accepts_and_shows_model` — build signed batch
    from canary results → `POST /v1/signals` returns **202** → `GET
    /v1/weather` lists the real model tuple as **STABLE** (single batch, no
    quorum). Real Ed25519, real schema validation, no signature mock.
  - `test_live_emit_payload_has_no_raw_output` — the signed wire payload's
    top-level keys ⊆ SignalBatch fields, metric keys ⊆ allowed set, all
    canary hashes are 64-hex, and no raw API-envelope keys (`choices`,
    `message`, `content`) appear in the signed bytes.
  - `test_live_emit_forged_signature_rejected` — a forged signature over a
    valid body is rejected **401** with no ingestion (Sybil / unsigned-batch
    gateway case).
- **Real-HTTP round-trip (in-sandbox, beyond TestClient):** a live `uvicorn
  gateway.main:app` instance accepted a signed batch over the wire via the
  script's own `urllib` `_post`, and `_weather_for` read back the model row
  — exercising the actual network code TestClient bypasses. Result:
  `accepted` + dashboard row `mistral/mistral-small-latest` STABLE.
- **ruff: All checks passed** on both new files (E302/E402/E501/I001 clean).

## 3. Defects caught and fixed

- **ruff E501 + I001 on first cut (real, fixed):** the `#SG-TRACE` header
  lines exceeded 79 cols and the first-party import block was unsorted.
  Shortened the trace lines and ran `ruff --fix`; recheck clean.
- **"Sandbox cannot run the full suite" lore corrected (environment):** every
  `engine/` and `gateway/` module parses and imports cleanly in-sandbox now;
  the only full-suite blocker was `tests/test_adapters.py` importing
  `opentelemetry`, which was simply not installed. Installing
  `opentelemetry-sdk` yields a clean 122-pass full run. This supersedes the
  S026 §4 note that the engine/gateway modules are read-truncated.

## 4. Known limitations (honest)

- **Local live run is pending Tatiana's execution.** The in-sandbox tests and
  real-HTTP round-trip prove the wiring with mock canary results; the run
  with a *real* Mistral response against a locally running gateway is
  Tatiana's to execute (needs the provider key + `uvicorn` up). Recipe is in
  the delivery note.
- **Single-batch DP noise is large by design.** With ε=2.0 and
  `avg_output_length` sensitivity = 8192, one batch's Laplace noise can swing
  the reported average far from the true ~120 tokens (observed ~4.6k in the
  HTTP check). The weather endpoint averages the last 10 signals, so the
  dashboard number only becomes meaningful after several batches. This is the
  privacy/accuracy trade-off, not a bug.
- **No live drift yet.** One org + one batch never reaches the 2-org quorum,
  so status is correctly STABLE. Demonstrating DRIFTING end-to-end requires
  either two orgs or a replayed drift window (future task).
- **`live_emit.py` POSTs to a gateway that must already be running.** It does
  not start the gateway; that is intentional separation.

## 5. Accountability statement

The above is an accurate account of Track 1b in Session 027. The live-emit
path composes previously verified primitives without modifying them; 3 new
integration tests pass against the real app with real Ed25519 (no mocks); the
full 122-test suite passes in-sandbox; ruff is clean; the privacy-on-the-wire
and forged-signature-rejection invariants are asserted by named tests and were
additionally confirmed over real HTTP. The local live run against Mistral is
stated as pending. Nothing is overclaimed.

Signed: _________________________  Tatiana — 2026-06-30

## 6. Methodology note (one improvement)

Installing `opentelemetry-sdk` in the sandbox unblocked the full suite that
was wrongly believed un-runnable for months. Recommendation: pin the probe's
optional/extra dependencies (otel, redis, clickhouse drivers) in a
`requirements-dev.txt` and have the session-start protocol install them, so
the agent always verifies against the *complete* suite in-sandbox rather than
a probe-only subset — closing the gap between sandbox green and CI green.
