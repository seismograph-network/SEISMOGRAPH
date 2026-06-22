"""
seismograph.gateway.main
=========================
FastAPI application -- SEISMOGRAPH Phase 3 ingestion gateway.

Mounts POST /v1/signals over the strictly-validated InboundSignalBatch
schema.  On acceptance, routes the batch through one of two paths based
on the fleet_id field:

Public path (fleet_id is None)
--------------------------------
  1. Batch is persisted via BaseRepository.save_batch().
  2. Each metric value is fed to the global CUSUMDetector.
  3. Any local DriftAlerts are persisted via save_local_alert() with
     the submitting client_id and fleet_id=None.
  4. Each local alert is fed into AgreementScorer.  If quorum is reached
     (>= QUORUM_MIN distinct orgs), a PublicDriftAlert is saved and the
     scorer state is cleared for that model_tuple.
  5. 202 response returns the local alert list (CUSUM events).

Private fleet path (fleet_id is non-None)
-------------------------------------------
  1. Batch is persisted via BaseRepository.save_batch() (fleet_id stored).
  2. A per-fleet CUSUMDetector is retrieved or lazily created from
     app.state.private_detectors[fleet_id].
  3. Any local DriftAlerts are persisted via save_local_alert() with
     fleet_id set -- NEVER passed to AgreementScorer.
  4. If a webhook is registered for this fleet, an async dispatch task
     is scheduled via asyncio.create_task(dispatcher.dispatch(...)).
     The dispatch runs after the 202 response is sent and does NOT
     block the probe.
  5. 202 response returns the local alert list for that fleet.
  INVARIANT: Private fleet alerts NEVER promote to PublicDriftAlert.
  INVARIANT: Private fleet alerts are NOT visible via GET /v1/weather.
  INVARIANT: A failing webhook never affects the 202 response.

Also mounts:
  POST /v1/webhooks -- register or replace a fleet webhook.
    Requires X-Admin-Token header == ADMIN_TOKEN env var.
    Returns 201 on success.
  GET /v1/weather -- drift-weather status for all known model_tuples.
    Reads PublicDriftAlert only; single-org local alerts and private
    fleet alerts do NOT affect the weather status.
  GET /           -- serves the landing page (dashboard/static/landing.html).
  GET /dashboard  -- serves the Model Weather dashboard (index.html).
  /static/*       -- static assets (JS, CSS).

Storage backends
----------------
Controlled by the STORAGE_BACKEND environment variable:

  STORAGE_BACKEND=sqlite (default)
    Uses SignalRepository (SQLAlchemy / SQLite).
    DB file path from SEISMOGRAPH_DB_URL (default: data/seismograph.db).
    Zero external dependencies; suitable for local dev and test.
    Supports webhook registration (register_webhook / get_webhook).

  STORAGE_BACKEND=clickhouse
    Uses ClickHouseRepository (clickhouse-connect).
    Connection from CLICKHOUSE_HOST (default: localhost),
    CLICKHOUSE_PORT (default: 8123), CLICKHOUSE_USER (default: default),
    CLICKHOUSE_PASSWORD (default: ""), CLICKHOUSE_DATABASE (default:
    default).
    setup_tables() is called on startup -- idempotent.
    Webhook registration is NOT supported (returns 500).

Quorum backends
---------------
Controlled by the QUORUM_BACKEND environment variable:

  QUORUM_BACKEND=memory (default)
    Uses AgreementScorer (in-process dict).
    Simple, zero-dependency.  State lost on gateway restart.
    Suitable for single-node dev, CI, and Phase 1 deployments.

  QUORUM_BACKEND=redis
    Uses RedisAgreementScorer (Redis-backed Sets).
    Connection URL from REDIS_URL (default: redis://localhost:6379/0).
    Quorum state survives gateway restarts and is shared across
    multiple gateway replicas behind a load balancer.

Admin token
-----------
The POST /v1/webhooks endpoint requires:
    X-Admin-Token: <value>
where <value> matches the ADMIN_TOKEN environment variable
(default: "seismograph-admin" for development).  Set a strong random
value in production via the .env file.

Endpoint contract
-----------------
POST /v1/signals

  Headers (mandatory from Phase 2):
    X-Signature:  Hex-encoded Ed25519 signature over raw request body.
    X-Public-Key: Hex-encoded Ed25519 public key for this client_id.

  Body: InboundSignalBatch (gateway/schema.py) as canonical JSON.

  Responses:
    202 Accepted       -- valid batch, metrics ingested, alerts returned.
    401 Unauthorized   -- signature verification failed.
    422 Unprocessable  -- schema validation failure.

POST /v1/webhooks

  Headers (mandatory):
    X-Admin-Token: <ADMIN_TOKEN value>

  Body: WebhookRegistration (gateway/schema.py) as JSON.

  Responses:
    201 Created        -- webhook registered or replaced.
    401 Unauthorized   -- missing or incorrect admin token.
    422 Unprocessable  -- schema validation failure.

GET /v1/weather
  No auth required. Returns list[ModelWeatherResponse].
  Status DRIFTING iff a PublicDriftAlert (quorum-verified) exists in
  the last 24h.  Private fleet alerts produce STABLE.

#SG-TRACE: REQ-GW-018
#   | assumption: single-node, in-memory CUSUMDetector + pluggable
#     storage is sufficient for Phase 2 MVP; Redis multi-node quorum
#     is wired via QUORUM_BACKEND=redis
#   | test: test_valid_payload_returns_202
#SG-TRACE: REQ-ENT-001
#   | assumption: private fleet path is gated on fleet_id is not None;
#     never on truthiness, to protect against fleet_id="" edge case
#   | test: test_enterprise_private_alert_not_in_weather
#SG-TRACE: REQ-ENT-002
#   | assumption: webhook dispatch uses asyncio.create_task so the
#     HTTP call to the customer endpoint never blocks the 202 response
#   | test: test_webhook_dispatch_called_on_private_alert
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import pathlib
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from engine.audit import AlertNotFoundError, AuditReportGenerator
from engine.correlation import AgreementScorer, ChangePointResult
from engine.detector import CUSUMDetector
from engine.detector import DriftAlert as DetectorDriftAlert
from engine.repository import DEFAULT_DB_URL, BaseRepository, SignalRepository
from engine.webhooks import DriftNotification, WebhookDispatcher
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from gateway.auth import verify_signature
from gateway.schema import (
    InboundSignalBatch,
    ModelWeatherResponse,
    WebhookRegistration,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static directory resolution (absolute, __file__-relative)
# ---------------------------------------------------------------------------

_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).parent.parent
_STATIC_DIR: pathlib.Path = _REPO_ROOT / "dashboard" / "static"

# Default Redis URL used when QUORUM_BACKEND=redis and REDIS_URL unset.
_DEFAULT_REDIS_URL: str = "redis://localhost:6379/0"

# Default admin token for POST /v1/webhooks (development only).
# Override with ADMIN_TOKEN env var in production.
_DEFAULT_ADMIN_TOKEN: str = "seismograph-admin"

# Export token sentinel — no default; unset means the endpoint is disabled.
# Set SEISMOGRAPH_EXPORT_TOKEN in production via the .env file.
_EXPORT_TOKEN_ENV_VAR: str = "SEISMOGRAPH_EXPORT_TOKEN"


# ---------------------------------------------------------------------------
# Engine bootstrap (callable independently for testing)
# ---------------------------------------------------------------------------


def bootstrap_detector(
    detector: CUSUMDetector,
    repo: BaseRepository,
) -> int:
    """Warm up CUSUMDetector from stored historical signals.

    Iterates all distinct model_tuples in the DB, fetches the 50 most
    recent signals for each, and feeds them into the detector
    in chronological (oldest-first) order.

    Alerts emitted during bootstrap are DISCARDED -- we only restore
    the baseline accumulation state, not re-fire historical alerts.
    AgreementScorer is NOT involved during bootstrap.

    Works with any BaseRepository implementation (SQLite or ClickHouse):
    both return objects with .avg_output_length and .json_success_rate
    attributes via duck typing.

    Parameters
    ----------
    detector:
        The CUSUMDetector instance to warm up.
    repo:
        Repository to read historical signals from.

    Returns
    -------
    int
        Total number of (model_tuple, metric_name) observations fed
        to the detector across all streams.

    #SG-TRACE: REQ-GW-022
    #   | assumption: reversed(get_recent_signals(limit=50)) gives
    #     chronological order since get_recent_signals returns DESC
    #   | test: test_bootstrap_warms_cusum_detector
    """
    total = 0
    for model_tuple in repo.get_all_model_tuples():
        signals = repo.get_recent_signals(model_tuple, limit=50)
        # signals are newest-first; feed oldest-first for correct order
        for i, signal in enumerate(reversed(signals)):
            ts = i * 1_000_000  # synthetic monotonic ns: 1 ms apart
            for metric_name in ("json_success_rate", "avg_output_length"):
                value = getattr(signal, metric_name, None)
                if value is not None:
                    # Discard alert -- bootstrap does not re-alert
                    detector.update(
                        model_tuple=model_tuple,
                        metric_name=metric_name,
                        value=float(value),
                        timestamp_ns=ts,
                    )
                    total += 1
    return total


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Initialise global state on startup; release on shutdown.

    Startup
    -------
    1. Creates CUSUMDetector(h=5.0, k=0.5, baseline_samples=30)
       for the public network path.
    2. Selects quorum backend from QUORUM_BACKEND env var.
    3. Selects storage backend from STORAGE_BACKEND env var.
    4. Calls bootstrap_detector() to restore baseline from DB history.
    5. Initialises app.state.private_detectors = {} for per-fleet
       CUSUMDetectors (lazily populated on first private-fleet signal).
    6. Creates a single WebhookDispatcher instance on app.state.

    Shutdown
    --------
    In-memory CUSUM state is discarded (both public and private fleet
    detectors).  Redis quorum state persists (TTL-managed by Redis).

    #SG-TRACE: REQ-GW-025
    #   | assumption: QUORUM_BACKEND env var switches scorer at startup;
    #     hot-reload of scorer type not supported (restart required)
    #   | test: test_single_org_noise_blocked
    #SG-TRACE: REQ-ENT-001
    #   | assumption: private_detectors is a plain dict[str, CUSUM];
    #     thread-safety acceptable for single-process Phase 3 MVP
    #   | test: test_enterprise_private_alert_not_in_weather
    #SG-TRACE: REQ-ENT-002
    #   | assumption: dispatcher is stateless; one instance shared
    #     across all requests is safe
    #   | test: test_webhook_dispatch_called_on_private_alert
    """
    db_url = os.getenv("SEISMOGRAPH_DB_URL", DEFAULT_DB_URL)
    storage_backend = os.getenv("STORAGE_BACKEND", "sqlite").lower()
    quorum_backend = os.getenv("QUORUM_BACKEND", "memory").lower()

    detector = CUSUMDetector(h=5.0, k=0.5, baseline_samples=30)

    # --- Quorum scorer selection -------------------------------------------
    if quorum_backend == "redis":
        import redis as redis_lib  # type: ignore[import]
        from engine.scorer_redis import RedisAgreementScorer

        redis_url = os.getenv("REDIS_URL", _DEFAULT_REDIS_URL)
        redis_client = redis_lib.Redis.from_url(redis_url)
        scorer: AgreementScorer | RedisAgreementScorer = RedisAgreementScorer(
            redis_client
        )
        logger.info(
            "SEISMOGRAPH gateway starting -- Redis quorum backend | url=%s",
            redis_url,
        )
    else:
        scorer = AgreementScorer()
        logger.info("SEISMOGRAPH gateway starting -- in-memory quorum backend")

    # --- Storage backend selection ----------------------------------------
    if storage_backend == "clickhouse":
        import clickhouse_connect  # type: ignore[import]
        from engine.clickhouse import ClickHouseRepository

        ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
        ch_port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
        ch_user = os.getenv("CLICKHOUSE_USER", "default")
        ch_password = os.getenv("CLICKHOUSE_PASSWORD", "")
        ch_database = os.getenv("CLICKHOUSE_DATABASE", "default")
        ch_client = clickhouse_connect.get_client(
            host=ch_host,
            port=ch_port,
            username=ch_user,
            password=ch_password,
            database=ch_database,
        )
        repo: BaseRepository = ClickHouseRepository(ch_client)
        repo.setup_tables()  # type: ignore[attr-defined]
        logger.info(
            "SEISMOGRAPH gateway starting -- ClickHouse backend"
            " | host=%s port=%d db=%s",
            ch_host,
            ch_port,
            ch_database,
        )
    else:
        repo = SignalRepository(db_url)
        logger.info(
            "SEISMOGRAPH gateway starting -- SQLite backend"
            " | CUSUMDetector(h=5.0, k=0.5, baseline_samples=30)"
            " | db=%s",
            db_url,
        )

    obs_count = bootstrap_detector(detector, repo)
    logger.info(
        "CUSUMDetector bootstrap complete | observations_fed=%d",
        obs_count,
    )

    app.state.detector = detector
    app.state.scorer = scorer
    app.state.repo = repo
    # Per-fleet private detectors: dict[fleet_id, CUSUMDetector].
    # Lazily populated on the first signal from each fleet.
    # INVARIANT: entries here never interact with AgreementScorer.
    app.state.private_detectors: dict[str, CUSUMDetector] = {}
    # Stateless webhook dispatcher; shared across all requests.
    app.state.dispatcher = WebhookDispatcher()
    yield
    logger.info("SEISMOGRAPH gateway shutting down.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SEISMOGRAPH Ingestion Gateway",
    description=(
        "Privacy-preserving ingestion for canary probe signal batches. "
        "Raw prompt text and model outputs are never accepted."
    ),
    version="0.4.0",
    lifespan=lifespan,
)

# Mount static dashboard assets.
app.mount(
    "/static",
    StaticFiles(directory=str(_STATIC_DIR)),
    name="static",
)


# ---------------------------------------------------------------------------
# Weather helper (module-level for testability)
# ---------------------------------------------------------------------------


def _compute_model_weather(
    repo: BaseRepository,
    model_tuple: str,
) -> ModelWeatherResponse:
    """Compute drift-weather status for a single model_tuple.

    Status is DRIFTING if any PublicDriftAlert (quorum-verified) was
    recorded in the last 24h.  A single-org local alert or a private
    fleet alert does NOT change the status to DRIFTING.
    Recent averages are computed over the last 10 signal batches.

    #SG-TRACE: REQ-GW-023
    #   | assumption: last-10-signal average is a sufficient recency
    #     proxy for Phase 1; Phase 2 uses a time-windowed aggregate
    #   | test: test_weather_returns_stable_when_no_alerts
    """
    signals = repo.get_recent_signals(model_tuple, limit=10)

    lengths = [
        s.avg_output_length for s in signals if s.avg_output_length is not None
    ]
    rates = [
        s.json_success_rate for s in signals if s.json_success_rate is not None
    ]
    avg_length = sum(lengths) / len(lengths) if lengths else None
    avg_rate = sum(rates) / len(rates) if rates else None

    recent_alerts = repo.get_recent_alerts(model_tuple, hours_back=24)
    last_ts = recent_alerts[0].timestamp if recent_alerts else None
    status = "DRIFTING" if recent_alerts else "STABLE"

    return ModelWeatherResponse(
        model_tuple=model_tuple,
        status=status,
        last_alert_timestamp=last_ts,
        recent_avg_output_length=avg_length,
        recent_json_success_rate=avg_rate,
    )


# ---------------------------------------------------------------------------
# GET / -- landing page
# GET /dashboard -- Model Weather dashboard
# ---------------------------------------------------------------------------


@app.get("/", response_class=FileResponse, include_in_schema=False)
async def landing_root() -> FileResponse:
    """Serve the SEISMOGRAPH landing page.

    #SG-TRACE: REQ-DASH-001
    #   | assumption: _STATIC_DIR is an absolute path; FileResponse
    #     does not depend on CWD
    #   | test: test_landing_root_returns_html
    """
    return FileResponse(str(_STATIC_DIR / "landing.html"))


@app.get("/dashboard", response_class=FileResponse, include_in_schema=False)
async def dashboard_root() -> FileResponse:
    """Serve the SEISMOGRAPH Model Weather dashboard.

    #SG-TRACE: REQ-DASH-002
    #   | assumption: index.html polls GET /v1/weather via fetch(); the
    #     route change is transparent to the frontend JS
    #   | test: test_dashboard_route_returns_html
    """
    return FileResponse(str(_STATIC_DIR / "index.html"))


# ---------------------------------------------------------------------------
# POST /v1/webhooks
# ---------------------------------------------------------------------------


@app.post("/v1/webhooks", status_code=status.HTTP_201_CREATED)
async def register_webhook(
    request: Request,
    x_admin_token: str = Header(default=""),
) -> dict[str, str]:
    """Register or replace a webhook URL for a private fleet.

    Accepts a WebhookRegistration payload and persists it via the
    repository.  Registering a new URL for the same fleet_id atomically
    replaces the prior entry.

    Only the SQLite storage backend supports this endpoint.  The
    ClickHouse backend will return 500 (NotImplementedError).

    Auth
    ----
    X-Admin-Token header must match the ADMIN_TOKEN environment variable
    (default: "seismograph-admin").  Provide a strong random value in
    production.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: admin token is a simple shared secret for Phase 3;
    #     Phase 4 replaces with fleet-scoped API keys (per-fleet OAuth)
    #   | test: test_register_webhook_api
    """
    admin_token = os.getenv("ADMIN_TOKEN", _DEFAULT_ADMIN_TOKEN)
    if x_admin_token != admin_token:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "detail": "Invalid or missing X-Admin-Token.",
            },
        )

    raw_bytes = await request.body()
    try:
        reg = WebhookRegistration.model_validate_json(raw_bytes)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "schema_validation_failed",
                "detail": str(exc),
            },
        ) from exc

    repo: BaseRepository = request.app.state.repo
    repo.register_webhook(
        fleet_id=reg.fleet_id,
        target_url=reg.target_url,
        auth_token=reg.auth_token,
    )
    logger.info(
        "Webhook registered | fleet=%s url=%s",
        reg.fleet_id,
        reg.target_url,
    )
    return {"status": "registered", "fleet_id": reg.fleet_id}


# ---------------------------------------------------------------------------
# POST /v1/signals
# ---------------------------------------------------------------------------


@app.post("/v1/signals", status_code=202)
async def ingest_signals(
    request: Request,
    x_signature: str = Header(default=""),
    x_public_key: str = Header(default=""),
) -> dict[str, Any]:
    """Ingest one signed canary probe signal batch.

    Routes through public or private path based on batch.fleet_id.

    Public path (fleet_id is None):
      1. Raw body read.
      2. Signature verification.
      3. Pydantic parsing.
      4. Persistence (save_batch).
      5. CUSUM ingestion via global detector.
      6. Local alert persistence (fleet_id=None).
      7. AgreementScorer -- quorum check; save_public_alert on quorum.
      8. 202 Accepted.

    Private path (fleet_id is not None):
      1-4. Same as public.
      5. CUSUM ingestion via per-fleet detector (lazy-init).
      6. Local alert persistence (fleet_id set).
      7. If webhook registered: asyncio.create_task(dispatch).
         Dispatch is non-blocking; 202 is returned immediately.
      8. NO AgreementScorer call.
      9. 202 Accepted.

    INVARIANT: private fleet alerts never enter AgreementScorer.
    INVARIANT: private fleet alerts are never promoted to PublicDriftAlert.
    INVARIANT: a failing webhook never affects the 202 response.

    # SG-TRACE: REQ-GW-019 | test: test_valid_payload_returns_202
    # SG-TRACE: REQ-GW-021 | test: test_unauthorized_returns_401
    # SG-TRACE: REQ-ENT-001
    # | test: test_enterprise_private_alert_not_in_weather
    # SG-TRACE: REQ-ENT-002
    # | test: test_webhook_dispatch_called_on_private_alert
    """
    # Step 1: Read raw body bytes.
    raw_bytes: bytes = await request.body()

    # Step 2: Verify Ed25519 signature over raw bytes.
    if not verify_signature(raw_bytes, x_signature, x_public_key):
        logger.error("401 Unauthorized | signature_verification_failed")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "signature_verification_failed",
                "detail": ("Ed25519 signature is invalid for this batch."),
            },
        )

    # Step 3: Parse and validate the Pydantic model.
    try:
        batch = InboundSignalBatch.model_validate_json(raw_bytes)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "schema_validation_failed",
                "detail": str(exc),
            },
        ) from exc

    # Step 4: Persist batch to storage backend.
    repo: BaseRepository = request.app.state.repo
    repo.save_batch(batch)

    alerts: list[dict[str, Any]] = []
    ts: int = time.monotonic_ns()

    if batch.fleet_id is not None:
        # ------------------------------------------------------------------
        # PRIVATE FLEET PATH
        # ------------------------------------------------------------------
        # Use a per-fleet CUSUMDetector; NEVER touch AgreementScorer.
        private_detectors: dict[str, CUSUMDetector] = (
            request.app.state.private_detectors
        )
        if batch.fleet_id not in private_detectors:
            private_detectors[batch.fleet_id] = CUSUMDetector(
                h=5.0, k=0.5, baseline_samples=30
            )
            logger.info(
                "Private detector created | fleet_id=%s model=%s",
                batch.fleet_id,
                batch.model_tuple,
            )
        fleet_detector = private_detectors[batch.fleet_id]
        dispatcher: WebhookDispatcher = request.app.state.dispatcher

        for metric_name, value in batch.metrics.items():
            alert: DetectorDriftAlert | None = fleet_detector.update(
                model_tuple=batch.model_tuple,
                metric_name=metric_name,
                value=float(value),
                timestamp_ns=ts,
            )
            if alert is not None:
                logger.warning(
                    "PRIVATE FLEET ALERT | fleet=%s model=%s"
                    " metric=%s direction=%s score=%.4f",
                    batch.fleet_id,
                    alert.model_tuple,
                    alert.metric_name,
                    alert.direction,
                    alert.cusum_score,
                )
                # Persist with fleet_id; do NOT call scorer.
                repo.save_local_alert(
                    alert,
                    str(batch.client_id),
                    fleet_id=batch.fleet_id,
                )

                # --- Webhook dispatch (non-blocking) ----------------------
                # get_webhook returns None if no webhook is registered;
                # dispatch is skipped silently in that case.
                wh_config = repo.get_webhook(batch.fleet_id)
                if wh_config is not None:
                    now_iso = (
                        datetime.now(timezone.utc)
                        .replace(tzinfo=None)
                        .isoformat()
                        + "Z"
                    )
                    notification = DriftNotification(
                        model_tuple=alert.model_tuple,
                        metric_name=alert.metric_name,
                        alert_value=alert.cusum_score,
                        timestamp=now_iso,
                        fleet_id=batch.fleet_id,
                    )
                    asyncio.create_task(
                        dispatcher.dispatch(notification, wh_config)
                    )
                    logger.debug(
                        "Webhook task scheduled | fleet=%s",
                        batch.fleet_id,
                    )

                alerts.append(
                    {
                        "model_tuple": alert.model_tuple,
                        "metric_name": alert.metric_name,
                        "direction": alert.direction,
                        "cusum_score": round(alert.cusum_score, 6),
                        "threshold": alert.threshold,
                        "window_count": alert.window_count,
                    }
                )

    else:
        # ------------------------------------------------------------------
        # PUBLIC NETWORK PATH (unchanged from Phase 2)
        # ------------------------------------------------------------------
        detector: CUSUMDetector = request.app.state.detector
        scorer = request.app.state.scorer

        for metric_name, value in batch.metrics.items():
            alert = detector.update(
                model_tuple=batch.model_tuple,
                metric_name=metric_name,
                value=float(value),
                timestamp_ns=ts,
            )
            if alert is not None:
                logger.warning(
                    "LOCAL ALERT | model=%s metric=%s direction=%s score=%.4f",
                    alert.model_tuple,
                    alert.metric_name,
                    alert.direction,
                    alert.cusum_score,
                )

                # Persist local alert (no fleet_id on public path)
                repo.save_local_alert(alert, str(batch.client_id))

                # Bridge DetectorDriftAlert -> ChangePointResult
                cp = ChangePointResult(
                    model_tuple=alert.model_tuple,
                    change_detected=True,
                    score=alert.cusum_score,
                    threshold=alert.threshold,
                    contributing_orgs=[str(batch.client_id)],
                )
                scorer.ingest(cp)

                # Check quorum; promote if >= QUORUM_MIN distinct orgs
                org_count = scorer.promote_to_public_alert(alert.model_tuple)
                if org_count is not None:
                    repo.save_public_alert(
                        model_tuple=alert.model_tuple,
                        metric_name=alert.metric_name,
                        contributing_org_count=org_count,
                    )
                    scorer.clear(alert.model_tuple)
                    logger.warning(
                        "PUBLIC ALERT | model=%s metric=%s orgs=%d",
                        alert.model_tuple,
                        alert.metric_name,
                        org_count,
                    )

                alerts.append(
                    {
                        "model_tuple": alert.model_tuple,
                        "metric_name": alert.metric_name,
                        "direction": alert.direction,
                        "cusum_score": round(alert.cusum_score, 6),
                        "threshold": alert.threshold,
                        "window_count": alert.window_count,
                    }
                )

    logger.info(
        "202 Accepted | batch_id=%s model=%s fleet=%s results=%d alerts=%d",
        batch.batch_id,
        batch.model_tuple,
        batch.fleet_id,
        batch.result_count,
        len(alerts),
    )
    return {
        "status": "accepted",
        "batch_id": str(batch.batch_id),
        "result_count": batch.result_count,
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# GET /v1/alerts/{alert_id}/export
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /v1/weather
# ---------------------------------------------------------------------------


@app.get("/v1/weather", status_code=200)
async def model_weather(
    request: Request,
) -> list[ModelWeatherResponse]:
    """Return current drift-weather for all known model_tuples.

    No authentication required -- weather data is aggregated and
    anonymised (no raw prompts, no client identifiers).

    Status DRIFTING requires a PublicDriftAlert (quorum-verified) within
    the last 24h.  Private fleet alerts are not reflected here.

    #SG-TRACE: REQ-GW-023
    #   | assumption: get_all_model_tuples() is cheap for Phase 2
    #     cardinality; no pagination required
    #   | test: test_weather_endpoint_empty_returns_200
    """
    repo: BaseRepository = request.app.state.repo
    return [
        _compute_model_weather(repo, mt) for mt in repo.get_all_model_tuples()
    ]


@app.get("/v1/alerts/{alert_id}/export", status_code=200)
async def export_audit_report(alert_id: int, request: Request) -> JSONResponse:
    """Return a SOC 2 audit-grade JSON export for a recorded drift alert.

    Builds a deterministically checksummed report containing:
      - export_timestamp   : naive UTC ISO-8601 generation time
      - alert_details      : full alert record (local or public)
      - baseline_evidence  : up to 50 telemetry signals preceding alert
      - report_checksum    : SHA-256 of canonical JSON (sorted keys)

    The report is returned as an attachment so browsers and audit tools
    automatically prompt a file download.

    Parameters
    ----------
    alert_id:
        Integer primary key.  Resolution order: local_drift_alerts
        first, then public_drift_alerts.

    Returns
    -------
    JSONResponse
        200 with Content-Disposition: attachment when found.

    Raises
    ------
    HTTPException 404
        When alert_id is absent from both alert tables.
    HTTPException 500
        On unexpected repository errors.

    #SG-TRACE: REQ-AUDIT-000
    #   | assumption: audit export is low-frequency (on-demand); no
    #     rate-limit required at Phase 3 scale
    #   | test: test_audit_endpoint_200, test_audit_endpoint_404
    #SG-TRACE: REQ-AUDIT-001
    #   | assumption: SEISMOGRAPH_EXPORT_TOKEN is a shared secret for
    #     Phase 3; Phase 4 replaces with per-fleet OAuth scoped tokens
    #   | test: test_audit_export_no_auth_401, test_audit_export_wrong_token_401,
    #           test_audit_export_token_not_configured_503
    """
    # ------------------------------------------------------------------
    # Auth: Bearer token from SEISMOGRAPH_EXPORT_TOKEN env var.
    # Unset token → 503 (endpoint administratively disabled).
    # Missing or wrong Bearer → 401.
    # ------------------------------------------------------------------
    export_token: str | None = os.getenv(_EXPORT_TOKEN_ENV_VAR)
    if not export_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "Audit export is disabled: "
                "SEISMOGRAPH_EXPORT_TOKEN is not configured."
            ),
        )
    auth_header: str = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header required: Bearer <export-token>",
        )
    provided: str = auth_header[len("Bearer "):]
    if not secrets.compare_digest(provided, export_token):
        raise HTTPException(status_code=401, detail="Invalid export token")

    repo: BaseRepository = request.app.state.repo
    generator = AuditReportGenerator(repo)
    try:
        report = generator.generate(alert_id)
    except AlertNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Alert {alert_id} not found in local or public tables.",
        )

    filename = f"seismograph_audit_{alert_id}.json"
    return JSONResponse(
        content=report,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
