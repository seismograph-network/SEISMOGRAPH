"""
tests.test_audit
=================
Test suite for P3-004: SOC 2 audit-grade incident export.

Coverage
--------
AU1  generate() resolves a LocalDriftAlert and returns a valid report
AU2  generate() resolves a PublicDriftAlert when no local match
AU3  generate() raises AlertNotFoundError for unknown alert_id
AU4  baseline_evidence contains up to 50 signals before alert timestamp
AU5  report_checksum matches SHA-256 of canonical JSON (sorted keys)
AU6  GET /v1/alerts/{alert_id}/export returns 200 + Content-Disposition
AU7  GET /v1/alerts/{alert_id}/export returns 404 for missing id
AU8  exported JSON checksum field verifies correctly (adversarial tamper)

#SG-TRACE: REQ-AUDIT-000
#   | assumption: tests use sqlite:///:memory: via conftest autouse
#     fixture; app.state.repo is the live SignalRepository
#   | test: all AU* tests below
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from engine.audit import AlertNotFoundError, AuditReportGenerator
from engine.detector import DriftAlert as DetectorDriftAlert
from engine.models import TelemetrySignal
from engine.repository import SignalRepository
from fastapi.testclient import TestClient
from gateway.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo() -> SignalRepository:
    """Return a fresh in-memory SignalRepository."""
    return SignalRepository("sqlite:///:memory:")


def _naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _save_local_alert(repo: SignalRepository) -> int:
    """Insert one LocalDriftAlert; return its integer id."""
    alert = DetectorDriftAlert(
        timestamp_ns=1_000_000_000,
        model_tuple="openai/gpt-4o",
        metric_name="json_success_rate",
        direction="negative",
        cusum_score=4.2,
        threshold=5.0,
        window_count=10,
    )
    repo.save_local_alert(
        alert, client_id="client-au-test", fleet_id="fleet-au"
    )
    # Fetch back to get the assigned id
    from engine.models import LocalDriftAlert as LDA
    from sqlalchemy import select

    with repo._db.session() as sess:
        row = sess.scalars(
            select(LDA).order_by(LDA.id.desc()).limit(1)
        ).first()
    assert row is not None
    return row.id


def _save_public_alert(repo: SignalRepository) -> int:
    """Insert one PublicDriftAlert; return its integer id."""
    repo.save_public_alert(
        model_tuple="openai/gpt-4o",
        metric_name="avg_output_length",
        contributing_org_count=3,
    )
    from engine.models import PublicDriftAlert as PDA
    from sqlalchemy import select

    with repo._db.session() as sess:
        row = sess.scalars(
            select(PDA).order_by(PDA.id.desc()).limit(1)
        ).first()
    assert row is not None
    return row.id


def _save_signal(
    repo: SignalRepository,
    model_tuple: str = "openai/gpt-4o",
    fleet_id: str | None = None,
    ts: datetime | None = None,
) -> None:
    """Insert one TelemetrySignal directly (bypasses InboundSignalBatch)."""

    record = TelemetrySignal(
        batch_id="00000001-0000-0000-0000-000000000001",
        timestamp=ts or _naive_utc(),
        model_tuple=model_tuple,
        avg_output_length=512.0,
        json_success_rate=0.95,
        result_count=10.0,
        fleet_id=fleet_id,
    )
    with repo._db.session() as sess:
        sess.add(record)


# ---------------------------------------------------------------------------
# AU1 — generate() resolves LocalDriftAlert
# ---------------------------------------------------------------------------


def test_audit_generate_local_alert() -> None:
    """AU1: generate() returns correct alert_details for a local alert."""
    repo = _make_repo()
    alert_id = _save_local_alert(repo)

    gen = AuditReportGenerator(repo)
    report = gen.generate(alert_id)

    assert report["alert_details"]["type"] == "local"
    assert report["alert_details"]["id"] == alert_id
    assert report["alert_details"]["model_tuple"] == "openai/gpt-4o"
    assert report["alert_details"]["metric_name"] == "json_success_rate"
    assert report["alert_details"]["client_id"] == "client-au-test"
    assert report["alert_details"]["fleet_id"] == "fleet-au"
    assert "export_timestamp" in report
    assert "report_checksum" in report


# ---------------------------------------------------------------------------
# AU2 — generate() resolves PublicDriftAlert when no local match
# ---------------------------------------------------------------------------


def test_audit_generate_public_alert() -> None:
    """AU2: generate() falls back to public alert when local not found."""
    repo = _make_repo()
    pub_id = _save_public_alert(repo)

    gen = AuditReportGenerator(repo)
    report = gen.generate(pub_id)

    assert report["alert_details"]["type"] == "public"
    assert report["alert_details"]["id"] == pub_id
    assert report["alert_details"]["contributing_org_count"] == 3
    assert "fleet_id" not in report["alert_details"]


# ---------------------------------------------------------------------------
# AU3 — generate() raises AlertNotFoundError for unknown id
# ---------------------------------------------------------------------------


def test_audit_generate_raises_not_found() -> None:
    """AU3: AlertNotFoundError raised when alert_id is absent."""
    repo = _make_repo()
    gen = AuditReportGenerator(repo)

    with pytest.raises(AlertNotFoundError) as exc_info:
        gen.generate(99999)

    assert exc_info.value.alert_id == 99999


# ---------------------------------------------------------------------------
# AU4 — baseline_evidence contains signals before alert timestamp
# ---------------------------------------------------------------------------


def test_audit_baseline_evidence_count() -> None:
    """AU4: baseline_evidence contains signals preceding alert timestamp.

    Insert 5 signals, save a local alert, then insert 3 more signals
    after the alert.  The report should contain exactly the 5 earlier
    signals (fleet-filtered).
    """

    repo = _make_repo()

    # Insert 5 signals before the alert
    for i in range(5):
        _save_signal(
            repo,
            fleet_id="fleet-au",
            ts=datetime(2026, 1, 1, 10, 0, i),
        )

    alert_ts = datetime(2026, 1, 1, 10, 0, 10)
    # Directly insert the alert row with a known timestamp
    from engine.models import LocalDriftAlert as LDA

    with repo._db.session() as sess:
        row = LDA(
            timestamp=alert_ts,
            model_tuple="openai/gpt-4o",
            metric_name="json_success_rate",
            alert_value=4.2,
            client_id="client-au",
            fleet_id="fleet-au",
        )
        sess.add(row)
    # Fetch id
    from sqlalchemy import select

    with repo._db.session() as sess:
        saved = sess.scalars(
            select(LDA).order_by(LDA.id.desc()).limit(1)
        ).first()
    alert_id = saved.id

    # Insert 3 signals AFTER the alert (should be excluded)
    for i in range(3):
        _save_signal(
            repo,
            fleet_id="fleet-au",
            ts=datetime(2026, 1, 1, 10, 0, 20 + i),
        )

    gen = AuditReportGenerator(repo)
    report = gen.generate(alert_id)

    assert len(report["baseline_evidence"]) == 5, (
        f"Expected 5 evidence rows, got {len(report['baseline_evidence'])}"
    )
    # All evidence timestamps must be before alert_ts
    for ev in report["baseline_evidence"]:
        ev_ts = datetime.fromisoformat(ev["timestamp"])
        assert ev_ts < alert_ts, f"Evidence row after alert: {ev_ts}"


# ---------------------------------------------------------------------------
# AU5 — report_checksum matches SHA-256 of canonical JSON
# ---------------------------------------------------------------------------


def test_audit_report_checksum_valid() -> None:
    """AU5: report_checksum is SHA-256 of json.dumps(body, sort_keys=True).

    The verifier re-serialises {export_timestamp, alert_details,
    baseline_evidence} (without report_checksum) and recomputes sha256.
    """
    repo = _make_repo()
    alert_id = _save_local_alert(repo)
    gen = AuditReportGenerator(repo)
    report = gen.generate(alert_id)

    checksum = report.pop("report_checksum")
    canonical = json.dumps(report, sort_keys=True, default=str)
    expected = hashlib.sha256(canonical.encode()).hexdigest()

    assert checksum == expected, (
        f"Checksum mismatch: stored={checksum[:16]}... "
        f"recomputed={expected[:16]}..."
    )


# ---------------------------------------------------------------------------
# AU6 — GET /v1/alerts/{alert_id}/export returns 200 + Content-Disposition
# ---------------------------------------------------------------------------


def test_audit_endpoint_200() -> None:
    """AU6: endpoint returns 200 and Content-Disposition attachment header."""
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            repo = app.state.repo
            alert_id = _save_local_alert(repo)

            resp = c.get(f"/v1/alerts/{alert_id}/export")

    assert resp.status_code == 200, resp.text
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd, (
        f"Missing attachment in Content-Disposition: {cd!r}"
    )
    assert f"seismograph_audit_{alert_id}.json" in cd, (
        f"Filename missing in Content-Disposition: {cd!r}"
    )
    body = resp.json()
    assert "report_checksum" in body
    assert "alert_details" in body
    assert "baseline_evidence" in body


# ---------------------------------------------------------------------------
# AU7 — GET /v1/alerts/{alert_id}/export returns 404 for missing id
# ---------------------------------------------------------------------------


def test_audit_endpoint_404() -> None:
    """AU7: endpoint returns 404 when alert_id is not in either table."""
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            resp = c.get("/v1/alerts/99999/export")

    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# AU8 — adversarial: tampered report fails checksum verification
# ---------------------------------------------------------------------------


def test_audit_checksum_detects_tampering() -> None:
    """AU8 (adversarial): checksum mismatch if report body is altered.

    Simulates a tampered audit report where alert_value is changed after
    export.  The stored checksum no longer matches the mutated body,
    demonstrating SOC 2 tamper evidence.
    """
    repo = _make_repo()
    alert_id = _save_local_alert(repo)
    gen = AuditReportGenerator(repo)
    report = gen.generate(alert_id)

    # Store original checksum
    original_checksum = report["report_checksum"]

    # Tamper: change the alert_value in alert_details
    report["alert_details"]["alert_value"] = 0.0

    # Re-verify: build the body WITHOUT report_checksum and recompute
    body_for_verify = {
        k: v for k, v in report.items() if k != "report_checksum"
    }
    canonical = json.dumps(body_for_verify, sort_keys=True, default=str)
    tampered_checksum = hashlib.sha256(canonical.encode()).hexdigest()

    assert tampered_checksum != original_checksum, (
        "Checksum collision on tampered report — integrity guarantee violated"
    )
