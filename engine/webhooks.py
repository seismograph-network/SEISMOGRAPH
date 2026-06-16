"""
seismograph.engine.webhooks
============================
Asynchronous webhook dispatcher for enterprise fleet drift notifications.

When a private-fleet CUSUMDetector fires a LocalDriftAlert, the gateway
calls WebhookDispatcher.dispatch() via asyncio.create_task() so that the
HTTP call to the customer's endpoint does not block the 202 response to
the probe.

Design decisions
----------------
DriftNotification
    A lightweight dataclass assembled from the DetectorDriftAlert in the
    gateway endpoint.  Decouples the dispatcher from both the ORM layer
    (no session required) and the detector internals.  Passed to dispatch
    by value -- no shared mutable state.

Fail-safe contract
    Any exception raised by the httpx call (connection error, timeout,
    non-2xx status, TLS error) is caught and logged at ERROR level.
    The exception is NEVER re-raised.  A failing customer webhook must
    not crash the gateway or halt the ingestion pipeline.

Authorization
    If WebhookConfig.auth_token is non-None, it is injected as:
        Authorization: Bearer <token>
    The token is never logged, never included in any response body,
    and never stored outside the WebhookConfig row.

Privacy invariants (Aegis)
    The dispatch payload contains only:
      - model_tuple (public model identifier)
      - metric_name (metric key, not a raw response)
      - alert_value (CUSUM score, a numeric statistic)
      - timestamp (ISO-8601 UTC string)
      - fleet_id (the recipient's own fleet identifier)
    No raw prompts, no raw model outputs, no client_id, no
    probe-internal identifiers are included.

#SG-TRACE: REQ-ENT-002
#   | assumption: dispatch is called only on the private fleet path;
#     never on the public path (AgreementScorer alerts are not webhookable
#     in Phase 3)
#   | test: test_dispatch_posts_correct_payload
#SG-TRACE: REQ-ENT-003
#   | assumption: failing webhook does not affect gateway availability;
#     try/except in dispatch is the sole enforcement point
#   | test: test_dispatch_fails_safely_on_500
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from engine.models import WebhookConfig

logger = logging.getLogger(__name__)

# Outgoing webhook HTTP timeout (seconds).
# Short enough to avoid stacking up blocked tasks on a slow target.
_DISPATCH_TIMEOUT: float = 10.0


@dataclass
class DriftNotification:
    """Payload assembled from a DetectorDriftAlert for webhook dispatch.

    All fields are safe to transmit to a third-party endpoint:
    no raw prompts, no raw outputs, no internal UUIDs other than
    the recipient's own fleet_id.

    Fields
    ------
    model_tuple:
        Model identifier string from the firing alert.
    metric_name:
        Name of the drifting metric (e.g. "json_success_rate").
    alert_value:
        CUSUM score at alert time.
    timestamp:
        ISO-8601 UTC string at the moment the gateway processed the
        alert (naive UTC converted to "YYYY-MM-DDTHH:MM:SS.ffffffZ").
    fleet_id:
        The recipient's fleet identifier.  Included so the webhook
        handler can correlate the notification with its own fleet.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: fleet_id is the recipient's own value; including
    #     it does not leak cross-fleet information
    #   | test: test_dispatch_posts_correct_payload
    """

    model_tuple: str
    metric_name: str
    alert_value: float
    timestamp: str
    fleet_id: str


class WebhookDispatcher:
    """Sends HTTP drift notifications to registered fleet webhook URLs.

    Stateless -- all required context is passed to dispatch() at call
    time.  A single shared instance is stored on app.state.dispatcher
    in the gateway lifespan.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: one dispatcher instance is sufficient for Phase 3;
    #     connection pooling is handled by httpx.AsyncClient internally
    #   | test: test_dispatch_posts_correct_payload
    """

    async def dispatch(
        self,
        notification: DriftNotification,
        config: WebhookConfig,
    ) -> None:
        """POST a drift notification to the registered webhook URL.

        Assembles the JSON payload and Authorization header, opens an
        httpx.AsyncClient, and fires the POST.  Any exception is caught
        and logged -- dispatch failures are non-fatal.

        Parameters
        ----------
        notification:
            DriftNotification assembled from the firing DetectorDriftAlert.
        config:
            WebhookConfig retrieved from the repository for this fleet.

        #SG-TRACE: REQ-ENT-003
        #   | assumption: httpx.AsyncClient(timeout=10.0) is sufficient
        #     for Phase 3; Phase 4 adds retry logic with exponential
        #     backoff
        #   | test: test_dispatch_posts_correct_payload
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.auth_token is not None:
            headers["Authorization"] = f"Bearer {config.auth_token}"

        payload: dict[str, object] = {
            "model_tuple": notification.model_tuple,
            "metric_name": notification.metric_name,
            "alert_value": notification.alert_value,
            "timestamp": notification.timestamp,
            "fleet_id": notification.fleet_id,
        }

        try:
            async with httpx.AsyncClient(timeout=_DISPATCH_TIMEOUT) as client:
                response = await client.post(
                    config.target_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                logger.info(
                    "Webhook delivered | fleet=%s url=%s status=%d",
                    notification.fleet_id,
                    config.target_url,
                    response.status_code,
                )
        except Exception as exc:
            # Fail-safe: a bad webhook target must not crash the gateway.
            # Log at ERROR so ops can observe failures without
            # alerting paging systems for a third-party endpoint issue.
            logger.error(
                "Webhook dispatch failed | fleet=%s url=%s error=%r",
                notification.fleet_id,
                config.target_url,
                exc,
            )
