"""
seismograph.engine.models
==========================
SQLAlchemy ORM models for SEISMOGRAPH persistence.

Four tables:

TelemetrySignal (table: telemetry_signals)
    One row per accepted InboundSignalBatch.  Stores distributional
    metric values only -- no raw prompt text or model output.
    fleet_id is nullable: NULL for public-network probes; non-NULL
    for private fleet probes.

LocalDriftAlert (table: local_drift_alerts)
    One row per CUSUMDetector alert, attributed to the submitting
    client_id.  Private -- never exposed via the public /v1/weather API.
    fleet_id is nullable: NULL for public-path alerts; non-NULL for
    private-fleet alerts.

PublicDriftAlert (table: public_drift_alerts)
    One row per quorum-verified drift event (>= QUORUM_MIN distinct
    orgs agree).  Anonymous -- no client_id or fleet_id stored (the
    only table read by GET /v1/weather).

WebhookConfig (table: webhook_configs)
    One row per registered enterprise fleet webhook.  Maps a fleet_id
    to a target URL and optional auth token.  Written by
    POST /v1/webhooks; read by the gateway on every private-fleet alert
    to determine whether a dispatch is required.
    fleet_id is UNIQUE -- only one active webhook per fleet at a time.

Privacy invariant (Aegis):
    No raw_output or raw_prompt column may ever appear in any table.
    The schema physically cannot store raw text.

#SG-TRACE: REQ-STORE-001
#   | assumption: SQLite for Phase 1/3 MVP; ClickHouse migration for
#     Phase 2 high-volume time-series data
#   | test: test_telemetry_signal_orm_columns
#SG-TRACE: REQ-STORE-002
#   | assumption: fleet_id is nullable String(128); gateway routing
#     logic uses `is not None` guard (not falsy check)
#   | test: test_fleet_id_column_nullable
#SG-TRACE: REQ-ENT-002
#   | assumption: WebhookConfig uses fleet_id as unique key; registering
#     a new URL for the same fleet replaces the prior config atomically
#   | test: test_register_webhook_upsert
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all SEISMOGRAPH ORM models."""


class TelemetrySignal(Base):
    """One persisted canary probe signal batch (distributional only).

    Columns
    -------
    id:          Auto-increment PK.
    batch_id:    UUID from InboundSignalBatch (indexed).
    timestamp:   Naive UTC insertion time.
    model_tuple: Model identifier string (indexed).
    avg_output_length: DP-noised average output token count (nullable).
    json_success_rate: DP-noised JSON validity rate (nullable).
    result_count: Number of canary prompts in this batch.
    fleet_id:    Optional tenant identifier for private fleet isolation.
                 NULL for public-network probes.

    #SG-TRACE: REQ-STORE-003
    #   | assumption: batch_id index supports duplicate-detection for
    #     Sybil resistance (Phase 2); not enforced as UNIQUE in Phase 1
    #   | test: test_save_batch_persists_to_db
    """

    __tablename__ = "telemetry_signals"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    batch_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    model_tuple: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    avg_output_length: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    json_success_rate: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    result_count: Mapped[float] = mapped_column(Float, nullable=False)
    fleet_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class LocalDriftAlert(Base):
    """One private CUSUMDetector alert, attributed to a client_id.

    Private fleet data.  Never exposed via GET /v1/weather or any
    public endpoint.

    Columns
    -------
    id:          Auto-increment PK.
    timestamp:   Naive UTC insertion time.
    model_tuple: Model identifier string (indexed).
    metric_name: Name of the drifting metric (e.g. "json_success_rate").
    alert_value: CUSUM score at alert time.
    client_id:   Pseudonymous probe session UUID (indexed).
    fleet_id:    Optional tenant identifier for private fleet isolation.
                 NULL for public-path alerts.

    #SG-TRACE: REQ-STORE-009
    #   | assumption: fleet_id is included so that enterprise operators
    #     can query their own drift history without cross-fleet exposure
    #   | test: test_save_local_alert_persists_fleet_id
    """

    __tablename__ = "local_drift_alerts"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    model_tuple: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_value: Mapped[float] = mapped_column(Float, nullable=False)
    client_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    fleet_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PublicDriftAlert(Base):
    """One quorum-verified public drift event.

    Written only when AgreementScorer.promote_to_public_alert() confirms
    >= QUORUM_MIN distinct orgs agree.  Anonymous (no client_id or
    fleet_id).  Read exclusively by GET /v1/weather.

    Columns
    -------
    id:                    Auto-increment PK.
    timestamp:             Naive UTC insertion time.
    model_tuple:           Model identifier string (indexed).
    metric_name:           Metric that completed the quorum.
    contributing_org_count: Number of distinct orgs that agreed.

    #SG-TRACE: REQ-STORE-013
    #   | assumption: no client_id or fleet_id here -- privacy by
    #     construction; quorum-level data is the only public surface
    #   | test: test_quorum_reached_triggers_dashboard
    """

    __tablename__ = "public_drift_alerts"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    model_tuple: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    contributing_org_count: Mapped[int] = mapped_column(
        Integer, nullable=False
    )


class WebhookConfig(Base):
    """Enterprise fleet webhook registration.

    One row per registered fleet.  fleet_id is the unique key -- the
    most recently registered URL for a fleet replaces the prior entry.
    The gateway reads this row on every private-fleet alert to decide
    whether to dispatch an HTTP notification.

    Columns
    -------
    id:         Auto-increment PK.
    fleet_id:   Unique tenant identifier (same value as in
                InboundSignalBatch.fleet_id and LocalDriftAlert.fleet_id).
    target_url: HTTP(S) endpoint to POST drift notifications to.
    auth_token: Optional Bearer token injected as
                Authorization: Bearer <token>.  Stored in plaintext for
                Phase 3 MVP; Phase 4 will encrypt at rest.

    Security note (Aegis):
        auth_token is operator-supplied secret material.  Never log its
        value.  Never include it in any API response body.

    #SG-TRACE: REQ-ENT-002
    #   | assumption: fleet_id uniqueness is enforced at the DB level
    #     (UniqueConstraint) AND at the application level (upsert in
    #     register_webhook); both guards are required because the DB
    #     constraint is the last line of defence
    #   | test: test_register_webhook_upsert
    """

    __tablename__ = "webhook_configs"
    __table_args__ = (UniqueConstraint("fleet_id", name="uq_webhook_fleet"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    fleet_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    target_url: Mapped[str] = mapped_column(String(512), nullable=False)
    auth_token: Mapped[str | None] = mapped_column(String(256), nullable=True)
