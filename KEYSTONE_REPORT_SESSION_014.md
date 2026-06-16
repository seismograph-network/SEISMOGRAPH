# KEYSTONE REPORT — SESSION 014
## Task: P3-002 Automated Canary-Gated Rollback Webhooks

---

## 1. Provenance

| File | Status | Notes |
|---|---|---|
| `engine/models.py` | AI-generated | Added `WebhookConfig` ORM class + `UniqueConstraint` import |
| `engine/repository.py` | AI-generated | Added `register_webhook` / `get_webhook` abstract + concrete methods; `BaseRepository` expanded to 8 abstract methods |
| `gateway/schema.py` | AI-generated | Added `WebhookRegistration` Pydantic model (write-only, `extra="forbid"`) |
| `engine/clickhouse.py` | AI-generated | Added `register_webhook` (raises `NotImplementedError`) + `get_webhook` (returns `None`) stubs |
| `engine/webhooks.py` | AI-generated (new file) | `DriftNotification` dataclass + `WebhookDispatcher.dispatch()` with fail-safe `try/except` |
| `gateway/main.py` | AI-generated | Added `POST /v1/webhooks` endpoint; wired `asyncio.create_task(dispatcher.dispatch(...))` into private fleet path; version bumped to `0.4.0` |
| `tests/test_webhooks.py` | AI-generated (new file) | WH1-WH8; defects D38a-c found and fixed during this session |
| `pyproject.toml` | AI-generated | Added `httpx>=0.26` dependency |

Human editorial changes: none.

---

## 2. Verification Summary

**Test suite:** 88/88 passed (1 warning: Starlette httpx deprecation, not actionable)

**Ruff:** 0 violations, 36 files checked

**Test coverage by component:**

| Component | Test | Result |
|---|---|---|
| POST /v1/webhooks registration | WH1 | PASS |
| Auth guard: missing token | WH2 (adversarial) | PASS |
| Auth guard: wrong token | WH3 (adversarial) | PASS |
| WebhookDispatcher.dispatch() correct payload + auth header | WH4 | PASS |
| Fail-safe: HTTP 500 from target | WH5 | PASS |
| Fail-safe: ConnectError from target | WH6 | PASS |
| No dispatch when no webhook registered | WH7 | PASS |
| Integration: CUSUM fire -> dispatch called with correct args | WH8 | PASS |

---

## 3. Defects Caught and Fixed

**D38a: Global asyncio.create_task patch broke Starlette event loop**

Symptom: WH7 and WH8 CUSUM never fired; all requests returned 422.

Root cause: `patch("gateway.main.asyncio.create_task")` patches the actual
`asyncio` module object globally (since `gateway.main.asyncio` IS the asyncio
module). This intercepted Starlette's internal `create_task` calls, breaking
the ASGI event loop during TestClient operation.

Fix: Replaced the `asyncio.create_task` patch with
`app.state.dispatcher = MagicMock(dispatch=AsyncMock())` injected after
lifespan startup. The gateway fetches `dispatcher` from `request.app.state`
at request time, so the substitution takes effect without touching asyncio.

**D38b: Invalid UUID in _signal_batch helper (non-hex prefix chars)**

Symptom: All POST /v1/signals in WH7/WH8 returned 422 "invalid character:
found `w` at 1".

Root cause: `bid = f"{prefix}{n:06d}-0000-0000-0000-000000000000"` with
`prefix="w7"` or `prefix="w8"` -- `w` is not a hex character. Pydantic v2
strictly validates UUID fields.

Fix: Changed to `f"{n:08x}-0000-0000-0000-{n:012x}"` -- always valid hex,
unique per n.

**D38c: Docstring E501 (line too long) after D38a fix**

One docstring line exceeded 79 chars. Fixed by wrapping. Ruff clean.

---

## 4. Known Limitations

- KNOWN-LIMIT-P3-002-A: `POST /v1/webhooks` uses a static ADMIN_TOKEN env
  var. Any token holder can register/overwrite any fleet webhook. Fleet-scoped
  API keys are a Phase 3 backlog item (KNOWN-LIMIT-P3-001-B).
- KNOWN-LIMIT-P3-002-B: `target_url` accepts any string with no HTTPS
  enforcement or URL format validation. Deferred to Phase 4.
- KNOWN-LIMIT-P3-002-C: Dispatch runs as a detached asyncio.create_task.
  If the gateway dies after CUSUM fires but before dispatch completes, the
  notification is silently lost. No retry queue.
- KNOWN-LIMIT-P3-002-D: No rate-limiting on outgoing dispatch. A sustained
  drift burst fires one dispatch per alert with no backpressure.
- KNOWN-LIMIT-P3-002-E: auth_token stored plaintext in SQLite. At-rest
  encryption is out of Phase 3 scope.
- Inherits KNOWN-LIMIT-P3-001-A through D.

---

## 5. Privacy Invariant Check

- DriftNotification contains only: model_tuple, metric_name, alert_value
  (CUSUM score float), timestamp, fleet_id. No raw prompts or outputs. PASS
- auth_token is never returned in any API response (write-only). PASS
- WebhookRegistration uses extra="forbid" blocking unknown field injection. PASS
- ClickHouseRepository.register_webhook raises NotImplementedError with an
  explicit message. No silent data loss. PASS

---

## 6. Accountability Statement

I have reviewed the Keystone Report for P3-002 Automated Canary-Gated
Rollback Webhooks (Session 014). Implementation complete: 88/88 tests pass,
ruff clean, defects D38a-c resolved.

Tatiana ___________________ Date: 2026-06-12

---

## 7. Methodology Note

WH7/WH8 failures exposed a Python mock pitfall: patching
`module.asyncio.create_task` patches the global asyncio module, not a
module-local binding. Future async gateway tests should inject mock objects
through app.state (post-lifespan) rather than patching stdlib internals.
This is now the established pattern for SEISMOGRAPH gateway integration tests.
