"""
seismograph.engine.repository
==============================
Repository layer between the gateway and the persistence backends.

Provides:
  - SignalRow / AlertRow: backend-agnostic return types for
    get_recent_signals and get_recent_alerts.  Both SQLite and ClickHouse
    implementations return objects with these attribute names so that
    gateway code is backend-neutral.
  - BaseRepository: ABC defining the eight-method interface all backends
    must implement.
  - DatabaseSession: SQLAlchemy engine factory (SQLite-specific).
  - SignalRepository(BaseRepository): SQLite/SQLAlchemy implementation.

Alert table separation
----------------------
LocalDriftAlert  (table: local_drift_alerts)
    Written by save_local_alert() for EVERY CUSUMDetector alert,
    attributed to the submitting client_id and optional fleet_id.
    Private -- never exposed via public API.

PublicDriftAlert (table: public_drift_alerts)
    Written by save_public_alert() ONLY when AgreementScorer confirms
    that >= QUORUM_MIN distinct orgs agree.  get_recent_alerts() reads
    from this table exclusively to compute the weather endpoint status.

Webhook storage
---------------
WebhookConfig (table: webhook_configs) -- SQLite only.
    register_webhook(): upsert one webhook registration per fleet.
    get_webhook(): retrieve config for a fleet (None if not registered).
    ClickHouseRepository returns None from get_webhook and raises
    NotImplementedError from register_webhook (config state is
    relational, not time-series; ClickHouse is not appropriate here).

Database URL configuration
---------------------------
The database URL is passed at construction time.  Production default:

    sqlite:///data/seismograph.db  (relative to working directory)

For tests, pass "sqlite:///:memory:" directly or set the env var
SEISMOGRAPH_DB_URL (consumed by gateway/main.py lifespan).

StaticPool
----------
For in-memory SQLite ("sqlite:///:memory:"), SQLAlchemy normally creates
a new database for each connection.  We use StaticPool to pin all
sessions to a single connection, so in-memory databases are shared
across the entire test run.

Sync SQLAlchemy in async gateway
---------------------------------
Phase 1 uses synchronous SQLAlchemy sessions inside the async FastAPI
endpoint.  This is acceptable for a single-node MVP with SQLite and low
probe throughput.  Phase 2 will migrate to async SQLAlchemy (or
aiosqlite / asyncpg) when real concurrency demands arise.

#SG-TRACE: REQ-STORE-004
#   | assumption: SQLite WAL mode not required for Phase 1 single-writer
#     single-reader usage; Phase 2 enables WAL for ClickHouse migration
#   | test: test_save_batch_persists_to_db
#SG-TRACE: REQ-STORE-005
#   | assumption: expire_on_commit=False is safe for read-after-write in
#     tests because all writes are committed before the read call
#   | test: test_get_recent_signals_filters_by_model_tuple
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from gateway.schema import InboundSignalBatch
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from engine.detector import DriftAlert as DetectorDriftAlert
from engine.models import (
    Base,
    LocalDriftAlert,
    PublicDriftAlert,
    TelemetrySignal,
    WebhookConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_URL: str = "sqlite:///data/seismograph.db"


# ---------------------------------------------------------------------------
# Backend-agnostic return types
# ---------------------------------------------------------------------------


@dataclass
class SignalRow:
    """Backend-agnostic telemetry signal record.

    Returned by BaseRepository.get_recent_signals().
    Attribute names mirror TelemetrySignal ORM columns so that
    gateway code (bootstrap_detector) works with both SQLite and
    ClickHouse backends via duck typing.

    #SG-TRACE: REQ-STORE-015
    #   | assumption: column set is stable across Phase 1; Phase 2 may
    #     add latency_p99 or error_rate columns under a new schema
    #     version
    #   | test: test_ch_get_recent_signals_returns_signal_rows
    """

    batch_id: str
    model_tuple: str
    timestamp: datetime
    avg_output_length: float | None
    json_success_rate: float | None
    result_count: float


@dataclass
class AlertRow:
    """Backend-agnostic public drift alert record.

    Returned by BaseRepository.get_recent_alerts().
    Attribute names mirror PublicDriftAlert ORM columns.

    #SG-TRACE: REQ-STORE-016
    #   | assumption: .timestamp is always naive UTC (stored without
    #     tzinfo) for consistent comparison in _compute_model_weather
    #   | test: test_ch_get_recent_alerts_returns_alert_rows
    """

    timestamp: datetime
    model_tuple: str
    metric_name: str
    contributing_org_count: int


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseRepository(ABC):
    """Interface contract for all SEISMOGRAPH persistence backends.

    All eight methods must be implemented by concrete subclasses.
    The gateway uses this interface exclusively so that swapping
    storage backends (SQLite <-> ClickHouse) requires only changing
    the concrete class instantiated in the lifespan context manager.

    Return types for get_recent_signals and get_recent_alerts are
    typed as list (unparameterised) because the SQLite implementation
    returns SQLAlchemy ORM objects while the ClickHouse implementation
    returns SignalRow / AlertRow dataclasses.  Both sets of objects
    expose the same attribute names, enabling duck-typed access in
    gateway code (bootstrap_detector, _compute_model_weather).

    register_webhook / get_webhook are only fully implemented by the
    SQLite backend.  ClickHouseRepository stubs raise NotImplementedError
    / return None respectively (webhook config is relational state not
    suited to ClickHouse MergeTree storage).

    #SG-TRACE: REQ-STORE-014
    #   | assumption: BaseRepository is the sole gateway-facing
    #     interface; no gateway code imports SignalRepository or
    #     ClickHouseRepository directly after Phase 2 migration
    #   | test: test_signal_repository_implements_base_repository
    """

    @abstractmethod
    def save_batch(self, batch: InboundSignalBatch) -> None:
        """Persist one accepted InboundSignalBatch."""

    @abstractmethod
    def save_local_alert(
        self,
        alert: DetectorDriftAlert,
        client_id: str,
        fleet_id: str | None = None,
    ) -> None:
        """Persist one local CUSUMDetector alert.

        Parameters
        ----------
        alert:
            DriftAlert from CUSUMDetector.update().
        client_id:
            Pseudonymous probe session UUID.
        fleet_id:
            Optional tenant identifier.  None for public-path alerts.
        """

    @abstractmethod
    def save_public_alert(
        self,
        model_tuple: str,
        metric_name: str,
        contributing_org_count: int,
    ) -> None:
        """Persist one quorum-verified public drift event."""

    @abstractmethod
    def get_recent_signals(
        self,
        model_tuple: str,
        limit: int = 100,
    ) -> list:
        """Return most recent signal records for model_tuple."""

    @abstractmethod
    def get_all_model_tuples(self) -> list[str]:
        """Return sorted distinct model_tuple strings."""

    @abstractmethod
    def get_recent_alerts(
        self,
        model_tuple: str,
        hours_back: int = 24,
    ) -> list:
        """Return public drift alerts for model_tuple in time window."""

    @abstractmethod
    def register_webhook(
        self,
        fleet_id: str,
        target_url: str,
        auth_token: str | None = None,
    ) -> None:
        """Upsert a webhook registration for fleet_id.

        Replaces any existing entry for this fleet_id atomically.
        Callers must ensure fleet_id is non-empty and target_url is a
        reachable HTTPS endpoint (validated upstream by the Pydantic
        WebhookRegistration schema).

        Parameters
        ----------
        fleet_id:
            Unique tenant identifier.  Must be non-empty.
        target_url:
            HTTP(S) endpoint to POST drift notifications to.
        auth_token:
            Optional Bearer token.  None means no Authorization header.
            Never logged.

        #SG-TRACE: REQ-ENT-002
        #   | assumption: upsert is atomic (DELETE + INSERT in one
        #     session commit); concurrent registrations for the same
        #     fleet will serialize via SQLite's write lock
        #   | test: test_register_webhook_upsert
        """

    @abstractmethod
    def get_webhook(
        self,
        fleet_id: str,
    ) -> WebhookConfig | None:
        """Return the WebhookConfig for fleet_id, or None if absent.

        Called on every private-fleet alert to determine whether a
        dispatch is required.  Must be read-only and fast (no network
        calls, no side effects).

        Parameters
        ----------
        fleet_id:
            Tenant identifier to look up.

        Returns
        -------
        WebhookConfig | None
            The registered config, or None if no webhook is registered
            for this fleet.

        #SG-TRACE: REQ-ENT-002
        #   | assumption: None return is safe -- gateway skips dispatch
        #     when get_webhook returns None; no error is raised
        #   | test: test_get_webhook_returns_none_when_absent
        """

    @abstractmethod
    def get_local_alert_by_id(
        self,
        alert_id: int,
    ) -> LocalDriftAlert | None:
        """Return the LocalDriftAlert with alert_id, or None.

        Used by AuditReportGenerator to resolve an alert before
        fetching the preceding telemetry evidence window.

        Parameters
        ----------
        alert_id:
            Primary key of the target row in local_drift_alerts.

        Returns
        -------
        LocalDriftAlert | None
            The matching ORM object, or None if not found.

        #SG-TRACE: REQ-AUDIT-001
        #   | assumption: integer PK lookup is O(1) via SQLite rowid
        #     index; no performance concern at Phase 3 scale
        #   | test: test_audit_export_local_alert
        """

    @abstractmethod
    def get_public_alert_by_id(
        self,
        alert_id: int,
    ) -> PublicDriftAlert | None:
        """Return the PublicDriftAlert with alert_id, or None.

        Falls back to this method when get_local_alert_by_id returns
        None, so the audit endpoint resolves both alert types from one
        id namespace.

        Parameters
        ----------
        alert_id:
            Primary key of the target row in public_drift_alerts.

        Returns
        -------
        PublicDriftAlert | None
            The matching ORM object, or None if not found.

        #SG-TRACE: REQ-AUDIT-001
        #   | assumption: public and local alert ids are independent
        #     sequences; caller probes both in order
        #   | test: test_audit_export_public_alert
        """

    @abstractmethod
    def get_signals_before_timestamp(
        self,
        model_tuple: str,
        timestamp: datetime,
        fleet_id: str | None = None,
        limit: int = 50,
    ) -> list:
        """Return up to limit signals preceding timestamp for model_tuple.

        Used by AuditReportGenerator to build baseline_evidence: the
        50 telemetry samples immediately before the alert fired.

        Parameters
        ----------
        model_tuple:
            Filter by this model identifier.
        timestamp:
            Exclusive upper bound.  Only rows with timestamp < this
            value are returned.
        fleet_id:
            If non-None, additionally filter by fleet_id.  None returns
            signals from all probes for that model_tuple.
        limit:
            Maximum rows to return.  Default 50 (SOC 2 evidence window).

        Returns
        -------
        list
            Most recent matching signals first.  Empty list if none.

        #SG-TRACE: REQ-AUDIT-002
        #   | assumption: timestamp column has no index in Phase 3 SQLite;
        #     acceptable for audit-on-demand workload (not hot path)
        #   | test: test_audit_baseline_evidence_count
        """


# ---------------------------------------------------------------------------
# SQLite / SQLAlchemy implementation
# ---------------------------------------------------------------------------


class DatabaseSession:
    """SQLAlchemy engine factory with table auto-creation.

    Handles two URL patterns:
    - "sqlite:///:memory:" -- uses StaticPool so all sessions share one
      connection and therefore one in-memory database.  Designed for
      testing.
    - Any file-backed URL -- uses the default NullPool / QueuePool and
      creates the parent directory automatically if it does not exist.

    Parameters
    ----------
    db_url:
        SQLAlchemy database URL.  Defaults to DEFAULT_DB_URL.

    #SG-TRACE: REQ-STORE-006
    #   | assumption: StaticPool + check_same_thread=False is the
    #     correct pattern for shared in-memory SQLite in pytest fixtures
    #   | test: test_save_batch_persists_to_db (uses :memory:)
    """

    def __init__(self, db_url: str = DEFAULT_DB_URL) -> None:
        self.db_url = db_url

        if ":memory:" in db_url:
            self._engine = create_engine(
                db_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            # Ensure parent directory exists for file-backed SQLite
            db_file = db_url.replace("sqlite:///", "")
            if db_file and os.path.dirname(db_file):
                os.makedirs(os.path.dirname(db_file), exist_ok=True)
            self._engine = create_engine(
                db_url,
                connect_args={"check_same_thread": False},
            )

        Base.metadata.create_all(self._engine)
        logger.info("DatabaseSession initialised | url=%s", db_url)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Yield a SQLAlchemy Session, committing on exit.

        Uses expire_on_commit=False so that ORM objects returned from
        within the context remain accessible after the session closes.
        This is required for get_recent_signals() to return usable
        TelemetrySignal objects after the context manager exits.
        """
        with Session(self._engine, expire_on_commit=False) as sess:
            yield sess
            sess.commit()


class SignalRepository(BaseRepository):
    """Data-access layer for telemetry signals and drift alerts (SQLite).

    Wraps a DatabaseSession and exposes eight operations:
      - save_batch: persist an InboundSignalBatch as a TelemetrySignal.
      - save_local_alert: persist a local (per-org) CUSUMDetector alert.
      - save_public_alert: persist a quorum-verified public drift event.
      - get_recent_signals: retrieve the latest N TelemetrySignals for
        a model_tuple (used to bootstrap CUSUMDetector on restart).
      - get_all_model_tuples: list distinct model_tuples in DB.
      - get_recent_alerts: fetch PublicDriftAlerts within a time window.
      - register_webhook: upsert a fleet webhook registration.
      - get_webhook: retrieve webhook config for a fleet.

    Return types from get_recent_signals and get_recent_alerts are
    SQLAlchemy ORM objects (TelemetrySignal, PublicDriftAlert).  These
    expose the same attribute names as SignalRow / AlertRow so that
    gateway code works via duck typing with both SQLite and ClickHouse.

    Parameters
    ----------
    db_url:
        SQLAlchemy database URL passed to DatabaseSession.

    #SG-TRACE: REQ-STORE-007
    #   | assumption: save_batch is called BEFORE CUSUMDetector.update()
    #     in the gateway endpoint so that a DB failure surfaces as a 500
    #     before any in-memory state is modified
    #   | test: test_gateway_save_batch_called_before_cusum (Phase 2)
    """

    def __init__(self, db_url: str = DEFAULT_DB_URL) -> None:
        self._db = DatabaseSession(db_url)

    def save_batch(self, batch: InboundSignalBatch) -> None:
        """Persist one accepted InboundSignalBatch as a TelemetrySignal.

        Extracts allowed metric keys from batch.metrics.  Missing metric
        keys are stored as NULL (nullable columns on TelemetrySignal).
        fleet_id is read from batch.fleet_id and stored on the row.

        Parameters
        ----------
        batch:
            Validated InboundSignalBatch from the gateway endpoint.

        #SG-TRACE: REQ-STORE-008
        #   | assumption: batch.metrics keys are validated upstream by
        #     InboundSignalBatch.check_metrics_keys; no unknown keys here
        #   | test: test_save_batch_persists_to_db
        """
        metrics = batch.metrics
        signal = TelemetrySignal(
            batch_id=str(batch.batch_id),
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            model_tuple=batch.model_tuple,
            avg_output_length=metrics.get("avg_output_length"),
            json_success_rate=metrics.get("json_success_rate"),
            result_count=float(
                metrics.get("result_count", batch.result_count)
            ),
            fleet_id=batch.fleet_id,
        )
        with self._db.session() as sess:
            sess.add(signal)
        logger.debug(
            "save_batch | batch_id=%s model=%s fleet_id=%s",
            batch.batch_id,
            batch.model_tuple,
            batch.fleet_id,
        )

    def save_local_alert(
        self,
        alert: DetectorDriftAlert,
        client_id: str,
        fleet_id: str | None = None,
    ) -> None:
        """Persist one local CUSUMDetector alert attributed to client_id.

        Writes to local_drift_alerts (private fleet data).  This record
        is NEVER exposed via the public weather API.

        Parameters
        ----------
        alert:
            DriftAlert dataclass emitted by CUSUMDetector.update().
            alert.cusum_score is stored as alert_value.
        client_id:
            Pseudonymous client identifier from the InboundSignalBatch.
            Used internally for Sybil resistance and audit logging.
        fleet_id:
            Optional tenant identifier.  None for public-path alerts;
            non-None for private-fleet alerts.

        #SG-TRACE: REQ-STORE-009
        #   | assumption: all alerts reaching this method have already
        #     passed the gateway 202 path; partial-ingest failures do
        #     not roll back the associated TelemetrySignal row
        #   | test: test_save_alert_persists_to_db
        """
        db_alert = LocalDriftAlert(
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            model_tuple=alert.model_tuple,
            metric_name=alert.metric_name,
            alert_value=alert.cusum_score,
            client_id=client_id,
            fleet_id=fleet_id,
        )
        with self._db.session() as sess:
            sess.add(db_alert)
        logger.debug(
            "save_local_alert | model=%s metric=%s client=%s"
            " fleet=%s score=%.4f",
            alert.model_tuple,
            alert.metric_name,
            client_id,
            fleet_id,
            alert.cusum_score,
        )

    def save_public_alert(
        self,
        model_tuple: str,
        metric_name: str,
        contributing_org_count: int,
    ) -> None:
        """Persist one quorum-verified public drift event.

        Writes to public_drift_alerts.  This is the table that GET
        /v1/weather reads to determine STABLE vs DRIFTING status.

        Must only be called after AgreementScorer.promote_to_public_alert()
        confirms that >= QUORUM_MIN distinct orgs agree.  Callers are
        responsible for enforcing this invariant.

        Parameters
        ----------
        model_tuple:
            Model identifier for the drifting stream.
        metric_name:
            Metric that triggered the quorum-completing alert.
        contributing_org_count:
            Number of distinct orgs whose signals agreed.  Must be
            >= QUORUM_MIN (currently 2).

        #SG-TRACE: REQ-STORE-013
        #   | assumption: contributing_org_count >= QUORUM_MIN enforced
        #     by AgreementScorer.promote_to_public_alert() before call
        #   | test: test_quorum_reached_triggers_dashboard
        """
        db_alert = PublicDriftAlert(
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            model_tuple=model_tuple,
            metric_name=metric_name,
            contributing_org_count=contributing_org_count,
        )
        with self._db.session() as sess:
            sess.add(db_alert)
        logger.warning(
            "save_public_alert | model=%s metric=%s orgs=%d",
            model_tuple,
            metric_name,
            contributing_org_count,
        )

    def get_recent_signals(
        self,
        model_tuple: str,
        limit: int = 100,
    ) -> list[TelemetrySignal]:
        """Return the most recent TelemetrySignal rows for a model_tuple.

        Ordered descending by insertion id (i.e., most recent first).
        Used to bootstrap the CUSUMDetector with historical metric
        values on gateway startup, preventing the baseline phase from
        repeating unnecessarily after a restart.

        Parameters
        ----------
        model_tuple:
            Filter by this model identifier.
        limit:
            Maximum number of rows to return.  Default 100.

        Returns
        -------
        list[TelemetrySignal]
            Most recent rows first.  Empty list if no data exists.

        #SG-TRACE: REQ-STORE-010
        #   | assumption: id ordering is sufficient as a recency proxy
        #     for Phase 1; Phase 2 orders by timestamp column
        #   | test: test_get_recent_signals_respects_limit
        """
        stmt = (
            select(TelemetrySignal)
            .where(TelemetrySignal.model_tuple == model_tuple)
            .order_by(TelemetrySignal.id.desc())
            .limit(limit)
        )
        with self._db.session() as sess:
            return list(sess.scalars(stmt).all())

    def get_all_model_tuples(self) -> list[str]:
        """Return distinct model_tuple values from telemetry_signals.

        Used by the gateway lifespan to enumerate streams for
        CUSUMDetector bootstrapping on restart, and by the weather
        endpoint to iterate all known models.

        Returns
        -------
        list[str]
            Sorted list of known model_tuple strings.
            Empty list if the table has no rows.

        #SG-TRACE: REQ-STORE-011
        #   | assumption: DISTINCT query is cheap for Phase 1 cardinality
        #     (target < 20 model tuples in Phase 1 top-10 dashboard)
        #   | test: test_bootstrap_warms_cusum_detector
        """
        stmt = select(TelemetrySignal.model_tuple).distinct()
        with self._db.session() as sess:
            return sorted(sess.scalars(stmt).all())

    def get_recent_alerts(
        self,
        model_tuple: str,
        hours_back: int = 24,
    ) -> list[PublicDriftAlert]:
        """Return PublicDriftAlert rows for model_tuple in time window.

        Queries public_drift_alerts ONLY -- local_drift_alerts are
        private and not considered for the weather endpoint status.

        Status is DRIFTING if any PublicDriftAlert (quorum-verified)
        was recorded in the last 24h.  Results are ordered most recent
        first.

        Timestamp comparison uses naive UTC datetimes throughout --
        save_public_alert() stores naive UTC, and the cutoff is computed
        as naive UTC, so the >= comparison is consistent.

        Parameters
        ----------
        model_tuple:
            Filter alerts by this model identifier.
        hours_back:
            Look-back window in hours.  Default 24.

        Returns
        -------
        list[PublicDriftAlert]
            Most recent public alerts first.  Empty list if none.

        #SG-TRACE: REQ-STORE-012
        #   | assumption: naive UTC cutoff is valid because
        #     save_public_alert stores
        #     datetime.now(timezone.utc).replace(tzinfo=None)
        #   | test: test_weather_returns_drifting_when_recent_alert
        """
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=hours_back
        )
        stmt = (
            select(PublicDriftAlert)
            .where(PublicDriftAlert.model_tuple == model_tuple)
            .where(PublicDriftAlert.timestamp >= cutoff)
            .order_by(PublicDriftAlert.timestamp.desc())
        )
        with self._db.session() as sess:
            return list(sess.scalars(stmt).all())

    def register_webhook(
        self,
        fleet_id: str,
        target_url: str,
        auth_token: str | None = None,
    ) -> None:
        """Upsert a webhook registration for fleet_id.

        Atomically replaces any existing entry for this fleet_id:
        delete-then-insert within a single session commit.  Safe under
        SQLite's write serialization.

        Parameters
        ----------
        fleet_id:
            Unique tenant identifier.  Must be non-empty.
        target_url:
            HTTP(S) endpoint to POST drift notifications to.
        auth_token:
            Optional Bearer token.  Never logged.

        #SG-TRACE: REQ-ENT-002
        #   | assumption: delete + insert is atomic within one session;
        #     no window exists for a concurrent reader to see a missing
        #     entry between the two operations (SQLite serializes writes)
        #   | test: test_register_webhook_upsert
        """
        with self._db.session() as sess:
            sess.execute(
                delete(WebhookConfig).where(WebhookConfig.fleet_id == fleet_id)
            )
            sess.add(
                WebhookConfig(
                    fleet_id=fleet_id,
                    target_url=target_url,
                    auth_token=auth_token,
                )
            )
        logger.info(
            "register_webhook | fleet=%s url=%s has_token=%s",
            fleet_id,
            target_url,
            auth_token is not None,
        )

    def get_webhook(self, fleet_id: str) -> WebhookConfig | None:
        """Return the WebhookConfig for fleet_id, or None.

        Returns
        -------
        WebhookConfig | None
            The registered config, or None if no webhook is registered.

        #SG-TRACE: REQ-ENT-002
        #   | assumption: None return is safe; gateway skips dispatch
        #   | test: test_get_webhook_returns_none_when_absent
        """
        stmt = select(WebhookConfig).where(WebhookConfig.fleet_id == fleet_id)
        with self._db.session() as sess:
            return sess.scalars(stmt).first()

    def get_local_alert_by_id(
        self,
        alert_id: int,
    ) -> LocalDriftAlert | None:
        """Return the LocalDriftAlert with alert_id, or None.

        #SG-TRACE: REQ-AUDIT-001
        #   | assumption: integer PK lookup via SQLite rowid index
        #   | test: test_audit_export_local_alert
        """
        stmt = select(LocalDriftAlert).where(LocalDriftAlert.id == alert_id)
        with self._db.session() as sess:
            return sess.scalars(stmt).first()

    def get_public_alert_by_id(
        self,
        alert_id: int,
    ) -> PublicDriftAlert | None:
        """Return the PublicDriftAlert with alert_id, or None.

        #SG-TRACE: REQ-AUDIT-001
        #   | assumption: integer PK lookup via SQLite rowid index
        #   | test: test_audit_export_public_alert
        """
        stmt = select(PublicDriftAlert).where(PublicDriftAlert.id == alert_id)
        with self._db.session() as sess:
            return sess.scalars(stmt).first()

    def get_signals_before_timestamp(
        self,
        model_tuple: str,
        timestamp: datetime,
        fleet_id: str | None = None,
        limit: int = 50,
    ) -> list[TelemetrySignal]:
        """Return up to limit TelemetrySignal rows before timestamp.

        Ordered descending by timestamp so most-recent evidence appears
        first.  fleet_id filter is applied when non-None (private fleet
        audit evidence window).

        #SG-TRACE: REQ-AUDIT-002
        #   | assumption: timestamp < bound (exclusive) matches the alert
        #     fire time; signals AT the alert timestamp are excluded to
        #     avoid partial-batch contamination
        #   | test: test_audit_baseline_evidence_count
        """
        stmt = (
            select(TelemetrySignal)
            .where(TelemetrySignal.model_tuple == model_tuple)
            .where(TelemetrySignal.timestamp < timestamp)
        )
        if fleet_id is not None:
            stmt = stmt.where(TelemetrySignal.fleet_id == fleet_id)
        stmt = stmt.order_by(TelemetrySignal.timestamp.desc()).limit(limit)
        with self._db.session() as sess:
            return list(sess.scalars(stmt).all())
