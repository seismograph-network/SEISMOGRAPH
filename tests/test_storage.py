"""
tests.test_storage
==================
Unit tests for the SEISMOGRAPH persistent storage layer.

Test contract -- SQLite (SignalRepository)
------------------------------------------
T1  test_save_batch_persists_to_db
    Save one batch, query back via get_recent_signals.
    Assert: row present, batch_id matches.

T2  test_save_batch_extracts_metrics_correctly
    Batch includes both avg_output_length and json_success_rate.
    Assert: both values stored correctly.

T3  test_save_batch_nullable_metrics_when_absent
    Batch metrics dict omits avg_output_length.
    Assert: stored as NULL (None in Python).

T4  test_save_alert_persists_to_db
    Save one DriftAlert via save_local_alert(), query back via direct
    ORM select on LocalDriftAlert.
    Assert: row present, model_tuple and metric_name match.

T5  test_save_alert_stores_cusum_score_as_alert_value
    Assert: alert_value == cusum_score from the DetectorDriftAlert.

T6  test_get_recent_signals_filters_by_model_tuple
    Save rows for two different model_tuples.
    Assert: get_recent_signals returns only the requested model.

T7  test_get_recent_signals_respects_limit
    Save 5 rows for the same model_tuple.
    Assert: get_recent_signals with limit=2 returns exactly 2 rows.

T8  test_get_recent_signals_empty_returns_empty_list
    Query a model_tuple with no data.
    Assert: returns [].

CU1 test_ch_setup_tables_creates_all_three_tables (ClickHouse -- mocked)
    setup_tables() calls client.command() exactly 3 times.
    Each CREATE TABLE SQL references the expected table name.

CU2 test_ch_save_batch_inserts_to_telemetry_signals (ClickHouse -- mocked)
    save_batch() calls client.insert("telemetry_signals", ...).
    Asserts column_names includes required columns and data row is a list.

CU3 test_ch_save_local_alert_inserts_to_local_drift_alerts
    save_local_alert() calls client.insert("local_drift_alerts", ...).
    Asserts client_id and alert_value appear in column_names and data.

CU4 test_ch_save_public_alert_inserts_to_public_drift_alerts
    save_public_alert() calls client.insert("public_drift_alerts", ...).
    Asserts contributing_org_count appears in column_names.

CU5 test_ch_get_recent_signals_returns_signal_rows
    get_recent_signals() calls client.query() once with telemetry_signals.
    Mocked result_rows returns one tuple; assert SignalRow attributes match.

CU6 test_ch_get_all_model_tuples_returns_sorted_list
    get_all_model_tuples() calls client.query() with DISTINCT.
    Mocked result_rows returns two tuples; assert sorted list returned.

CU7 test_ch_get_recent_alerts_returns_alert_rows
    get_recent_alerts() calls client.query() with public_drift_alerts.
    Mocked result_rows returns one tuple; assert AlertRow.timestamp present.

Adversarial note
----------------
T3 is the nullable-metrics gate: probes that cannot measure
avg_output_length (e.g., provider APIs that do not return token counts)
must not cause a DB write failure.

#SG-TRACE: REQ-STORE-008 | test: test_save_batch_persists_to_db
#SG-TRACE: REQ-STORE-003 | test: test_save_batch_nullable_metrics_when_absent
#SG-TRACE: REQ-STORE-009 | test: test_save_alert_persists_to_db
# SG-TRACE: REQ-STORE-002
#   | test: test_save_alert_stores_cusum_score_as_alert_value
#SG-TRACE: REQ-STORE-010 | test: test_get_recent_signals_respects_limit
#SG-TRACE: REQ-STORE-014 | test: test_ch_setup_tables_creates_all_three_tables
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from engine.clickhouse import ClickHouseRepository
from engine.detector import DriftAlert as DetectorDriftAlert
from engine.models import LocalDriftAlert
from engine.repository import AlertRow, SignalRepository
from gateway.schema import InboundSignalBatch
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_A = "anthropic/claude-3-5-sonnet@global"
_MODEL_B = "openai/gpt-4o@2025-08"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_batch(
    model_tuple: str = _MODEL_A,
    metrics: dict | None = None,
    result_count: int = 3,
) -> InboundSignalBatch:
    """Return a valid InboundSignalBatch for testing."""
    if metrics is None:
        metrics = {"json_success_rate": 0.9, "avg_output_length": 512.0}
    return InboundSignalBatch.model_validate(
        {
            "batch_id": str(uuid.uuid4()),
            "client_id": str(uuid.uuid4()),
            "window_start": "2025-08-01T00:00:00Z",
            "window_end": "2025-08-01T01:00:00Z",
            "model_tuple": model_tuple,
            "suite_version": "v1.0.0",
            "metrics": metrics,
            "canary_hashes": {
                "v1.0.0-logic": _sha256("logic"),
                "v1.0.0-format": _sha256("format"),
            },
            "result_count": result_count,
        }
    )


def _make_alert(
    model_tuple: str = _MODEL_A,
    metric_name: str = "json_success_rate",
    cusum_score: float = 6.5,
) -> DetectorDriftAlert:
    """Return a DetectorDriftAlert for testing."""
    return DetectorDriftAlert(
        timestamp_ns=1_000_000_000,
        model_tuple=model_tuple,
        metric_name=metric_name,
        direction="negative",
        cusum_score=cusum_score,
        threshold=5.0,
        window_count=35,
    )


@pytest.fixture()
def repo() -> SignalRepository:
    """Fresh in-memory SignalRepository for each test."""
    return SignalRepository("sqlite:///:memory:")


@pytest.fixture()
def mock_ch_client() -> MagicMock:
    """Mocked clickhouse_connect client for ClickHouseRepository tests."""
    client = MagicMock()
    # Default query result: empty result_rows list
    client.query.return_value.result_rows = []
    return client


@pytest.fixture()
def ch_repo(mock_ch_client: MagicMock) -> ClickHouseRepository:
    """ClickHouseRepository wired to the mocked client."""
    return ClickHouseRepository(mock_ch_client)


# ---------------------------------------------------------------------------
# T1 -- save_batch persists a row
# ---------------------------------------------------------------------------


def test_save_batch_persists_to_db(repo: SignalRepository) -> None:
    """T1: Saving a batch results in one TelemetrySignal row."""
    batch = _make_batch()
    repo.save_batch(batch)

    rows = repo.get_recent_signals(_MODEL_A)
    assert len(rows) == 1
    assert rows[0].batch_id == str(batch.batch_id)
    assert rows[0].model_tuple == _MODEL_A
    assert rows[0].result_count == 3.0


# ---------------------------------------------------------------------------
# T2 -- save_batch extracts metrics correctly
# ---------------------------------------------------------------------------


def test_save_batch_extracts_metrics_correctly(repo: SignalRepository) -> None:
    """T2: avg_output_length and json_success_rate stored from metrics dict."""
    batch = _make_batch(
        metrics={"json_success_rate": 0.85, "avg_output_length": 256.0}
    )
    repo.save_batch(batch)

    rows = repo.get_recent_signals(_MODEL_A)
    assert rows[0].json_success_rate == pytest.approx(0.85)
    assert rows[0].avg_output_length == pytest.approx(256.0)


# ---------------------------------------------------------------------------
# T3 -- nullable metrics when absent (adversarial: probe omits metric)
# ---------------------------------------------------------------------------


def test_save_batch_nullable_metrics_when_absent(
    repo: SignalRepository,
) -> None:
    """T3: avg_output_length stores NULL when absent from the metrics dict.

    Adversarial case: probe cannot measure token counts.  The DB write
    must succeed and return None for the missing column.
    """
    batch = _make_batch(metrics={"json_success_rate": 0.75})
    repo.save_batch(batch)

    rows = repo.get_recent_signals(_MODEL_A)
    assert rows[0].avg_output_length is None
    assert rows[0].json_success_rate == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# T4 -- save_local_alert persists a row
# ---------------------------------------------------------------------------


def test_save_alert_persists_to_db(repo: SignalRepository) -> None:
    """T4: Saving a DriftAlert via save_local_alert() persists to DB.

    Queries local_drift_alerts directly via ORM select to stay
    independent of any future get_recent_alerts() API changes.
    """
    alert = _make_alert()
    repo.save_local_alert(alert, client_id="test-client-001")

    # Query directly via SQLAlchemy to stay independent of any future
    # get_recent_alerts() API
    with repo._db.session() as sess:
        rows = list(sess.scalars(select(LocalDriftAlert)).all())

    assert len(rows) == 1
    assert rows[0].model_tuple == _MODEL_A
    assert rows[0].metric_name == "json_success_rate"
    assert rows[0].client_id == "test-client-001"


# ---------------------------------------------------------------------------
# T5 -- cusum_score stored as alert_value
# ---------------------------------------------------------------------------


def test_save_alert_stores_cusum_score_as_alert_value(
    repo: SignalRepository,
) -> None:
    """T5: alert_value in local_drift_alerts equals cusum_score."""
    alert = _make_alert(cusum_score=7.314)
    repo.save_local_alert(alert, client_id="test-client-001")

    with repo._db.session() as sess:
        row = sess.scalars(select(LocalDriftAlert)).first()

    assert row is not None
    assert row.alert_value == pytest.approx(7.314)


# ---------------------------------------------------------------------------
# T6 -- get_recent_signals filters by model_tuple
# ---------------------------------------------------------------------------


def test_get_recent_signals_filters_by_model_tuple(
    repo: SignalRepository,
) -> None:
    """T6: Two model_tuples saved; get_recent_signals returns only one."""
    repo.save_batch(_make_batch(model_tuple=_MODEL_A))
    repo.save_batch(_make_batch(model_tuple=_MODEL_B))
    repo.save_batch(_make_batch(model_tuple=_MODEL_A))

    a_rows = repo.get_recent_signals(_MODEL_A)
    b_rows = repo.get_recent_signals(_MODEL_B)

    assert len(a_rows) == 2
    assert len(b_rows) == 1
    assert all(r.model_tuple == _MODEL_A for r in a_rows)
    assert all(r.model_tuple == _MODEL_B for r in b_rows)


# ---------------------------------------------------------------------------
# T7 -- get_recent_signals respects limit
# ---------------------------------------------------------------------------


def test_get_recent_signals_respects_limit(repo: SignalRepository) -> None:
    """T7: Saving 5 rows; limit=2 returns exactly 2 rows."""
    for _ in range(5):
        repo.save_batch(_make_batch())

    rows = repo.get_recent_signals(_MODEL_A, limit=2)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# T8 -- get_recent_signals on unknown model returns empty list
# ---------------------------------------------------------------------------


def test_get_recent_signals_empty_returns_empty_list(
    repo: SignalRepository,
) -> None:
    """T8: No data for model_tuple returns an empty list, not an error."""
    rows = repo.get_recent_signals("nonexistent/model@v0")
    assert rows == []


# ===========================================================================
# ClickHouse mocked tests (CU1-CU7)
# No live ClickHouse daemon required -- client is a MagicMock.
# ===========================================================================


# ---------------------------------------------------------------------------
# CU1 -- setup_tables calls command() 3 times with correct table names
# ---------------------------------------------------------------------------


def test_ch_setup_tables_creates_all_three_tables(
    ch_repo: ClickHouseRepository,
    mock_ch_client: MagicMock,
) -> None:
    """CU1: setup_tables() issues 3 CREATE TABLE IF NOT EXISTS commands.

    #SG-TRACE: REQ-STORE-014
    """
    ch_repo.setup_tables()

    assert mock_ch_client.command.call_count == 3
    sqls = [c.args[0] for c in mock_ch_client.command.call_args_list]
    table_names = {
        "telemetry_signals",
        "local_drift_alerts",
        "public_drift_alerts",
    }
    for table in table_names:
        assert any(table in sql for sql in sqls), (
            f"Expected CREATE TABLE for {table!r}"
        )
    for sql in sqls:
        assert "CREATE TABLE IF NOT EXISTS" in sql
        assert "MergeTree" in sql


# ---------------------------------------------------------------------------
# CU2 -- save_batch inserts to telemetry_signals
# ---------------------------------------------------------------------------


def test_ch_save_batch_inserts_to_telemetry_signals(
    ch_repo: ClickHouseRepository,
    mock_ch_client: MagicMock,
) -> None:
    """CU2: save_batch() calls client.insert('telemetry_signals', ...).

    Asserts table name, required column_names, and data row structure.
    """
    batch = _make_batch(
        metrics={"json_success_rate": 0.9, "avg_output_length": 512.0}
    )
    ch_repo.save_batch(batch)

    mock_ch_client.insert.assert_called_once()
    args, kwargs = mock_ch_client.insert.call_args
    table = args[0] if args else kwargs.get("table")
    assert table == "telemetry_signals"

    columns = kwargs.get("column_names") or args[2]
    assert "batch_id" in columns
    assert "model_tuple" in columns
    assert "json_success_rate" in columns
    assert "avg_output_length" in columns

    data = kwargs.get("data") or args[1]
    assert len(data) == 1  # one row
    assert len(data[0]) == 7  # seven columns (fleet_id added P3-001)


# ---------------------------------------------------------------------------
# CU3 -- save_local_alert inserts to local_drift_alerts
# ---------------------------------------------------------------------------


def test_ch_save_local_alert_inserts_to_local_drift_alerts(
    ch_repo: ClickHouseRepository,
    mock_ch_client: MagicMock,
) -> None:
    """CU3: save_local_alert() calls client.insert('local_drift_alerts', ...).

    Asserts client_id and alert_value (cusum_score) appear in call.
    """
    alert = _make_alert(cusum_score=7.5)
    ch_repo.save_local_alert(alert, client_id="org-abc-123")

    mock_ch_client.insert.assert_called_once()
    args, kwargs = mock_ch_client.insert.call_args
    table = args[0] if args else kwargs.get("table")
    assert table == "local_drift_alerts"

    columns = kwargs.get("column_names") or args[2]
    assert "alert_value" in columns
    assert "client_id" in columns

    data = kwargs.get("data") or args[1]
    row = data[0]
    assert 7.5 in row
    assert "org-abc-123" in row


# ---------------------------------------------------------------------------
# CU4 -- save_public_alert inserts to public_drift_alerts
# ---------------------------------------------------------------------------


def test_ch_save_public_alert_inserts_to_public_drift_alerts(
    ch_repo: ClickHouseRepository,
    mock_ch_client: MagicMock,
) -> None:
    """CU4: save_public_alert() targets public_drift_alerts table.

    Asserts contributing_org_count column and value appear in the call.
    """
    ch_repo.save_public_alert(
        model_tuple=_MODEL_A,
        metric_name="json_success_rate",
        contributing_org_count=3,
    )

    mock_ch_client.insert.assert_called_once()
    args, kwargs = mock_ch_client.insert.call_args
    table = args[0] if args else kwargs.get("table")
    assert table == "public_drift_alerts"

    columns = kwargs.get("column_names") or args[2]
    assert "contributing_org_count" in columns

    data = kwargs.get("data") or args[1]
    assert 3 in data[0]


# ---------------------------------------------------------------------------
# CU5 -- get_recent_signals queries telemetry_signals and returns SignalRows
# ---------------------------------------------------------------------------


def test_ch_get_recent_signals_returns_signal_rows(
    ch_repo: ClickHouseRepository,
    mock_ch_client: MagicMock,
) -> None:
    """CU5: get_recent_signals() queries telemetry_signals and maps to
    SignalRow objects with correct attribute values.
    """
    ts = datetime(2025, 8, 1, 12, 0, 0)
    mock_ch_client.query.return_value.result_rows = [
        ("batch-001", _MODEL_A, ts, 512.0, 0.95, 10.0),
    ]

    rows = ch_repo.get_recent_signals(_MODEL_A, limit=5)

    mock_ch_client.query.assert_called_once()
    sql_arg = mock_ch_client.query.call_args.args[0]
    assert "telemetry_signals" in sql_arg

    assert len(rows) == 1
    row = rows[0]
    assert row.batch_id == "batch-001"
    assert row.model_tuple == _MODEL_A
    assert row.json_success_rate == pytest.approx(0.95)
    assert row.avg_output_length == pytest.approx(512.0)
    assert row.result_count == pytest.approx(10.0)
    assert row.timestamp == ts


# ---------------------------------------------------------------------------
# CU6 -- get_all_model_tuples queries DISTINCT and returns sorted list
# ---------------------------------------------------------------------------


def test_ch_get_all_model_tuples_returns_sorted_list(
    ch_repo: ClickHouseRepository,
    mock_ch_client: MagicMock,
) -> None:
    """CU6: get_all_model_tuples() queries DISTINCT model_tuple and returns
    a sorted list of strings.
    """
    mock_ch_client.query.return_value.result_rows = [
        ("anthropic/claude-3-5-sonnet@global",),
        ("openai/gpt-4o@2025-08",),
    ]

    result = ch_repo.get_all_model_tuples()

    mock_ch_client.query.assert_called_once()
    sql_arg = mock_ch_client.query.call_args.args[0]
    assert "DISTINCT" in sql_arg
    assert "telemetry_signals" in sql_arg
    assert result == [
        "anthropic/claude-3-5-sonnet@global",
        "openai/gpt-4o@2025-08",
    ]


# ---------------------------------------------------------------------------
# CU7 -- get_recent_alerts queries public_drift_alerts and returns AlertRows
# ---------------------------------------------------------------------------


def test_ch_get_recent_alerts_returns_alert_rows(
    ch_repo: ClickHouseRepository,
    mock_ch_client: MagicMock,
) -> None:
    """CU7: get_recent_alerts() queries public_drift_alerts and returns
    AlertRow objects with a .timestamp attribute.

    Adversarial: single-org local alerts must never appear here (they are
    stored in local_drift_alerts and never queried by this method).
    """
    ts = datetime(2025, 8, 15, 9, 0, 0)
    mock_ch_client.query.return_value.result_rows = [
        (ts, _MODEL_A, "json_success_rate", 2),
    ]

    alerts = ch_repo.get_recent_alerts(_MODEL_A, hours_back=24)

    mock_ch_client.query.assert_called_once()
    sql_arg = mock_ch_client.query.call_args.args[0]
    assert "public_drift_alerts" in sql_arg
    assert "local_drift_alerts" not in sql_arg  # adversarial check

    assert len(alerts) == 1
    alert = alerts[0]
    assert isinstance(alert, AlertRow)
    assert alert.timestamp == ts
    assert alert.model_tuple == _MODEL_A
    assert alert.metric_name == "json_success_rate"
    assert alert.contributing_org_count == 2
