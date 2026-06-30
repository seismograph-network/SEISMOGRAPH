"""
tests.test_live_emit
====================
Integration tests for Track 1b: the live-emission path that takes canary
results, builds a DP-noised, Ed25519-signed SignalBatch, and POSTs it to the
gateway so the dashboard surfaces a REAL model tuple.

These exercise scripts.live_emit.build_signed_request end-to-end against the
real FastAPI app via TestClient (real Ed25519, no signature mock) and the
in-memory SQLite repo provided by the autouse conftest fixture.

Adversarial coverage:
  - a tampered/forged signature on an otherwise valid body is rejected 401
    with no ingestion (Sybil / unsigned-batch gateway case).
  - the signed body carries only DP-noised aggregates + SHA-256 hashes --
    never raw model output (privacy invariant on the wire).

#SG-TRACE: REQ-AUTH-002 | test: live_emit round-trip 202 + weather
#SG-TRACE: REQ-PRIV-020 | test: live_emit signed body has no raw output
#SG-TRACE: REQ-GW-002   | test: live_emit forged signature -> 401
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from gateway.main import app
from probe.canary import execute_canary
from scripts.live_emit import build_signed_request

MODEL_TUPLE = "mistral/mistral-small-latest"

# SignalBatch top-level fields that may appear on the wire. No raw text.
_ALLOWED_TOP_KEYS = {
    "batch_id",
    "client_id",
    "window_start",
    "window_end",
    "model_tuple",
    "suite_version",
    "metrics",
    "canary_hashes",
    "result_count",
    "fleet_id",
}
_ALLOWED_METRIC_KEYS = {
    "avg_output_length",
    "json_success_rate",
    "result_count",
}


def _results():
    """Three deterministic mock CanaryResults (no network, no provider)."""
    return execute_canary(MODEL_TUPLE, mock=True)


def test_live_emit_round_trip_accepts_and_shows_model(tmp_path: Path) -> None:
    """Live emit -> 202 accepted -> /v1/weather shows the real model.

    Single batch, single org: no quorum, so status is STABLE -- but the
    REAL model tuple now appears on the public dashboard with its metrics.
    """
    body, headers, _ = build_signed_request(
        _results(), MODEL_TUPLE, tmp_path / ".seismograph_id"
    )
    with TestClient(app) as c:
        resp = c.post("/v1/signals", content=body, headers=headers)
        assert resp.status_code == 202, resp.text
        weather = c.get("/v1/weather")

    assert weather.status_code == 200, weather.text
    entry = next(
        (e for e in weather.json() if e["model_tuple"] == MODEL_TUPLE), None
    )
    assert entry is not None, f"{MODEL_TUPLE} not on dashboard"
    assert entry["status"] == "STABLE", entry["status"]


def test_live_emit_payload_has_no_raw_output(tmp_path: Path) -> None:
    """The signed wire payload carries only aggregates + hashes."""
    body, _, payload = build_signed_request(
        _results(), MODEL_TUPLE, tmp_path / ".seismograph_id"
    )
    assert set(payload) <= _ALLOWED_TOP_KEYS
    assert set(payload["metrics"]) <= _ALLOWED_METRIC_KEYS
    assert all(len(h) == 64 for h in payload["canary_hashes"].values())
    # No raw API-response envelope keys leak into the signed bytes.
    blob = body.decode("utf-8").lower()
    for forbidden in ("choices", "message", "content"):
        assert forbidden not in blob, forbidden


def test_live_emit_forged_signature_rejected(tmp_path: Path) -> None:
    """A forged signature over a valid body is rejected 401, no ingestion."""
    body, headers, _ = build_signed_request(
        _results(), MODEL_TUPLE, tmp_path / ".seismograph_id"
    )
    forged = dict(headers)
    forged["x-signature"] = "00" * 64  # 128 hex chars, wrong signature
    with TestClient(app) as c:
        resp = c.post("/v1/signals", content=body, headers=forged)
    assert resp.status_code == 401, resp.text
