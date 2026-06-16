"""
seismograph.engine.clickhouse
==============================
ClickHouse persistence backend for SEISMOGRAPH (Phase 2).

Implements BaseRepository using clickhouse-connect raw SQL INSERT/SELECT
queries.  Designed for high-volume time-series probe data that would
overwhelm SQLite under real multi-org federation traffic.

Architectural position
----------------------
This module is instantiated by gateway/main.py ONLY when the env var
STORAGE_BACKEND=clickhouse is set.  All other code paths use the SQLite
SignalRepository.  The gateway code uses BaseRepository typing throughout
and is therefore backend-neutral.

Privacy invariants
------------------
No raw prompt text or model output is accepted or stored here.  The
telemetry_signals table stores only distributional feature hashes and
DP-noised aggregate metrics -- identical privacy guarantees to the SQLite
path.  Aegis audit: verify on every PR that no raw_output or raw_prompt
column is added to any table definition in setup_tables().

Table schema (all Engine = MergeTree)
--------------------------------------
telemetry_signals
    id                UUID DEFAULT generateUUIDv4()
    batch_id          String
    timestamp         DateTime
    model_tuple       String
    avg_output_length Nullable(Float64)
    json_success_rate Nullable(Float64)
    result_count      Float64
    fleet_id          Nullable(String)
    ORDER BY (model_tuple, timestamp)

local_drift_alerts
    id          UUID DEFAULT generateUUIDv4()
    timestamp   DateTime
    model_tuple String
    metric_name String
    alert_value Float64
    client_id   String
    fleet_id    Nullable(String)
    ORDER BY (model_tuple, timestamp)

public_drift_alerts
    id                     UUID DEFAULT generateUUIDv4()
    timestamp              DateTime
    model_tuple            String
    metric_name            String
    contributing_org_count UInt32
    ORDER BY (model_tuple, timestamp)

Webhook config (NOT stored in ClickHouse)
-----------------------------------------
WebhookConfig is relational configuration state, not time-series data.
ClickHouseRepository stubs register_webhook (raises NotImplementedError)
and get_webhook (returns None).  The enterprise webhook feature requires
the SQLite backend (STORAGE_BACKEND=sqlite, the default).

#SG-TRACE: REQ-STORE-014
#   | assumption: MergeTree with (model_tuple, timestamp) ORDER BY is
#     optimal for range scans in get_recent_signals and
#     get_recent_alerts; no secondary index required at Phase 2
#   | test: test_ch_setup_tables_creates_all_three_tables
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from gateway.schema import InboundSignalBatch

from engine.detector import DriftAlert as DetectorDriftAlert
from engine.models import WebhookConfig
from engine.repository import AlertRow, BaseRepository, SignalRow

logger = logging.getLogger(__name__)


class ClickHouseRepository(BaseRepository):
    """ClickHouse-backed persistence for SEISMOGRAPH signals and alerts.

    All DML uses clickhouse-connect raw SQL via client.insert() and
    client.query().  DDL is executed via client.command().  No ORM layer.

    Parameters
    ----------
    client:
        A connected clickhouse_connect.driver.Client instance.
        Injected at construction time for testability (mock-friendly).

    #SG-TRACE: REQ-STORE-014
    #   | assumption: client is already authenticated and connected when
    #     passed to __init__; connection lifetime managed by the caller
    #     (gateway lifespan or test fixture)
    #   | test: test_ch_save_batch_inserts_to_telemetry_signals
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def setup_tables(self) -> None:
        """Execute CREATE TABLE IF NOT EXISTS for all three tables.

        Safe to call on every gateway startup -- idempotent.
        Tables use MergeTree engine with (model_tuple, timestamp)
        ORDER BY for efficient time-range scans.

        Must be called once before any other method is used.

        Adversarial check: verify no raw_output or raw_prompt column is
        present in any CREATE TABLE statement.  Privacy-by-construction
        requires that the schema physically cannot store raw text.

        #SG-TRACE: REQ-STORE-014
        #   | assumption: MergeTree requires no explicit partition key
        #     at Phase 2 scale; Phase 3 adds
        #     PARTITION BY toYYYYMM(timestamp)
        #   | test: test_ch_setup_tables_creates_all_three_tables
        """
        self._client.command(
            "CREATE TABLE IF NOT EXISTS telemetry_signals ("
            " id UUID DEFAULT generateUUIDv4(),"
            " batch_id String,"
            " timestamp DateTime,"
            " model_tuple String,"
            " avg_output_length Nullable(Float64),"
            " json_success_rate Nullable(Float64),"
            " result_count Float64,"
            " fleet_id Nullable(String)"
            ") ENGINE = MergeTree()"
            " ORDER BY (model_tuple, timestamp)"
        )
        self._client.command(
            "CREATE TABLE IF NOT EXISTS local_drift_alerts ("
            " id UUID DEFAULT generateUUIDv4(),"
            " timestamp DateTime,"
            " model_tuple String,"
            " metric_name String,"
            " alert_value Float64,"
            " client_id String,"
            " fleet_id Nullable(String)"
            ") ENGINE = MergeTree()"
            " ORDER BY (model_tuple, timestamp)"
        )
        self._client.command(
            "CREATE TABLE IF NOT EXISTS public_drift_alerts ("
            " id UUID DEFAULT generateUUIDv4(),"
            " timestamp DateTime,"
            " model_tuple String,"
            " metric_name String,"
            " contributing_org_count UInt32"
            ") ENGINE = MergeTree()"
            " ORDER BY (model_tuple, timestamp)"
        )
        logger.info(
            "ClickHouseRepository.setup_tables: all three tables ensured"
        )

    def save_batch(self, batch: InboundSignalBatch) -> None:
        """Persist one InboundSignalBatch to telemetry_signals.

        Parameters
        ----------
        batch:
            Validated InboundSignalBatch from the gateway endpoint.
            Only distributional metric values are stored -- no raw text.
            fleet_id is read from batch.fleet_id.

        #SG-TRACE: REQ-STORE-008
        #   | assumption: batch.metrics keys are validated upstream
        #   | test: test_ch_save_batch_inserts_to_telemetry_signals
        """
        metrics = batch.metrics
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
        self._client.insert(
            "telemetry_signals",
            [
                [
                    str(batch.batch_id),
                    ts,
                    batch.model_tuple,
                    metrics.get("avg_output_length"),
                    metrics.get("json_success_rate"),
                    float(metrics.get("result_count", batch.result_count)),
                    batch.fleet_id,
                ]
            ],
            column_names=[
                "batch_id",
                "timestamp",
                "model_tuple",
                "avg_output_length",
                "json_success_rate",
                "result_count",
                "fleet_id",
            ],
        )
        logger.debug(
            "CH save_batch | batch_id=%s model=%s fleet_id=%s",
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
        """Persist one local CUSUMDetector alert to local_drift_alerts.

        Private fleet data -- never surfaced via the public weather API.

        Parameters
        ----------
        alert:
            DriftAlert dataclass from CUSUMDetector.update().
        client_id:
            Pseudonymous org identifier for Sybil audit trail.
        fleet_id:
            Optional tenant identifier.  None for public-path alerts.

        #SG-TRACE: REQ-STORE-009
        #   | assumption: cusum_score stored as alert_value (Float64)
        #     preserves sufficient precision for audit purposes
        #   | test: test_ch_save_local_alert_inserts_to_local_drift_alerts
        """
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
        self._client.insert(
            "local_drift_alerts",
            [
                [
                    ts,
                    alert.model_tuple,
                    alert.metric_name,
                    alert.cusum_score,
                    client_id,
                    fleet_id,
                ]
            ],
            column_names=[
                "timestamp",
                "model_tuple",
                "metric_name",
                "alert_value",
                "client_id",
                "fleet_id",
            ],
        )
        logger.debug(
            "CH save_local_alert | model=%s metric=%s fleet=%s score=%.4f",
            alert.model_tuple,
            alert.metric_name,
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

        Called by the gateway only when
        AgreementScorer.promote_to_public_alert() returns non-None
        org count (>= QUORUM_MIN).

        Parameters
        ----------
        model_tuple:
            Model identifier for the drifting stream.
        metric_name:
            Metric that completed the quorum.
        contributing_org_count:
            Distinct orgs that agreed; >= QUORUM_MIN guaranteed by
            caller.

        #SG-TRACE: REQ-STORE-013
        #   | assumption: quorum invariant enforced in gateway before
        #     call
        #   | test: test_ch_save_public_alert_inserts_to_public_alerts
        """
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
        self._client.insert(
            "public_drift_alerts",
            [
                [
                    ts,
                    model_tuple,
                    metric_name,
                    contributing_org_count,
                ]
            ],
            column_names=[
                "timestamp",
                "model_tuple",
                "metric_name",
                "contributing_org_count",
            ],
        )
        logger.warning(
            "CH save_public_alert | model=%s metric=%s orgs=%d",
            model_tuple,
            metric_name,
            contributing_org_count,
        )

    def get_recent_signals(
        self,
        model_tuple: str,
        limit: int = 100,
    ) -> list[SignalRow]:
        """Return most recent SignalRows for model_tuple (newest first).

        Used by gateway bootstrap_detector() to restore CUSUMDetector
        baseline state on gateway restart.

        Parameters
        ----------
        model_tuple:
            Filter by this model identifier.
        limit:
            Maximum rows to return.

        Returns
        -------
        list[SignalRow]
            Most recent rows first.  Empty list if no data exists.

        #SG-TRACE: REQ-STORE-010
        #   | assumption: ORDER BY timestamp DESC is efficient on
        #     MergeTree because timestamp is the second column in the
        #     sort key
        #   | test: test_ch_get_recent_signals_returns_signal_rows
        """
        result = self._client.query(
            "SELECT batch_id, model_tuple, timestamp,"
            " avg_output_length, json_success_rate, result_count"
            " FROM telemetry_signals"
            " WHERE model_tuple = {mt:String}"
            " ORDER BY timestamp DESC"
            " LIMIT {lim:Int32}",
            parameters={"mt": model_tuple, "lim": limit},
        )
        rows: list[SignalRow] = []
        for row in result.result_rows:
            rows.append(
                SignalRow(
                    batch_id=row[0],
                    model_tuple=row[1],
                    timestamp=row[2],
                    avg_output_length=row[3],
                    json_success_rate=row[4],
                    result_count=row[5],
                )
            )
        return rows

    def get_all_model_tuples(self) -> list[str]:
        """Return sorted distinct model_tuple strings.

        Returns
        -------
        list[str]
            Sorted model_tuple strings; empty if table is empty.

        #SG-TRACE: REQ-STORE-011
        #   | assumption: DISTINCT is cheap at Phase 2 model cardinality
        #     (target < 100 tuples); Phase 3 materialises a separate
        #     view
        #   | test: test_ch_get_all_model_tuples_returns_sorted_list
        """
        result = self._client.query(
            "SELECT DISTINCT model_tuple"
            " FROM telemetry_signals"
            " ORDER BY model_tuple"
        )
        return [row[0] for row in result.result_rows]

    def get_recent_alerts(
        self,
        model_tuple: str,
        hours_back: int = 24,
    ) -> list[AlertRow]:
        """Return public drift alerts for model_tuple in time window.

        Reads public_drift_alerts ONLY -- local_drift_alerts are
        private.

        Parameters
        ----------
        model_tuple:
            Filter alerts by this model identifier.
        hours_back:
            Look-back window in hours.  Default 24.

        Returns
        -------
        list[AlertRow]
            Most recent public alerts first.

        #SG-TRACE: REQ-STORE-012
        #   | assumption: DateTime comparison is accurate because both
        #     stored value and cutoff are naive UTC
        #   | test: test_ch_get_recent_alerts_returns_alert_rows
        """
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=hours_back
        )
        result = self._client.query(
            "SELECT timestamp, model_tuple, metric_name,"
            " contributing_org_count"
            " FROM public_drift_alerts"
            " WHERE model_tuple = {mt:String}"
            "  AND timestamp >= {cutoff:DateTime}"
            " ORDER BY timestamp DESC",
            parameters={"mt": model_tuple, "cutoff": cutoff},
        )
        rows: list[AlertRow] = []
        for row in result.result_rows:
            rows.append(
                AlertRow(
                    timestamp=row[0],
                    model_tuple=row[1],
                    metric_name=row[2],
                    contributing_org_count=row[3],
                )
            )
        return rows

    def register_webhook(
        self,
        fleet_id: str,
        target_url: str,
        auth_token: str | None = None,
    ) -> None:
        """Not supported on the ClickHouse backend.

        WebhookConfig is relational configuration state, not time-series
        data.  Use the SQLite backend (STORAGE_BACKEND=sqlite) for
        webhook registration.

        Raises
        ------
        NotImplementedError
            Always.  The gateway POST /v1/webhooks endpoint will surface
            this as a 500 if called against the ClickHouse backend.

        #SG-TRACE: REQ-ENT-002
        #   | assumption: ClickHouse backend is used for high-volume
        #     time-series data only; relational config belongs in SQLite
        #   | test: (not tested; raises immediately)
        """
        raise NotImplementedError(
            "register_webhook requires STORAGE_BACKEND=sqlite; "
            "ClickHouseRepository does not support webhook config storage."
        )

    def get_webhook(self, fleet_id: str) -> WebhookConfig | None:
        """Always returns None on the ClickHouse backend.

        No webhook config is stored in ClickHouse.  The gateway will
        skip dispatch (safe no-op) when this returns None.

        Returns
        -------
        WebhookConfig | None
            Always None.

        #SG-TRACE: REQ-ENT-002
        #   | assumption: None return causes gateway to skip dispatch;
        #     no alert or error is raised
        #   | test: test_ch_get_webhook_returns_none
        """
        return None

    def get_local_alert_by_id(self, alert_id: int) -> None:
        """Not supported on the ClickHouse backend.

        ClickHouse uses UUID primary keys for local_drift_alerts, not
        integer auto-increment.  Integer id lookups are not meaningful
        against MergeTree storage.  Use SQLite backend for audit export.

        Raises
        ------
        NotImplementedError
            Always.

        #SG-TRACE: REQ-AUDIT-001
        #   | assumption: audit export requires SQLite (STORAGE_BACKEND
        #     omitted or sqlite); ClickHouse path raises immediately
        #   | test: (not tested; raises immediately)
        """
        raise NotImplementedError(
            "get_local_alert_by_id requires STORAGE_BACKEND=sqlite; "
            "ClickHouseRepository uses UUID primary keys."
        )

    def get_public_alert_by_id(self, alert_id: int) -> None:
        """Not supported on the ClickHouse backend.

        Same constraint as get_local_alert_by_id: UUID PKs incompatible
        with integer id lookup.

        Raises
        ------
        NotImplementedError
            Always.

        #SG-TRACE: REQ-AUDIT-001
        #   | assumption: audit export requires SQLite backend
        #   | test: (not tested; raises immediately)
        """
        raise NotImplementedError(
            "get_public_alert_by_id requires STORAGE_BACKEND=sqlite; "
            "ClickHouseRepository uses UUID primary keys."
        )

    def get_signals_before_timestamp(
        self,
        model_tuple: str,
        timestamp: Any,
        fleet_id: str | None = None,
        limit: int = 50,
    ) -> list[SignalRow]:
        """Return up to limit SignalRows before timestamp (ClickHouse).

        Equivalent of the SQLite implementation but uses raw SQL against
        the MergeTree telemetry_signals table.

        Parameters
        ----------
        model_tuple:
            Filter by this model identifier.
        timestamp:
            Exclusive upper bound (naive UTC DateTime).
        fleet_id:
            If non-None, additionally filter by fleet_id.
        limit:
            Maximum rows to return.  Default 50.

        Returns
        -------
        list[SignalRow]
            Most recent matching signals first.

        #SG-TRACE: REQ-AUDIT-002
        #   | assumption: timestamp < bound is efficient on MergeTree
        #     because timestamp is part of the ORDER BY key
        #   | test: test_ch_get_signals_before_timestamp
        """
        if fleet_id is not None:
            result = self._client.query(
                "SELECT batch_id, model_tuple, timestamp,"
                " avg_output_length, json_success_rate, result_count"
                " FROM telemetry_signals"
                " WHERE model_tuple = {mt:String}"
                "  AND timestamp < {ts:DateTime}"
                "  AND fleet_id = {fid:String}"
                " ORDER BY timestamp DESC"
                " LIMIT {lim:Int32}",
                parameters={
                    "mt": model_tuple,
                    "ts": timestamp,
                    "fid": fleet_id,
                    "lim": limit,
                },
            )
        else:
            result = self._client.query(
                "SELECT batch_id, model_tuple, timestamp,"
                " avg_output_length, json_success_rate, result_count"
                " FROM telemetry_signals"
                " WHERE model_tuple = {mt:String}"
                "  AND timestamp < {ts:DateTime}"
                " ORDER BY timestamp DESC"
                " LIMIT {lim:Int32}",
                parameters={
                    "mt": model_tuple,
                    "ts": timestamp,
                    "lim": limit,
                },
            )
        rows: list[SignalRow] = []
        for row in result.result_rows:
            rows.append(
                SignalRow(
                    batch_id=row[0],
                    model_tuple=row[1],
                    timestamp=row[2],
                    avg_output_length=row[3],
                    json_success_rate=row[4],
                    result_count=row[5],
                )
            )
        return rows
