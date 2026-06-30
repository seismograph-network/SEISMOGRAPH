# KEYSTONE REPORT — Session 026 (2026-06-29)

**Task:** Track 1 of the product-hardening pass — make the canary probe
*real*. Wire `execute_canary(mock=False)` to a live OpenAI-compatible
provider so the pipeline can run against an actual model, not synthetic
mock data. Provider-agnostic by configuration (local Ollama → hosted
OpenAI/Groq/Mistral).
**Branch:** suggested `seismograph/task-live-probe` (Tatiana commits).
**Director:** Tatiana | **Co-pilot:** Claude (Lead Technical Co-Pilot)

---

## 1. Provenance

| Artifact | Origin |
|---|---|
| `probe/providers.py` (new — OpenAICompatibleProvider, ProviderError, model_name_from_tuple) | AI |
| `probe/canary.py` (execute_canary now accepts a live `provider`; raw output still hashed + discarded) | AI |
| `tests/test_providers.py` (new — 11 tests, fully offline) | AI |
| `scripts/live_probe.py` (new — live run CLI; prints only privacy-safe features) | AI |
| `.env.example` (probe endpoint config block) | AI |
| `docs/PROVIDER_TOS_CHECKS.md` (self-hosted + Groq rows) | AI |
| All commits, pushes, the live run against a real endpoint | Human (Tatiana) |

## 2. Verification summary

- **New tests: 11/11 passed** (`tests/test_providers.py`), fully offline via
  an injected fake HTTP transport — no network.
- **Probe-side regression: 69 passed** on fresh bytecode
  (`PYTHONPYCACHEPREFIX` off the NTFS mount): test_providers + test_sdk +
  test_privacy + test_crypto + test_adapters + test_storage. These exercise
  the modified `canary.py` end-to-end through a clean recompile.
- **ruff check + format: clean** on `probe/providers.py`, `probe/canary.py`,
  `tests/test_providers.py`, `scripts/live_probe.py` (`--no-cache`).
- **Privacy invariant re-asserted by test** (`test_execute_canary_live_no_raw_output`):
  the raw model string never appears on the serialised result; only the
  64-char SHA-256 hash, length, json_valid and latency survive.
- **Adversarial (b) covered** (`test_adversarial_silent_drift_changes_hash_and_length`):
  a provider-side semantic shift with an identical latency profile and no
  error changes the response hash AND output length — exactly the signal the
  CUSUM detector consumes. **Adversarial (a)** (Sybil/fabricated probe) is
  unchanged and remains gated at the AgreementScorer quorum layer.

## 3. Defects caught and fixed

- **Transport exceptions could escape as raw errors (real, fixed):** a custom
  transport raising `TimeoutError` bypassed `ProviderError`. `complete()` now
  wraps any non-`ProviderError` transport exception into `ProviderError` with
  a payload-free message. Test: `test_provider_timeout_raises_clean`.
- **Sandbox mount truncation of `canary.py` (environment, mitigated):** after
  tool-based edits, the sandbox's read of `canary.py` was truncated mid-`for`
  loop (the same NTFS-overlay artifact that affects `engine/correlation.py`).
  Mitigation: `canary.py` was rewritten in full via a single sandbox-side
  write so the in-sandbox file is complete and parses; AST-verified.

## 4. Known limitations

- **The live run itself is pending Tatiana's execution.** Tests prove the
  wire format, privacy and drift-signal behaviour offline; the actual call to
  a real endpoint (Ollama/OpenAI) must be run on a machine with that endpoint.
  Command: `python scripts/live_probe.py`.
- **Full 118-test suite not run in-sandbox.** `engine/correlation.py` and
  `gateway/main.py` are truncated by the NTFS-overlay read, so a fresh-compile
  full run errors on collection for the 5 engine/gateway test modules. Those
  modules are untouched this session; Tatiana re-runs `py -3.10 -m pytest -q`
  on the real disk to confirm 107 baseline + 11 new = **118 expected**.
- **Gateway/dashboard emission not yet wired.** `live_probe.py` proves the real
  call and prints results; feeding the live `SignalBatch` through the privacy
  aggregator + crypto signing into `POST /v1/signals` (so the public dashboard
  shows a real model) is the immediate next step.
- **Groq ToS row marked ⚠ VERIFY** — complete it before any production probe
  against the free tier. Self-hosted Ollama needs no third-party ToS.

## 5. Accountability statement

The above is an accurate account of Session 026. New and probe-side tests pass
(11 new, 69 probe-side, fresh bytecode); ruff is clean; the privacy and
silent-drift invariants are asserted by named tests. The live run and the full
118-test confirmation are Tatiana's to execute on the real disk. Nothing is
overclaimed; pending items are stated as pending.

Signed: _________________________  Tatiana — 2026-06-29

## 6. Methodology note (one improvement)

The recurring NTFS-overlay truncation (now hit on `correlation.py`, `main.py`,
`canary.py`) keeps forcing full-file rewrites and blocking in-sandbox full
runs. Recommendation: add a one-line CI job that runs `python -m compileall
probe engine gateway scripts` on push — it deterministically catches any
truncation/syntax breakage on a clean checkout, independent of the sandbox
artifact, and gives a trustworthy green signal the sandbox currently cannot.

---

# ADDENDUM — Session 027 (2026-06-30): first live run + probe hardening

Closes the three "pending" items flagged in §4 above (live run, full-suite
confirmation, documented-command robustness). Same task arc; same branch
`seismograph/task-live-probe`.

## A1. What happened

- **First live probe run executed** against a REAL hosted model — Mistral
  `mistral-small-latest` via `https://api.mistral.ai/v1` (ToS: ✅ green,
  PROVIDER_TOS_CHECKS row reviewed 2026-06-16). Three canary results
  returned with real network latencies (638–1280 ms):

  | prompt_id | output_len | json_valid | latency_ms |
  |---|---|---|---|
  | v1.0.0-logic | 1 | False | 1280 |
  | v1.0.0-format | 124 | **True** | 638 |
  | v1.0.0-refusal | 237 | False | 913 |

- **Privacy invariant held live:** only SHA-256 hash, length, json_valid and
  latency were printed. No raw model output was displayed, stored, or
  transmitted. This is the first end-to-end real-model confirmation of the
  privacy boundary, not just an offline test assertion.

## A2. Hardening applied (commit 9b0779f)

| Change | File | Rationale |
|---|---|---|
| `sys.path` bootstrap | `scripts/live_probe.py` | Documented command `python scripts/live_probe.py` now resolves the `probe` package without requiring `PYTHONPATH` (sys.path[0] was scripts/, not repo root). #SG-TRACE REQ-CANARY-024 |
| Non-ASCII API-key guard | `probe/providers.py` | A pasted non-ASCII placeholder previously crashed deep in urllib with an opaque `UnicodeEncodeError`. Now rejected early with a clear, payload-free `ProviderError`. #SG-TRACE REQ-CANARY-025 |
| `.gitattributes` (`* text=auto eol=lf`) | repo root | Establishes LF as the canonical EOL, the policy fix for the recurring CRLF-phantom diffs from the NTFS working tree. |

## A3. Verification

- **Full suite on real disk: 119 passed** (`py -3.10 -m pytest -q`) — the
  S026 baseline of 118 plus one new adversarial test. Confirms the S026 §4
  "118 expected" item and supersedes it.
- **New test:** `test_provider_rejects_non_ascii_api_key` (adversarial: a
  Cyrillic placeholder key is rejected pre-network).
- **ruff:** clean on all touched files (E302/E402/PEP8).
- **Bootstrap verified in-sandbox:** `python scripts/live_probe.py` with no
  `PYTHONPATH` resolves `probe` and reaches the network layer.

## A4. Defect caught and fixed (this addendum)

- **`ProviderError` from the constructor escaped as a traceback (real,
  fixed):** the first cut of the non-ASCII guard raised inside
  `OpenAICompatibleProvider.__init__`, but `main()` in `live_probe.py` only
  wrapped `execute_canary` in its `try/except ProviderError` — so the guard
  produced an ugly traceback instead of the intended clean message. Fix:
  provider construction moved inside the same `try`. Re-verified: non-ASCII
  key now prints `Provider call failed: API key contains non-ASCII
  characters…` with no traceback.

## A5. Known limitations (honest)

- The generic "is the endpoint reachable? … Ollama" hint still prints on a
  key-validation failure, where it is not strictly relevant. The primary
  error message is correct and printed first; left as-is (cosmetic).
- The Mistral API key used for the run was exposed in the working chat
  transcript and must be revoked/rotated by Tatiana.
- Gateway/dashboard emission still not wired — feeding this live
  `CanaryResult` through the privacy aggregator + crypto signing into
  `POST /v1/signals` (Track 1b) remains the next step. Unchanged from §4.

## A6. Methodology note

The `compileall` CI job recommended in §6 would also have surfaced the
`sys.path`/invocation gap earlier: a push-time `python scripts/live_probe.py
--check-imports` style smoke (import-only, no network) would catch
script-entrypoint import regressions that the pytest suite misses because
pytest injects the repo root automatically.

Signed: _________________________  Tatiana — 2026-06-30
