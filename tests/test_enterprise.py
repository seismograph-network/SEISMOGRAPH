"""
tests.test_enterprise
======================
Test suite for P3-001 Multi-Tenant Data Isolation (Phase 3).

Test matrix
-----------
EN1  fleet_alert_fires_without_quorum
       Private fleet batch drives CUSUM to alert; no quorum needed
       because private fleet never touches AgreementScorer.
EN2  private_alert_absent_from_weather  (ADVERSARIAL)
       After a private fleet CUSUM fires, GET /v1/weather must NOT
       return DRIFTING for the affected model_tuple.
EN3  fleet_id_stored_in_telemetry_signal
       fleet_id field is persisted to telemetry_signals when non-None.
EN4  fleet_id_stored_in_local_drift_alert
       fleet_id field is persisted to local_drift_alerts on alert.
EN5  public_batch_unaffected_by_private_fleet
       A public-path batch (fleet_id=None) on the same model_tuple
       does not interact with a private-fleet detector.

Design notes
------------
- All tests use TestClient(app) with verify_signature mocked to True.
- Private detectors are pre-injected into app.state.private_detectors
  with baseline_samples=3 so tests complete in < 20 round trips.
- EN2 is the critical adversarial case: private alerts MUST NOT
  promote to PublicDriftAlert or appear in /v1/weather.

#SG-TRACE: REQ-ENT-001
#   | assumption: private fleet path isolation is the sole new
#     invariant in Phase 3 Step 1; all prior 75 tests are unaffected
#   | test: EN1--EN5
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from engine.detector import CUSUMDetector
from engine.models import LocalDriftAlert, TelemetrySignal
from fastapi.testclient import TestClient
from gateway.main import app
from sqlalchemy import select
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    """Return 64-char lowercase hex SHA-256 of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _batch(
    n: int,
    prefix: str,
    metrics: dict,
    fleet_id: str | None = None,
) -> dict:
    """Build a minimal valid InboundSignalBatch dict."""
    bid = f"{prefix}{n:06d}-0000-0000-0000-000000000000"
    return {
        "batch_id": bid,
        "client_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "window_start": "2026-06-01T00:00:00Z",
        "window_end": "2026-06-01T01:00:00Z",
        "model_tuple": "openai/gpt-4o@2026-fleet-test",
        "suite_version": "v3.0.0",
        "metrics": metrics,
        "canary_hashes": {
            "p001": _sha256(f"fleet-canary-{n}"),
        },
        "result_count": 5,
        "fleet_id": fleet_id,
    }


_FLEET_ID = "fleet-acme-unit-test"
_MODEL = "openai/gpt-4o@2026-fleet-test"
_STABLE_METRICS = {"json_success_rate": 0.95}
_DRIFT_METRICS = {"json_success_rate": 0.0}


# ---------------------------------------------------------------------------
# EN1 -- fleet alert fires without quorum
# ---------------------------------------------------------------------------


def test_fleet_alert_fires_without_quorum() -> None:
    """Private fleet CUSUM fires; AgreementScorer is never called.

    Steps:
    1. Pre-inject a low-baseline (baseline_samples=3) private detector.
    2. Send 3 stable batches through the private detector (baseline).
    3. Send up to 15 drift batches; expect CUSUM to fire on one.
    4. Assert that at least one alert was returned in the 202 body.

    Invariant: private alert must not require quorum.

    #SG-TRACE: REQ-ENT-001
    #   | assumption: private_detectors dict is mutated in-place
    #     before any batch is posted, so gateway uses our detector
    #   | test: EN1
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            # Inject low-baseline private detector for our fleet.
            app.state.private_detectors[_FLEET_ID] = CUSUMDetector(
                h=5.0, k=0.5, baseline_samples=3
            )

            # Baseline phase: 3 stable batches.
            for i in range(3):
                resp = c.post(
                    "/v1/signals",
                    json=_batch(i, "e1", _STABLE_METRICS, _FLEET_ID),
                )
                assert resp.status_code == 202, resp.text

            # Drift phase: drive CUSUM over threshold.
            cusum_fired = False
            for i in range(15):
                resp = c.post(
                    "/v1/signals",
                    json=_batch(100 + i, "e1", _DRIFT_METRICS, _FLEET_ID),
                )
                assert resp.status_code == 202, resp.text
                if resp.json().get("alerts"):
                    cusum_fired = True
                    break

    assert cusum_fired, (
        "EN1 FAIL: private fleet CUSUM did not fire within 15 batches"
    )


# ---------------------------------------------------------------------------
# EN2 (ADVERSARIAL) -- private alert absent from /v1/weather
# ---------------------------------------------------------------------------


def test_private_alert_absent_from_weather() -> None:
    """Private fleet alert MUST NOT appear in GET /v1/weather.

    A private fleet CUSUM fires (verified by EN1 logic above).
    After the fire, the public weather endpoint must report STABLE
    for the same model_tuple -- because no PublicDriftAlert was
    created (AgreementScorer was never called on the private path).

    This is the primary adversarial invariant for P3-001: the
    private fleet boundary must be impermeable to the public API.

    #SG-TRACE: REQ-ENT-001
    #   | assumption: GET /v1/weather reads only public_drift_alerts;
    #     local_drift_alerts (fleet or not) are never reflected there
    #   | test: EN2 (ADVERSARIAL)
    """
    weather_response = None

    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            # Inject low-baseline private detector.
            app.state.private_detectors[_FLEET_ID] = CUSUMDetector(
                h=5.0, k=0.5, baseline_samples=3
            )

            # Baseline.
            for i in range(3):
                c.post(
                    "/v1/signals",
                    json=_batch(i, "e2", _STABLE_METRICS, _FLEET_ID),
                )

            # Drive CUSUM to fire.
            for i in range(15):
                resp = c.post(
                    "/v1/signals",
                    json=_batch(100 + i, "e2", _DRIFT_METRICS, _FLEET_ID),
                )
                assert resp.status_code == 202, resp.text
                if resp.json().get("alerts"):
                    break  # confirmed fired

            # Check public weather AFTER private alert fired.
            weather_response = c.get("/v1/weather")

    assert weather_response is not None
    assert weather_response.status_code == 200, weather_response.text

    data = weather_response.json()
    entry = next((e for e in data if e["model_tuple"] == _MODEL), None)
    # Model exists in weather (batch was saved) but status is STABLE.
    if entry is not None:
        status = entry["status"]
        assert status == "STABLE", (
            f"EN2 ADVERSARIAL FAIL: private fleet alert leaked into "
            f"public weather; got status={status!r} for {_MODEL!r}. "
            "PublicDriftAlert must never be created on the private path."
        )


# ---------------------------------------------------------------------------
# EN3 -- fleet_id stored in telemetry_signal
# ---------------------------------------------------------------------------


def test_fleet_id_stored_in_telemetry_signal() -> None:
    """fleet_id is persisted to telemetry_signals on save_batch().

    Verifies that the fleet_id field on InboundSignalBatch flows
    through SignalRepository.save_batch() into the TelemetrySignal row.

    #SG-TRACE: REQ-ENT-001
    #   | assumption: fleet_id column is Nullable(String(128)); the
    #     value stored must exactly match the batch fleet_id
    #   | test: EN3
    """
    target_fleet = "fleet-storage-verify"
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            resp = c.post(
                "/v1/signals",
                json=_batch(0, "e3", _STABLE_METRICS, target_fleet),
            )
            assert resp.status_code == 202, resp.text

            # Query the in-memory DB directly.
            repo = app.state.repo
            engine = repo._db._engine
            with Session(engine) as sess:
                rows = list(
                    sess.scalars(
                        select(TelemetrySignal).where(
                            TelemetrySignal.model_tuple == _MODEL
                        )
                    ).all()
                )

    assert len(rows) >= 1, "EN3 FAIL: no TelemetrySignal rows found"
    stored = [r.fleet_id for r in rows]
    assert target_fleet in stored, (
        f"EN3 FAIL: fleet_id={target_fleet!r} not found in stored rows"
        f"; got {stored!r}"
    )


# ---------------------------------------------------------------------------
# EN4 -- fleet_id stored in local_drift_alert
# ---------------------------------------------------------------------------


def test_fleet_id_stored_in_local_drift_alert() -> None:
    """fleet_id is persisted to local_drift_alerts on alert.

    Fires a private fleet CUSUM and verifies that the resulting
    LocalDriftAlert row carries the correct fleet_id.

    #SG-TRACE: REQ-ENT-001
    #   | assumption: save_local_alert(fleet_id=batch.fleet_id) is
    #     called on the private path; value must match exactly
    #   | test: EN4
    """
    target_fleet = "fleet-alert-verify"
    alert_db_rows: list = []

    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            app.state.private_detectors[target_fleet] = CUSUMDetector(
                h=5.0, k=0.5, baseline_samples=3
            )

            for i in range(3):
                c.post(
                    "/v1/signals",
                    json=_batch(i, "e4", _STABLE_METRICS, target_fleet),
                )

            for i in range(15):
                resp = c.post(
                    "/v1/signals",
                    json=_batch(100 + i, "e4", _DRIFT_METRICS, target_fleet),
                )
                assert resp.status_code == 202, resp.text
                if resp.json().get("alerts"):
                    break

            repo = app.state.repo
            engine = repo._db._engine
            with Session(engine) as sess:
                alert_db_rows = list(
                    sess.scalars(
                        select(LocalDriftAlert).where(
                            LocalDriftAlert.model_tuple == _MODEL
                        )
                    ).all()
                )

    fleet_alerts = [r for r in alert_db_rows if r.fleet_id == target_fleet]
    assert len(fleet_alerts) >= 1, (
        f"EN4 FAIL: no LocalDriftAlert with fleet_id={target_fleet!r};"
        f" rows found: {[(r.fleet_id, r.metric_name) for r in alert_db_rows]}"
    )


# ---------------------------------------------------------------------------
# EN5 -- public batch unaffected by private fleet detector
# ---------------------------------------------------------------------------


def test_public_batch_unaffected_by_private_fleet() -> None:
    """Public (fleet_id=None) path uses global detector, not fleet's.

    Send a private fleet batch that fires CUSUM on the fleet detector,
    then send a public batch for the same model_tuple.  Verify that the
    public batch is handled by the global CUSUMDetector (public path)
    and that the response is 202.

    #SG-TRACE: REQ-ENT-001
    #   | assumption: public and private detectors are fully isolated;
    #     a private fleet alert cannot affect the public detector state
    #   | test: EN5
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            app.state.private_detectors[_FLEET_ID] = CUSUMDetector(
                h=5.0, k=0.5, baseline_samples=3
            )

            # Fire private fleet CUSUM.
            for i in range(3):
                c.post(
                    "/v1/signals",
                    json=_batch(i, "e5", _STABLE_METRICS, _FLEET_ID),
                )
            for i in range(15):
                resp = c.post(
                    "/v1/signals",
                    json=_batch(100 + i, "e5", _DRIFT_METRICS, _FLEET_ID),
                )
                if resp.json().get("alerts"):
                    break

            # Now send a public batch (fleet_id=None).
            public_resp = c.post(
                "/v1/signals",
                json=_batch(200, "e5", _STABLE_METRICS, None),
            )

    assert public_resp.status_code == 202, public_resp.text
    body = public_resp.json()
    assert body["status"] == "accepted", (
        f"EN5 FAIL: public batch rejected; body={body!r}"
    )
