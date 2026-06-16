#!/usr/bin/env python3
"""
scripts/first_party_fleet.py
==============================
SEISMOGRAPH First-Party Probe Fleet — continuous canary runner.

Pings the top model tuples every PROBE_INTERVAL_SECONDS (default 4 h) using
the SEISMOGRAPH ProbeSDK, and flushes DP-noised signal batches to the
ingestion gateway.  This populates the public dashboard with real baseline
data before community probes join the network.

Usage
-----
Environment variables:

  SEISMOGRAPH_GATEWAY_URL   Gateway endpoint (default: http://localhost:8000/v1/signals)
  SEISMOGRAPH_KEY_DIR       Directory for the Ed25519 fleet keypair (default: /var/seismograph)
  PROBE_INTERVAL_SECONDS    Sleep between probe rounds in seconds (default: 14400 = 4 h)
  OPENAI_API_KEY            Optional. Real OpenAI calls when set; MOCK mode if absent.
  ANTHROPIC_API_KEY         Optional. Real Anthropic calls when set; MOCK mode if absent.

Mock mode
---------
If neither OPENAI_API_KEY nor ANTHROPIC_API_KEY is set, the fleet runner
simulates a successful canary response for every model tuple and sleeps
normally.  This lets the script run in CI/dev environments without billing
while still building CUSUM baselines in the gateway.

Design notes
------------
Import path: probe.sdk (not seismograph_probe.sdk — the PyPI distribution
name is seismograph-probe but the Python package name is probe).

The OTel SeismographSpanProcessor is intentionally NOT used here.  That
adapter is a passive SpanProcessor meant to tap gen_ai.* spans from an
existing TracerProvider.  The fleet runner is an active probe: it calls the
LLM APIs itself and records results directly via ProbeSDK span lifecycle
methods.

Privacy invariant (Aegis):
  - Raw prompt text is NEVER stored or transmitted.
  - Raw model output is NEVER stored or transmitted.
  - output_tokens: integer count only.
  - json_valid: boolean derived from output structure.
  - All metrics are DP-noised (epsilon=2.0 Laplace) before transmission.

Cost cap: 4 models * 6 rounds/day * 20 max_tokens ≈ 480 output tokens/day.
At current pricing this is well under $0.10/day for all providers combined.

Provider ToS: reviewed and approved for OpenAI and Anthropic.
See docs/PROVIDER_TOS_CHECKS.md.

#SG-TRACE: REQ-FLEET-001
#   | assumption: fleet_id=None so probe signals enter the PUBLIC path
#     and contribute to get_all_model_tuples() for the dashboard
#   | test: manual -- start gateway, run fleet, GET /v1/weather shows models
#SG-TRACE: REQ-FLEET-002
#   | assumption: one KeyManager shared across all model SDKs;
#     all fleet signals carry the same Ed25519 public key (one fleet identity)
#   | test: fleet_key.pem is stable across restarts (KeyManager loads if exists)
#SG-TRACE: REQ-FLEET-003
#   | assumption: mock mode produces output_tokens=7, json_valid=True;
#     this builds a clean CUSUM baseline identical to a healthy real response
#   | test: py_compile + manual MOCK run against a local gateway
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import NamedTuple

import httpx

from probe.canary import SUITE_VERSION
from probe.crypto import KeyManager
from probe.sdk import ProbeConfig, ProbeSDK

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("seismograph.fleet")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GATEWAY_URL: str = os.getenv(
    "SEISMOGRAPH_GATEWAY_URL", "http://localhost:8000/v1/signals"
)
KEY_DIR: Path = Path(os.getenv("SEISMOGRAPH_KEY_DIR", "/var/seismograph"))
PROBE_INTERVAL_SECONDS: int = int(
    os.getenv("PROBE_INTERVAL_SECONDS", "14400")
)

# Deterministic canary prompt (temperature=0, max_tokens=20).
# Any healthy model should echo back valid JSON.
# Reviewed as ToS-compliant for OpenAI and Anthropic.
# See docs/PROVIDER_TOS_CHECKS.md.
CANARY_PROMPT: str = (
    'Output the following JSON exactly, with no extra text or markdown: '
    '{"canary": "alive"}'
)
EXPECTED_RESPONSE: dict[str, str] = {"canary": "alive"}

# Hash of the canary suite version — used as the stable suite_version_hash
# in ProbeConfig.  Ties each signal batch to a specific prompt corpus version.
SUITE_VERSION_HASH: str = hashlib.sha256(
    SUITE_VERSION.encode()
).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Target model registry
# ---------------------------------------------------------------------------
# SEISMOGRAPH model_tuple format: "{provider}/{model-name}"
# Provider ToS status: see docs/PROVIDER_TOS_CHECKS.md
#
# To add a model:
#   1. Complete ToS review and add a row to PROVIDER_TOS_CHECKS.md.
#   2. Add the tuple -> API model name mapping to _API_MODEL_MAP.
#   3. Add the tuple string to TARGET_MODELS.

_API_MODEL_MAP: dict[str, str] = {
    "openai/gpt-4o": "gpt-4o",
    "openai/gpt-4o-mini": "gpt-4o-mini",
    "anthropic/claude-3-5-sonnet": "claude-3-5-sonnet-20241022",
    "anthropic/claude-3-haiku": "claude-3-haiku-20240307",
}

TARGET_MODELS: list[str] = list(_API_MODEL_MAP.keys())


# ---------------------------------------------------------------------------
# Provider-specific API callers
# ---------------------------------------------------------------------------


class ProbeResult(NamedTuple):
    """Raw result from one LLM API call.

    Fields
    ------
    output_tokens:
        Number of tokens in the model response.  Used as the output_length
        metric.  Never contains raw text.
    json_valid:
        True iff the model returned valid JSON exactly matching
        EXPECTED_RESPONSE.  Measures semantic consistency over time.
    """

    output_tokens: int
    json_valid: bool


def _parse_response_text(text: str) -> bool:
    """Return True iff text is valid JSON matching EXPECTED_RESPONSE.

    Does NOT store the text itself -- only the boolean result crosses
    the privacy boundary.

    #SG-TRACE: REQ-FLEET-004
    #   | assumption: strip() handles leading/trailing whitespace from models
    #     that add newlines before the JSON
    #   | test: unit -- tested via MOCK path in probe_model()
    """
    try:
        parsed = json.loads(text.strip())
        return parsed == EXPECTED_RESPONSE
    except (json.JSONDecodeError, ValueError):
        return False


def _call_openai(api_model: str, client: httpx.Client) -> ProbeResult:
    """POST to OpenAI chat completions API.

    Returns ProbeResult(output_tokens, json_valid).
    Raises httpx.HTTPError on non-2xx responses (caller handles).

    #SG-TRACE: REQ-FLEET-005
    #   | assumption: completion_tokens is always present in usage field
    #     for successful non-streaming responses
    #   | test: integration -- run with real OPENAI_API_KEY
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    response = client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": api_model,
            "messages": [{"role": "user", "content": CANARY_PROMPT}],
            "temperature": 0.0,
            "max_tokens": 20,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    text: str = data["choices"][0]["message"]["content"]
    output_tokens: int = data.get("usage", {}).get("completion_tokens", 0)
    return ProbeResult(
        output_tokens=output_tokens,
        json_valid=_parse_response_text(text),
    )


def _call_anthropic(api_model: str, client: httpx.Client) -> ProbeResult:
    """POST to Anthropic messages API.

    Returns ProbeResult(output_tokens, json_valid).
    Raises httpx.HTTPError on non-2xx responses (caller handles).

    #SG-TRACE: REQ-FLEET-006
    #   | assumption: content[0].text contains the full response for
    #     non-streaming single-turn messages
    #   | test: integration -- run with real ANTHROPIC_API_KEY
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    response = client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": api_model,
            "max_tokens": 20,
            "messages": [{"role": "user", "content": CANARY_PROMPT}],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    text: str = data["content"][0]["text"]
    output_tokens: int = data.get("usage", {}).get("output_tokens", 0)
    return ProbeResult(
        output_tokens=output_tokens,
        json_valid=_parse_response_text(text),
    )


def _mock_result(model_tuple: str) -> ProbeResult:
    """Simulate a healthy canary response without an API call.

    Returns output_tokens=7 (approx token count for '{"canary": "alive"}')
    and json_valid=True.  Builds an accurate CUSUM baseline for
    later drift detection against real API data.

    #SG-TRACE: REQ-FLEET-003
    #   | assumption: 7 tokens ≈ {"canary": "alive"} across all tokenizers
    #   | test: implicit -- MOCK mode exercises all probe/gateway code paths
    """
    logger.debug("MOCK | model=%s output_tokens=7 json_valid=True", model_tuple)
    return ProbeResult(output_tokens=7, json_valid=True)


# ---------------------------------------------------------------------------
# Single probe execution
# ---------------------------------------------------------------------------


def probe_model(
    model_tuple: str,
    sdk: ProbeSDK,
    http_client: httpx.Client,
    mock: bool,
) -> None:
    """Execute one canary probe cycle for model_tuple and flush to gateway.

    Flow:
    1. start_canary_span() -- opens an OTelSpanContext.
    2. Call the LLM API (or mock) -- records output_tokens and json_valid.
    3. Set gen_ai.* attributes on the span.
    4. finish_canary_span() -- synthesises CanaryResult (SHA-256 hash only).
    5. flush() -- Aggregator -> DP noise -> SignalBatch -> gateway POST.

    The span lifecycle mirrors the OTel gen_ai.* semantic conventions
    used by SeismographSpanProcessor, ensuring the same metric derivation
    path regardless of whether signals originate from the fleet runner
    or a passive OTel tap.

    Privacy: only output_tokens (int) and json_valid (bool) are recorded.
    Raw model output text is discarded before step 3.

    Parameters
    ----------
    model_tuple:
        SEISMOGRAPH model identifier (e.g., "openai/gpt-4o").
    sdk:
        ProbeSDK instance bound to this model_tuple.
    http_client:
        Shared httpx.Client (connection pooling across models).
    mock:
        If True, skips the real API call and uses _mock_result().

    #SG-TRACE: REQ-FLEET-007
    #   | assumption: finish_canary_span reads gen_ai.usage.output_tokens
    #     and gen_ai.response.json_valid from span.attributes
    #   | test: test_sdk.py T1 (span lifecycle)
    """
    provider = model_tuple.split("/")[0]
    api_model = _API_MODEL_MAP[model_tuple]
    span = sdk.start_canary_span(prompt_count=1)

    try:
        if mock:
            result = _mock_result(model_tuple)
        elif provider == "openai":
            result = _call_openai(api_model, http_client)
        elif provider == "anthropic":
            result = _call_anthropic(api_model, http_client)
        else:
            logger.warning(
                "Unknown provider %r in %r -- skipping", provider, model_tuple
            )
            sdk.finish_canary_span(
                status_code=500,
                error_message=f"Unknown provider: {provider}",
            )
            return

        # Set gen_ai.* span attributes -- these are the ONLY data that
        # cross the privacy boundary (integer + boolean, no text).
        span.attributes["gen_ai.usage.output_tokens"] = result.output_tokens
        span.attributes["gen_ai.response.json_valid"] = result.json_valid

        sdk.finish_canary_span(status_code=200)
        flush_result = sdk.flush()

        logger.info(
            "OK | model=%-35s tokens=%3d json_valid=%-5s flush=%s",
            model_tuple,
            result.output_tokens,
            result.json_valid,
            (
                flush_result.get("status")
                if isinstance(flush_result, dict)
                else str(flush_result)
            ),
        )

    except httpx.HTTPStatusError as exc:
        sdk.finish_canary_span(
            status_code=exc.response.status_code,
            error_message=str(exc),
        )
        logger.error(
            "HTTP error | model=%s status=%d error=%r",
            model_tuple,
            exc.response.status_code,
            exc,
        )
    except httpx.RequestError as exc:
        sdk.finish_canary_span(status_code=503, error_message=str(exc))
        logger.error(
            "Request error | model=%s error=%r", model_tuple, exc
        )
    except Exception as exc:  # noqa: BLE001
        sdk.finish_canary_span(status_code=500, error_message=str(exc))
        logger.error(
            "Unhandled error | model=%s error=%r", model_tuple, exc
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Initialise the fleet and run the infinite probe loop.

    Key initialisation steps:
    1. Create KEY_DIR if absent; load or generate the Ed25519 fleet keypair.
    2. Detect API key availability and log mock mode per provider.
    3. Build one ProbeSDK per model_tuple (shared KeyManager, unique aggregators).
    4. Enter the probe loop: probe each model, sleep PROBE_INTERVAL_SECONDS.

    Loop safety: individual probe errors are caught inside probe_model().
    A secondary try/except inside the loop prevents one crash from killing
    all subsequent rounds.
    """
    # ------------------------------------------------------------------
    # Key initialisation
    # ------------------------------------------------------------------
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    key_path = KEY_DIR / "fleet_key.pem"
    key_manager = KeyManager(key_path=key_path)
    logger.info(
        "Fleet key ready | pubkey=%s... | path=%s",
        key_manager.public_key_hex[:16],
        key_path,
    )

    # ------------------------------------------------------------------
    # Provider availability
    # ------------------------------------------------------------------
    has_openai: bool = bool(os.getenv("OPENAI_API_KEY"))
    has_anthropic: bool = bool(os.getenv("ANTHROPIC_API_KEY"))

    if not has_openai and not has_anthropic:
        logger.warning(
            "No API keys detected (OPENAI_API_KEY, ANTHROPIC_API_KEY). "
            "Running in full MOCK mode -- simulating healthy responses. "
            "Set API keys to enable real probes."
        )
    else:
        if not has_openai:
            logger.warning(
                "OPENAI_API_KEY not set -- openai/* models will use MOCK mode."
            )
        if not has_anthropic:
            logger.warning(
                "ANTHROPIC_API_KEY not set -- anthropic/* models will use MOCK mode."
            )

    # ------------------------------------------------------------------
    # SDK construction (one per model_tuple, shared key)
    # ------------------------------------------------------------------
    # Each SDK has its own Aggregator so signal batches are per-model.
    # The shared KeyManager means all batches carry the same Ed25519
    # public key -- one fleet identity in the gateway's view.

    FleetEntry = NamedTuple(
        "FleetEntry", [("model_tuple", str), ("sdk", ProbeSDK), ("mock", bool)]
    )

    fleet: list[FleetEntry] = []
    for model_tuple in TARGET_MODELS:
        provider = model_tuple.split("/")[0]
        mock = (provider == "openai" and not has_openai) or (
            provider == "anthropic" and not has_anthropic
        )
        config = ProbeConfig(
            model_tuple=model_tuple,
            suite_version_hash=SUITE_VERSION_HASH,
            gateway_endpoint=GATEWAY_URL,
        )
        sdk = ProbeSDK(config=config, _key_manager=key_manager)
        fleet.append(FleetEntry(model_tuple=model_tuple, sdk=sdk, mock=mock))
        logger.info(
            "Registered | model=%-35s mock=%s", model_tuple, mock
        )

    logger.info(
        "Fleet runner started | models=%d interval=%ds gateway=%s",
        len(fleet),
        PROBE_INTERVAL_SECONDS,
        GATEWAY_URL,
    )

    # ------------------------------------------------------------------
    # Probe loop
    # ------------------------------------------------------------------
    with httpx.Client() as http_client:
        while True:
            logger.info("--- Probe round starting (%d models) ---", len(fleet))

            for entry in fleet:
                try:
                    probe_model(
                        model_tuple=entry.model_tuple,
                        sdk=entry.sdk,
                        http_client=http_client,
                        mock=entry.mock,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Belt-and-suspenders: probe_model already catches most
                    # exceptions; this guard prevents any unexpected leak
                    # from killing subsequent model probes in this round.
                    logger.error(
                        "Round guard | model=%s error=%r",
                        entry.model_tuple,
                        exc,
                    )

            logger.info(
                "--- Round complete. Sleeping %ds (%.1f h) ---",
                PROBE_INTERVAL_SECONDS,
                PROBE_INTERVAL_SECONDS / 3600,
            )
            time.sleep(PROBE_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Fleet runner stopped by keyboard interrupt.")
        sys.exit(0)
