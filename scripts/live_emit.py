"""
scripts/live_emit.py
====================
End-to-end LIVE emission: run the canary suite against a real
OpenAI-compatible endpoint, aggregate the results into a DP-noised,
Ed25519-signed SignalBatch, and POST it to a running gateway's
``/v1/signals`` endpoint so the public "model weather" dashboard shows a
REAL model tuple instead of demo data.

This is Track 1b: the first time a live probe result travels the full
privacy + signing + ingestion path end to end.

Pipeline:
  execute_canary(mock=False, provider) -> [CanaryResult]
    -> Aggregator.add_result / .flush  (clamp + Laplace DP noise, eps=2.0)
    -> SignalBatch (frozen, fleet_id=None => public network path)
    -> canonical_json(payload)          (the exact signed bytes)
    -> Ed25519 sign  (probe identity key, .seismograph_id, gitignored)
    -> POST {gateway}/v1/signals  with x-signature + x-public-key headers
    -> (optional) GET {gateway}/v1/weather to show the model row

Privacy: only the DP-noised aggregate metrics, SHA-256 hashes, and counts
are transmitted. Raw prompt text and raw model output never leave the probe
perimeter and are never printed.

Configuration (environment variables):
  SEISMOGRAPH_PROBE_BASE_URL     default http://localhost:11434/v1
  SEISMOGRAPH_PROBE_API_KEY      bearer token (omit for local Ollama)
  SEISMOGRAPH_PROBE_MODEL_TUPLE  default ollama/llama3.1
  SEISMOGRAPH_PROBE_MAX_TOKENS   default 64
  SEISMOGRAPH_GATEWAY_ENDPOINT   default http://localhost:8000/v1/signals
  SEISMOGRAPH_PROBE_KEY_PATH     default .seismograph_id

Example (Mistral -> local gateway):
  # terminal 1:  uvicorn gateway.main:app --port 8000
  # terminal 2:
  SEISMOGRAPH_PROBE_BASE_URL=https://api.mistral.ai/v1 \
  SEISMOGRAPH_PROBE_API_KEY=... \
  SEISMOGRAPH_PROBE_MODEL_TUPLE=mistral/mistral-small-latest \
  python scripts/live_emit.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Make the repository root importable when run directly as a script.
# #SG-TRACE: REQ-CANARY-024
# #   | assumption: repo root is the parent directory of scripts/
# #   | test: tests/test_live_emit.py imports the build helper offline
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from probe.canary import execute_canary  # noqa: E402
from probe.crypto import (  # noqa: E402
    KeyManager,
    canonical_json,
    sign_payload,
)
from probe.privacy import Aggregator  # noqa: E402
from probe.providers import (  # noqa: E402
    OpenAICompatibleProvider,
    ProviderError,
)


def build_signed_request(
    results: list,
    model_tuple: str,
    key_path: str | Path,
) -> tuple[bytes, dict, dict]:
    """Aggregate canary results into a signed, ready-to-POST request.

    Pure and network-free so it is unit-testable offline: takes already
    executed CanaryResults, returns the exact request body bytes, the HTTP
    headers (signature + public key), and the plain payload dict (for
    display/inspection only).

    The body bytes are the canonical JSON that the signature is computed
    over -- the gateway verifies the signature against the raw body, so the
    two MUST be byte-identical.

    #SG-TRACE: REQ-AUTH-002
    #   | assumption: gateway verifies Ed25519 sig over the raw request body
    #     which equals canonical_json(payload)
    #   | test: test_live_emit_round_trip_accepts_and_shows_model
    #SG-TRACE: REQ-PRIV-020
    #   | assumption: only DP-noised aggregates + hashes leave the probe;
    #     no raw output is present on the SignalBatch
    #   | test: test_live_emit_payload_has_no_raw_output
    """
    aggregator = Aggregator()
    for result in results:
        aggregator.add_result(result)
    # fleet_id=None => public network path (subject to quorum gating).
    batch = aggregator.flush(model_tuple, fleet_id=None)

    payload = batch.to_dict()
    body = canonical_json(payload)

    key_manager = KeyManager(Path(key_path))
    signature_hex = sign_payload(payload, key_manager.private_key)
    headers = {
        "Content-Type": "application/json",
        "x-signature": signature_hex,
        "x-public-key": key_manager.public_key_hex,
    }
    return body, headers, payload


def _post(endpoint: str, body: bytes, headers: dict, timeout: float) -> dict:
    """POST signed body to the gateway; return the decoded JSON response."""
    req = urllib.request.Request(
        endpoint, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"gateway HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"gateway unreachable: {exc.reason} -- is uvicorn running?"
        ) from None


def _weather_for(base: str, model_tuple: str, timeout: float) -> dict | None:
    """GET {base}/v1/weather and return the row for *model_tuple*, if any."""
    url = base.rstrip("/") + "/v1/weather"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    for row in rows:
        if row.get("model_tuple") == model_tuple:
            return row
    return None


def main() -> int:
    base_url = os.environ.get(
        "SEISMOGRAPH_PROBE_BASE_URL", "http://localhost:11434/v1"
    )
    api_key = os.environ.get("SEISMOGRAPH_PROBE_API_KEY") or None
    model_tuple = os.environ.get(
        "SEISMOGRAPH_PROBE_MODEL_TUPLE", "ollama/llama3.1"
    )
    max_tokens = int(os.environ.get("SEISMOGRAPH_PROBE_MAX_TOKENS", "64"))
    gateway = os.environ.get(
        "SEISMOGRAPH_GATEWAY_ENDPOINT", "http://localhost:8000/v1/signals"
    )
    key_path = os.environ.get("SEISMOGRAPH_PROBE_KEY_PATH", ".seismograph_id")
    gateway_base = gateway.split("/v1/signals")[0]

    print(f"Probing {model_tuple} via {base_url} ...")
    try:
        provider = OpenAICompatibleProvider(
            base_url=base_url, api_key=api_key, max_tokens=max_tokens
        )
        results = execute_canary(model_tuple, mock=False, provider=provider)
    except ProviderError as exc:
        print(f"Provider call failed: {exc}", file=sys.stderr)
        return 1

    body, headers, payload = build_signed_request(
        results, model_tuple, key_path
    )
    print(
        f"Built signed SignalBatch: {len(body)} bytes, "
        f"key {headers['x-public-key'][:12]}..."
    )
    print(
        "  DP-noised metrics: "
        f"avg_output_length={payload['metrics']['avg_output_length']:.1f}, "
        f"json_success_rate={payload['metrics']['json_success_rate']:.3f}, "
        f"result_count={payload['result_count']}"
    )

    print(f"POST {gateway} ...")
    try:
        resp = _post(gateway, body, headers, timeout=30.0)
    except RuntimeError as exc:
        print(f"Emission failed: {exc}", file=sys.stderr)
        return 1
    print(f"  -> {resp.get('status')} batch_id={resp.get('batch_id')}")

    row = _weather_for(gateway_base, model_tuple, timeout=10.0)
    if row is not None:
        print(
            f"\nDashboard now shows REAL model {model_tuple}: "
            f"status={row.get('status')}, "
            f"avg_len={row.get('recent_avg_output_length')}, "
            f"json_rate={row.get('recent_json_success_rate')}"
        )
        print(f"Open the dashboard: {gateway_base}/dashboard")
    print(
        "\nPrivacy: only DP-noised aggregates, SHA-256 hashes and counts "
        "were transmitted -- no raw output."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
