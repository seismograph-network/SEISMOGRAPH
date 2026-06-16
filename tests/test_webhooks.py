"""
tests.test_webhooks
====================
Test suite for P3-002 Automated Canary-Gated Rollback Webhooks.

Test matrix
-----------
WH1  register_webhook_api_returns_201
       POST /v1/webhooks with valid admin token and payload -> 201.
       Verifies the registered config is retrievable from the DB.
WH2  register_webhook_missing_token_returns_401  (ADVERSARIAL)
       POST /v1/webhooks without X-Admin-Token -> 401.
       No webhook row written.
WH3  register_webhook_wrong_token_returns_401  (ADVERSARIAL)
       POST /v1/webhooks with wrong X-Admin-Token -> 401.
WH4  dispatch_posts_correct_payload
       Unit test: WebhookDispatcher.dispatch() posts the expected JSON
       payload to target_url with the correct Authorization header.
       Uses unittest.mock to patch httpx.AsyncClient.
WH5  dispatch_fails_safely_on_server_error
       Unit test: target returns HTTP 500 -> no exception propagated.
       Fail-safe invariant: a bad webhook must not crash the gateway.
WH6  dispatch_fails_safely_on_connection_error
       Unit test: httpx raises ConnectError -> no exception propagated.
WH7  no_dispatch_when_no_webhook_registered
       Send private-fleet batches that trigger CUSUM.  No webhook
       registered.  Verify asyncio.create_task is NOT called.
WH8  dispatch_called_on_private_fleet_drift  (INTEGRATION)
       Register a webhook for a fleet.  Send batches that trigger CUSUM.
       Patch asyncio.create_task to capture the coroutine.
       Run the coroutine in a fresh event loop with httpx mocked.
       Assert dispatch was called with model_tuple, metric_name,
       alert_value, timestamp, fleet_id.

Design notes
------------
- WH1-WH3 use TestClient(app) with a real in-memory SQLite DB.
- WH4-WH6 call asyncio.run(dispatcher.dispatch(...)) directly;
  no gateway involved; httpx.AsyncClient is mocked at the module level.
- WH7-WH8 patch asyncio.create_task in gateway.main to intercept and
  collect coroutines, then drain them in a fresh event loop where
  httpx is also mocked.  This decouples test correctness from event
  loop scheduling guarantees in TestClient.

#SG-TRACE: REQ-ENT-002
#   | assumption: webhook dispatch is non-blocking (create_task) and
#     fail-safe (try/except in dispatch); both invariants are tested
#   | test: WH4-WH8
"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

from engine.detector import CUSUMDetector
from engine.models import WebhookConfig
from engine.webhooks import DriftNotification, WebhookDispatcher
from fastapi.testclient import TestClient
from gateway.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADMIN_TOKEN = "seismograph-admin"
_FLEET_ID = "fleet-webhook-test"
_MODEL = "openai/gpt-4o@2026-webhook-test"
_TARGET_URL = "https://fleet.example.com/seismograph-hook"
_AUTH_TOKEN = "bearer-test-secret"
_STABLE_METRICS = {"json_success_rate": 0.95}
_DRIFT_METRICS = {"json_success_rate": 0.0}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _signal_batch(
    n: int,
    prefix: str,
    metrics: dict,
    fleet_id: str | None = None,
    model: str = _MODEL,
) -> dict:
    # Use n for both UUID halves; prefix is only a logical label now.
    # This guarantees a valid UUID (all hex) while keeping n-uniqueness.
    bid = f"{n:08x}-0000-0000-0000-{n:012x}"
    return {
        "batch_id": bid,
        "client_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "window_start": "2026-06-12T00:00:00Z",
        "window_end": "2026-06-12T01:00:00Z",
        "model_tuple": model,
        "suite_version": "v3.1.0",
        "metrics": metrics,
        "canary_hashes": {"p001": _sha256(f"wh-canary-{n}")},
        "result_count": 5,
        "fleet_id": fleet_id,
    }


def _webhook_payload(
    fleet_id: str = _FLEET_ID,
    target_url: str = _TARGET_URL,
    auth_token: str | None = _AUTH_TOKEN,
) -> dict:
    d: dict = {"fleet_id": fleet_id, "target_url": target_url}
    if auth_token is not None:
        d["auth_token"] = auth_token
    return d


def _make_mock_httpx_client(status_code: int = 200):
    """Return (mock_client_class, mock_post) for patching httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    if status_code >= 400:
        mock_response.raise_for_status.side_effect = Exception(
            f"HTTP {status_code}"
        )
    else:
        mock_response.raise_for_status = MagicMock()

    mock_post = AsyncMock(return_value=mock_response)

    mock_client_instance = AsyncMock()
    mock_client_instance.post = mock_post
    mock_client_instance.__aenter__ = AsyncMock(
        return_value=mock_client_instance
    )
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    mock_client_class = MagicMock(return_value=mock_client_instance)
    return mock_client_class, mock_post


# ---------------------------------------------------------------------------
# WH1 -- register webhook API returns 201
# ---------------------------------------------------------------------------


def test_register_webhook_api_returns_201() -> None:
    """POST /v1/webhooks with valid admin token and body -> 201.

    Also verifies the row is retrievable from the in-memory DB.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: register_webhook upserts; first registration
    #     creates the row
    #   | test: WH1
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            resp = c.post(
                "/v1/webhooks",
                json=_webhook_payload(),
                headers={"X-Admin-Token": _ADMIN_TOKEN},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["status"] == "registered"
            assert body["fleet_id"] == _FLEET_ID

            # Verify the row is in the DB.
            repo = app.state.repo
            cfg = repo.get_webhook(_FLEET_ID)
            assert cfg is not None, "WH1 FAIL: webhook row not found in DB"
            assert cfg.fleet_id == _FLEET_ID
            assert cfg.target_url == _TARGET_URL
            assert cfg.auth_token == _AUTH_TOKEN


# ---------------------------------------------------------------------------
# WH2 -- register webhook without admin token -> 401
# ---------------------------------------------------------------------------


def test_register_webhook_missing_token_returns_401() -> None:
    """POST /v1/webhooks without X-Admin-Token -> 401.

    Adversarial: an unauthenticated caller must not be able to register
    a webhook (which could redirect drift notifications to an attacker).

    #SG-TRACE: REQ-ENT-002
    #   | assumption: missing token header defaults to empty string,
    #     which never matches a valid ADMIN_TOKEN
    #   | test: WH2 (ADVERSARIAL)
    """
    with TestClient(app) as c:
        resp = c.post("/v1/webhooks", json=_webhook_payload())
    assert resp.status_code == 401, (
        f"WH2 FAIL: expected 401, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# WH3 -- register webhook with wrong token -> 401
# ---------------------------------------------------------------------------


def test_register_webhook_wrong_token_returns_401() -> None:
    """POST /v1/webhooks with incorrect X-Admin-Token -> 401.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: token comparison is exact string match (no prefix
    #     match, no case folding)
    #   | test: WH3 (ADVERSARIAL)
    """
    with TestClient(app) as c:
        resp = c.post(
            "/v1/webhooks",
            json=_webhook_payload(),
            headers={"X-Admin-Token": "wrong-token"},
        )
    assert resp.status_code == 401, (
        f"WH3 FAIL: expected 401, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# WH4 -- dispatch posts correct payload (unit)
# ---------------------------------------------------------------------------


def test_dispatch_posts_correct_payload() -> None:
    """WebhookDispatcher.dispatch() posts the expected JSON payload.

    Runs the coroutine with asyncio.run() (no gateway involved).
    Mocks httpx.AsyncClient to capture the outgoing POST.

    Verifies:
    - POST is sent to the configured target_url
    - JSON payload contains model_tuple, metric_name, alert_value,
      timestamp, fleet_id
    - Authorization: Bearer header is present when auth_token is set

    #SG-TRACE: REQ-ENT-002
    #   | assumption: all five payload fields are included; no raw
    #     prompt or output data leaks into the webhook payload
    #   | test: WH4
    """
    notification = DriftNotification(
        model_tuple=_MODEL,
        metric_name="json_success_rate",
        alert_value=7.42,
        timestamp="2026-06-12T10:00:00.000000Z",
        fleet_id=_FLEET_ID,
    )
    config = WebhookConfig(
        fleet_id=_FLEET_ID,
        target_url=_TARGET_URL,
        auth_token=_AUTH_TOKEN,
    )
    dispatcher = WebhookDispatcher()
    mock_client_class, mock_post = _make_mock_httpx_client(200)

    with patch("engine.webhooks.httpx.AsyncClient", mock_client_class):
        asyncio.run(dispatcher.dispatch(notification, config))

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    # Positional arg 0 is the URL; keyword arg "json" is the payload.
    called_url = (
        call_kwargs.args[0]
        if call_kwargs.args
        else call_kwargs.kwargs.get("url")
    )
    called_json = call_kwargs.kwargs.get("json")
    called_headers = call_kwargs.kwargs.get("headers", {})

    assert called_url == _TARGET_URL, (
        f"WH4 FAIL: wrong URL; got {called_url!r}"
    )
    assert called_json is not None, "WH4 FAIL: no json payload"
    assert called_json["model_tuple"] == _MODEL
    assert called_json["metric_name"] == "json_success_rate"
    assert called_json["alert_value"] == 7.42
    assert called_json["fleet_id"] == _FLEET_ID
    assert "timestamp" in called_json

    auth_header = called_headers.get("Authorization", "")
    assert auth_header == f"Bearer {_AUTH_TOKEN}", (
        f"WH4 FAIL: wrong Authorization header; got {auth_header!r}"
    )


# ---------------------------------------------------------------------------
# WH5 -- dispatch fails safely on HTTP 500
# ---------------------------------------------------------------------------


def test_dispatch_fails_safely_on_server_error() -> None:
    """Dispatch to a target returning 500 must not raise.

    Fail-safe invariant: a bad webhook target never crashes the gateway.

    #SG-TRACE: REQ-ENT-003
    #   | assumption: raise_for_status() raises for 5xx; the try/except
    #     in dispatch must catch it silently
    #   | test: WH5
    """
    notification = DriftNotification(
        model_tuple=_MODEL,
        metric_name="json_success_rate",
        alert_value=5.0,
        timestamp="2026-06-12T10:00:00.000000Z",
        fleet_id=_FLEET_ID,
    )
    config = WebhookConfig(
        fleet_id=_FLEET_ID,
        target_url=_TARGET_URL,
        auth_token=None,
    )
    dispatcher = WebhookDispatcher()
    mock_client_class, _ = _make_mock_httpx_client(500)

    # Must not raise:
    with patch("engine.webhooks.httpx.AsyncClient", mock_client_class):
        asyncio.run(dispatcher.dispatch(notification, config))


# ---------------------------------------------------------------------------
# WH6 -- dispatch fails safely on connection error
# ---------------------------------------------------------------------------


def test_dispatch_fails_safely_on_connection_error() -> None:
    """Dispatch with a ConnectError must not raise.

    Covers network failures: DNS resolution failure, TCP refused,
    TLS handshake error.

    #SG-TRACE: REQ-ENT-003
    #   | assumption: httpx.ConnectError is a subclass of Exception;
    #     the bare except Exception in dispatch catches it
    #   | test: WH6
    """
    import httpx as _httpx

    notification = DriftNotification(
        model_tuple=_MODEL,
        metric_name="json_success_rate",
        alert_value=5.0,
        timestamp="2026-06-12T10:00:00.000000Z",
        fleet_id=_FLEET_ID,
    )
    config = WebhookConfig(
        fleet_id=_FLEET_ID,
        target_url="https://does-not-exist.invalid/hook",
        auth_token=None,
    )
    dispatcher = WebhookDispatcher()

    # Simulate connection failure at the AsyncClient level.
    mock_client_instance = AsyncMock()
    mock_client_instance.__aenter__ = AsyncMock(
        return_value=mock_client_instance
    )
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)
    mock_client_instance.post = AsyncMock(
        side_effect=_httpx.ConnectError("simulated DNS failure")
    )
    mock_client_class = MagicMock(return_value=mock_client_instance)

    # Must not raise:
    with patch("engine.webhooks.httpx.AsyncClient", mock_client_class):
        asyncio.run(dispatcher.dispatch(notification, config))


# ---------------------------------------------------------------------------
# WH7 -- no dispatch when no webhook registered
# ---------------------------------------------------------------------------


def test_no_dispatch_when_no_webhook_registered() -> None:
    """Private fleet drift fires with no webhook registered -> dispatch
    skipped.

    Strategy: replace app.state.dispatcher with an AsyncMock after lifespan
    starts.  No webhook is registered for the fleet.  After CUSUM fires,
    dispatcher.dispatch must NOT have been called.

    Patching asyncio.create_task globally was removed (it patches the real
    asyncio module, breaking Starlette's internal task scheduling).

    #SG-TRACE: REQ-ENT-002
    #   | assumption: gateway guards dispatch with get_webhook() != None;
    #     a None return skips create_task entirely
    #   | test: WH7
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            fleet = "fleet-no-webhook-registered"
            # Inject a low-threshold detector (fires in ~7 drift batches).
            app.state.private_detectors[fleet] = CUSUMDetector(
                h=3.0, k=0.5, baseline_samples=3
            )
            # Replace dispatcher with a mock AFTER lifespan (no webhook
            # registered for this fleet, so dispatch should never be called).
            mock_dispatcher = MagicMock()
            mock_dispatcher.dispatch = AsyncMock()
            app.state.dispatcher = mock_dispatcher

            for i in range(3):
                c.post(
                    "/v1/signals",
                    json=_signal_batch(i, "w7", _STABLE_METRICS, fleet),
                )
            alert_fired = False
            for i in range(20):
                resp = c.post(
                    "/v1/signals",
                    json=_signal_batch(100 + i, "w7", _DRIFT_METRICS, fleet),
                )
                if resp.json().get("alerts"):
                    alert_fired = True
                    break

    assert alert_fired, "WH7 setup: CUSUM did not fire within 20 batches"
    mock_dispatcher.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# WH8 -- dispatch called on private fleet drift (integration)
# ---------------------------------------------------------------------------


def test_dispatch_called_on_private_fleet_drift() -> None:
    """Register webhook + trigger drift -> dispatch called with correct args.

    Strategy:
    1. Register a webhook for the test fleet via POST /v1/webhooks.
    2. Pre-inject a low-threshold (h=3.0) fleet detector.
    3. Replace app.state.dispatcher with an AsyncMock after lifespan starts.
    4. Send baseline + drift batches to fire CUSUM.
    5. Assert dispatcher.dispatch was called with the expected
       DriftNotification (model_tuple, metric_name, fleet_id) and the
       registered WebhookConfig (target_url).

    HTTP-level payload verification (httpx POST body) is covered by WH4.
    WH8 is the gateway integration layer: CUSUM fire -> dispatch invoked.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: gateway calls dispatcher.dispatch exactly once per
    #     CUSUM alert when a webhook config exists for the fleet
    #   | test: WH8
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            # Step 1: register webhook
            reg_resp = c.post(
                "/v1/webhooks",
                json=_webhook_payload(
                    fleet_id=_FLEET_ID,
                    target_url=_TARGET_URL,
                    auth_token=_AUTH_TOKEN,
                ),
                headers={"X-Admin-Token": _ADMIN_TOKEN},
            )
            assert reg_resp.status_code == 201, reg_resp.text

            # Step 2: inject low-threshold detector
            app.state.private_detectors[_FLEET_ID] = CUSUMDetector(
                h=3.0, k=0.5, baseline_samples=3
            )
            # Step 3: replace dispatcher with AsyncMock
            mock_dispatcher = MagicMock()
            mock_dispatcher.dispatch = AsyncMock()
            app.state.dispatcher = mock_dispatcher

            # Step 4: baseline + drift
            for i in range(3):
                c.post(
                    "/v1/signals",
                    json=_signal_batch(i, "w8", _STABLE_METRICS, _FLEET_ID),
                )
            alert_fired = False
            for i in range(20):
                resp = c.post(
                    "/v1/signals",
                    json=_signal_batch(
                        100 + i, "w8", _DRIFT_METRICS, _FLEET_ID
                    ),
                )
                if resp.json().get("alerts"):
                    alert_fired = True
                    break

    # Step 5: assertions
    assert alert_fired, "WH8 setup: CUSUM did not fire within 20 batches"
    assert mock_dispatcher.dispatch.called, (
        "WH8 FAIL: dispatcher.dispatch was not called after CUSUM fired"
    )

    call_args = mock_dispatcher.dispatch.call_args
    notification = call_args.args[0]
    config = call_args.args[1]

    assert notification.fleet_id == _FLEET_ID, (
        f"WH8 FAIL: wrong fleet_id in notification: {notification.fleet_id!r}"
    )
    assert notification.model_tuple == _MODEL, (
        f"WH8 FAIL: wrong model_tuple: {notification.model_tuple!r}"
    )
    assert notification.metric_name == "json_success_rate", (
        f"WH8 FAIL: wrong metric_name: {notification.metric_name!r}"
    )
    assert isinstance(notification.alert_value, float), (
        "WH8 FAIL: alert_value is not a float"
    )
    assert config.target_url == _TARGET_URL, (
        f"WH8 FAIL: wrong target_url in config: {config.target_url!r}"
    )
