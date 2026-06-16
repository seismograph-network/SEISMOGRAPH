"""
tests.test_gateway
==================
TestClient suite for POST /v1/signals and GET /v1/weather
(gateway/main.py).

Test matrix
-----------
T1  valid_payload_returns_202
T2  schema_violation_unknown_metric_key_returns_422
T3  schema_violation_missing_required_field_returns_422
T4  unauthorized_returns_401
T5  bootstrap_warms_cusum_detector
T6  weather_endpoint_empty_returns_200
T7  weather_returns_stable_when_no_alerts
T8  weather_returns_drifting_when_recent_alert
T9  landing_root_returns_html
T14 dashboard_route_returns_html
T10 test_single_org_noise_blocked (ADVERSARIAL)
T11 test_quorum_reached_triggers_dashboard (ADVERSARIAL)
T12 test_signed_request_returns_202 (real Ed25519 -- no mock)
T13 test_tampered_payload_rejected (real Ed25519 -- no mock)

Design notes
------------
- client fixture wraps TestClient with verify_signature -> True.
- T4 overrides the patch inside its body to return False.
- T6-T8, T10-T11 wrap their own TestClient with the same patch.
- T12/T13 use crypto_client (no patch) to exercise real Ed25519 path.
"""

from __future__ import annotations

import copy
import hashlib
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)
from fastapi.testclient import TestClient
from gateway.main import app
from probe.crypto import canonical_json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    """Return a lowercase 64-char hex SHA-256 digest of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Shared valid payload
# ---------------------------------------------------------------------------

VALID_PAYLOAD: dict = {
    "batch_id": "12345678-1234-5678-1234-567812345678",
    "client_id": "87654321-4321-8765-4321-876543218765",
    "window_start": "2025-08-01T00:00:00Z",
    "window_end": "2025-08-01T01:00:00Z",
    "model_tuple": "anthropic/claude-3-5-sonnet@global",
    "suite_version": "v1.0.0",
    "metrics": {
        "json_success_rate": 0.95,
        "avg_output_length": 512.0,
    },
    "canary_hashes": {
        "prompt_001": _sha256("test canary prompt 001"),
        "prompt_002": _sha256("test canary prompt 002"),
    },
    "result_count": 10,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient with verify_signature patched True (T1-T9)."""
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            yield c


@pytest.fixture
def crypto_client():
    """TestClient with NO verify_signature patch (T12-T13 real crypto)."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------


def test_valid_payload_returns_202(client: TestClient) -> None:
    """A well-formed InboundSignalBatch returns 202 Accepted."""
    response = client.post("/v1/signals", json=VALID_PAYLOAD)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["batch_id"] == VALID_PAYLOAD["batch_id"]
    assert body["result_count"] == VALID_PAYLOAD["result_count"]
    assert isinstance(body["alerts"], list)


# ---------------------------------------------------------------------------
# T2
# ---------------------------------------------------------------------------


def test_schema_violation_unknown_metric_key_returns_422(
    client: TestClient,
) -> None:
    """A metric key outside ALLOWED_METRIC_KEYS triggers 422."""
    payload = copy.deepcopy(VALID_PAYLOAD)
    payload["metrics"]["raw_prompt_text"] = 1.0
    response = client.post("/v1/signals", json=payload)
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# T3
# ---------------------------------------------------------------------------


def test_schema_violation_missing_required_field_returns_422(
    client: TestClient,
) -> None:
    """A payload missing the required batch_id field returns 422."""
    payload = copy.deepcopy(VALID_PAYLOAD)
    del payload["batch_id"]
    response = client.post("/v1/signals", json=payload)
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# T4
# ---------------------------------------------------------------------------


def test_unauthorized_returns_401(client: TestClient) -> None:
    """When verify_signature returns False the endpoint returns 401.

    The client fixture patches True; this test overrides to False.
    """
    with patch("gateway.main.verify_signature", return_value=False):
        response = client.post("/v1/signals", json=VALID_PAYLOAD)

    assert response.status_code == 401, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "signature_verification_failed"


# ---------------------------------------------------------------------------
# T5
# ---------------------------------------------------------------------------


def test_bootstrap_warms_cusum_detector() -> None:
    """bootstrap_detector() restores CUSUMDetector baseline from DB.

    #SG-TRACE: REQ-GW-022
    """
    from engine.detector import CUSUMDetector
    from engine.repository import SignalRepository
    from gateway.main import bootstrap_detector
    from gateway.schema import InboundSignalBatch

    detector = CUSUMDetector(h=5.0, k=0.5, baseline_samples=5)
    repo = SignalRepository("sqlite:///:memory:")

    mt = VALID_PAYLOAD["model_tuple"]
    for i in range(8):
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["batch_id"] = f"00000000-0000-0000-0000-{str(i).zfill(12)}"
        batch = InboundSignalBatch.model_validate(payload)
        repo.save_batch(batch)

    count = bootstrap_detector(detector, repo)

    assert count >= 8, f"Expected >= 8 observations, got {count}"
    assert (mt, "json_success_rate") in detector.tracked_streams
    assert (mt, "avg_output_length") in detector.tracked_streams
    state = detector._states[(mt, "json_success_rate")]
    assert state.baseline_ready


# ---------------------------------------------------------------------------
# T6
# ---------------------------------------------------------------------------


def test_weather_endpoint_empty_returns_200() -> None:
    """GET /v1/weather on a fresh DB returns HTTP 200 and an empty list.

    #SG-TRACE: REQ-GW-023
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            response = c.get("/v1/weather")
    assert response.status_code == 200, response.text
    assert response.json() == []


# ---------------------------------------------------------------------------
# T7
# ---------------------------------------------------------------------------


def test_weather_returns_stable_when_no_alerts() -> None:
    """After ingesting one signal batch with no alerts, status is STABLE.

    #SG-TRACE: REQ-GW-023
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            post_resp = c.post("/v1/signals", json=VALID_PAYLOAD)
            assert post_resp.status_code == 202, post_resp.text
            response = c.get("/v1/weather")

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    entry = data[0]
    assert entry["model_tuple"] == VALID_PAYLOAD["model_tuple"]
    assert entry["status"] == "STABLE"
    assert entry["last_alert_timestamp"] is None
    assert isinstance(entry["recent_avg_output_length"], float)
    assert isinstance(entry["recent_json_success_rate"], float)


# ---------------------------------------------------------------------------
# T8
# ---------------------------------------------------------------------------


def test_weather_returns_drifting_when_recent_alert() -> None:
    """GET /v1/weather returns DRIFTING after a PublicDriftAlert is injected.

    #SG-TRACE: REQ-GW-024
    """
    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            post_resp = c.post("/v1/signals", json=VALID_PAYLOAD)
            assert post_resp.status_code == 202, post_resp.text

            app.state.repo.save_public_alert(
                model_tuple=VALID_PAYLOAD["model_tuple"],
                metric_name="json_success_rate",
                contributing_org_count=2,
            )
            response = c.get("/v1/weather")

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    entry = data[0]
    status = entry["status"]
    assert status == "DRIFTING", f"Expected DRIFTING, got {status!r}"
    assert entry["last_alert_timestamp"] is not None


# ---------------------------------------------------------------------------
# T9
# ---------------------------------------------------------------------------


def test_landing_root_returns_html(client: TestClient) -> None:
    """GET / serves the landing page (dashboard/static/landing.html).

    #SG-TRACE: REQ-DASH-001
    """
    response = client.get("/")
    assert response.status_code == 200, response.text
    ct = response.headers.get("content-type", "")
    assert "text/html" in ct, f"Expected text/html, got {ct!r}"
    assert "SEISMOGRAPH" in response.text


# ---------------------------------------------------------------------------
# T14
# ---------------------------------------------------------------------------


def test_dashboard_route_returns_html(client: TestClient) -> None:
    """GET /dashboard serves the Model Weather dashboard (index.html).

    #SG-TRACE: REQ-DASH-002
    """
    response = client.get("/dashboard")
    assert response.status_code == 200, response.text
    ct = response.headers.get("content-type", "")
    assert "text/html" in ct, f"Expected text/html, got {ct!r}"
    assert "SEISMOGRAPH" in response.text


# ---------------------------------------------------------------------------
# T10 -- ADVERSARIAL: single-org noise does NOT promote to public alert
# ---------------------------------------------------------------------------


def test_single_org_noise_blocked() -> None:
    """Single org triggers CUSUM; quorum not met; weather stays STABLE.

    Invariant: a single-org signal NEVER produces DRIFTING.

    #SG-TRACE: REQ-GW-025
    """
    from engine.correlation import AgreementScorer
    from engine.detector import CUSUMDetector

    client_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    mt = VALID_PAYLOAD["model_tuple"]

    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            app.state.detector = CUSUMDetector(
                h=5.0, k=0.5, baseline_samples=3
            )
            app.state.scorer = AgreementScorer()

            for i in range(3):
                payload = {
                    **VALID_PAYLOAD,
                    "batch_id": f"10{i:06d}-0000-0000-0000-000000000000",
                    "client_id": client_a,
                    "metrics": {"json_success_rate": 0.95},
                }
                c.post("/v1/signals", json=payload)

            cusum_fired = False
            for i in range(15):
                payload = {
                    **VALID_PAYLOAD,
                    "batch_id": f"11{i:06d}-0000-0000-0000-000000000000",
                    "client_id": client_a,
                    "metrics": {"json_success_rate": 0.0},
                }
                resp = c.post("/v1/signals", json=payload)
                assert resp.status_code == 202, resp.text
                if resp.json().get("alerts"):
                    cusum_fired = True
                    break

            assert cusum_fired, "CUSUM should fire for sustained bad metrics"
            weather = c.get("/v1/weather")

    assert weather.status_code == 200, weather.text
    data = weather.json()
    entry = next((e for e in data if e["model_tuple"] == mt), None)
    assert entry is not None, f"No weather entry for {mt!r}"
    status = entry["status"]
    assert status == "STABLE", (
        f"Expected STABLE (quorum not met), got {status!r}"
    )


# ---------------------------------------------------------------------------
# T11 -- ADVERSARIAL: two orgs reach quorum -> dashboard shows DRIFTING
# ---------------------------------------------------------------------------


def test_quorum_reached_triggers_dashboard() -> None:
    """Two distinct orgs agree on drift -> quorum -> DRIFTING.

    #SG-TRACE: REQ-GW-025
    """
    from engine.correlation import AgreementScorer
    from engine.detector import CUSUMDetector

    client_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    client_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    mt = VALID_PAYLOAD["model_tuple"]

    with patch("gateway.main.verify_signature", return_value=True):
        with TestClient(app) as c:
            app.state.detector = CUSUMDetector(
                h=5.0, k=0.5, baseline_samples=3
            )
            app.state.scorer = AgreementScorer()

            for i in range(3):
                payload = {
                    **VALID_PAYLOAD,
                    "batch_id": f"20{i:06d}-0000-0000-0000-000000000000",
                    "client_id": client_a,
                    "metrics": {"json_success_rate": 0.95},
                }
                c.post("/v1/signals", json=payload)

            cusum_fired_a = False
            for i in range(15):
                payload = {
                    **VALID_PAYLOAD,
                    "batch_id": f"21{i:06d}-0000-0000-0000-000000000000",
                    "client_id": client_a,
                    "metrics": {"json_success_rate": 0.0},
                }
                resp = c.post("/v1/signals", json=payload)
                assert resp.status_code == 202, resp.text
                if resp.json().get("alerts"):
                    cusum_fired_a = True
                    break

            assert cusum_fired_a, (
                "CUSUM should fire for client_a before client_b joins"
            )

            payload_b = {
                **VALID_PAYLOAD,
                "batch_id": "22000000-0000-0000-0000-000000000000",
                "client_id": client_b,
                "metrics": {"json_success_rate": 0.0},
            }
            resp_b = c.post("/v1/signals", json=payload_b)
            assert resp_b.status_code == 202, resp_b.text
            assert resp_b.json().get("alerts"), (
                "client_b batch should fire a local CUSUM alert"
            )

            weather = c.get("/v1/weather")

    assert weather.status_code == 200, weather.text
    data = weather.json()
    entry = next((e for e in data if e["model_tuple"] == mt), None)
    assert entry is not None, f"No weather entry for {mt!r}"
    status = entry["status"]
    assert status == "DRIFTING", (
        f"Expected DRIFTING after quorum, got {status!r}"
    )
    assert entry["last_alert_timestamp"] is not None


# ---------------------------------------------------------------------------
# T12 -- ADVERSARIAL (real crypto): signed request returns 202
# ---------------------------------------------------------------------------


def test_signed_request_returns_202(crypto_client: TestClient) -> None:
    """Real Ed25519-signed canonical JSON payload returns 202.

    #SG-TRACE: REQ-AUTH-002 | test: T12
    """
    key = Ed25519PrivateKey.generate()
    pub_hex = (
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    )
    payload = copy.deepcopy(VALID_PAYLOAD)
    canonical_bytes = canonical_json(payload)
    sig_hex = key.sign(canonical_bytes).hex()

    response = crypto_client.post(
        "/v1/signals",
        content=canonical_bytes,
        headers={
            "Content-Type": "application/json",
            "x-signature": sig_hex,
            "x-public-key": pub_hex,
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["batch_id"] == VALID_PAYLOAD["batch_id"]
    assert body["result_count"] == VALID_PAYLOAD["result_count"]


# ---------------------------------------------------------------------------
# T13 -- ADVERSARIAL (real crypto): tampered payload returns 401
# ---------------------------------------------------------------------------


def test_tampered_payload_rejected(crypto_client: TestClient) -> None:
    """Payload tampered after signing is rejected with 401.

    Sybil probe adversarial case: fabricated feature vector after
    legitimate signing must break the signature.

    #SG-TRACE: REQ-AUTH-002 | test: T13
    #SG-TRACE: REQ-PRIV-002 (Sybil resistance, partial)
    """
    key = Ed25519PrivateKey.generate()
    pub_hex = (
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    )

    original_payload = copy.deepcopy(VALID_PAYLOAD)
    sig_hex = key.sign(canonical_json(original_payload)).hex()

    tampered_payload = copy.deepcopy(VALID_PAYLOAD)
    tampered_payload["metrics"]["json_success_rate"] = 0.10  # was 0.95
    tampered_bytes = canonical_json(tampered_payload)

    response = crypto_client.post(
        "/v1/signals",
        content=tampered_bytes,
        headers={
            "Content-Type": "application/json",
            "x-signature": sig_hex,
            "x-public-key": pub_hex,
        },
    )
    assert response.status_code == 401, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "signature_verification_failed"
