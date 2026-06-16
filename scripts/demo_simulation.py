#!/usr/bin/env python3
"""
scripts/demo_simulation.py
==========================
SEISMOGRAPH End-to-End Demo Simulation.

Simulates two independent organisations discovering a silent model update
in real-time using the federated drift-detection network.

Story arc
---------
  PRE-FLIGHT   Warm up the CUSUM detector with 30 silent stable batches.
               (In production this happens naturally across hours/days.)

  PHASE 1      Both Client A (startup) and Client B (enterprise) emit
               healthy canary probe signals.  Weather: STABLE.

  PHASE 2      Client A begins seeing JSON validation failures.  CUSUM
               fires a LOCAL alert.  Dashboard stays STABLE: 1 org is
               not quorum.  A single-org signal is NEVER promoted to a
               public drift alert.

  PHASE 3      Client B observes the same degradation.  Their CUSUM fires.
               AgreementScorer has 2 distinct org IDs -> quorum reached.
               A PublicDriftAlert is written.  Dashboard -> DRIFTING.

Privacy note
------------
  Raw prompts and model outputs NEVER leave the probe perimeter.
  Only SHA-256 response hashes, distributional features, and
  Laplace DP-noised aggregates are transmitted to the gateway.

Usage
-----
  # Terminal 1 -- start gateway
  uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload

  # Terminal 2 -- run demo
  python scripts/demo_simulation.py
"""

from __future__ import annotations

# noqa: E501 -- SG-TRACE lines are allowed to exceed line-length limit.
# SG-TRACE: REQ-DEMO-001 | assumption: gateway on localhost:8000 | test: check_server_reachable  # noqa: E501
import hashlib
import json
import sys
import time
from typing import Any

import httpx
from probe.canary import CANARY_SUITE_V1
from probe.sdk import ProbeConfig, ProbeSDK

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GATEWAY_URL: str = "http://localhost:8000"
SIGNALS_ENDPOINT: str = f"{GATEWAY_URL}/v1/signals"
WEATHER_ENDPOINT: str = f"{GATEWAY_URL}/v1/weather"
MODEL_TUPLE: str = "anthropic/claude-3-5-sonnet@global"

SUITE_HASH: str = hashlib.sha256(
    json.dumps(CANARY_SUITE_V1, sort_keys=True).encode("utf-8")
).hexdigest()

SLEEP_STABLE_ROUND: float = 0.5
SLEEP_DRIFT_ROUND: float = 1.0
MAX_DRIFT_ATTEMPTS: int = 25

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_GRN = "\033[92m"
_YLW = "\033[93m"
_RED = "\033[91m"
_CYN = "\033[96m"
_BLD = "\033[1m"
_RST = "\033[0m"


def _ok(s: str) -> str:
    """Wrap s in green ANSI codes."""
    return f"{_GRN}{s}{_RST}"


def _warn(s: str) -> str:
    """Wrap s in yellow ANSI codes."""
    return f"{_YLW}{s}{_RST}"


def _err(s: str) -> str:
    """Wrap s in red ANSI codes."""
    return f"{_RED}{s}{_RST}"


def _head(s: str) -> str:
    """Wrap s in bold cyan ANSI codes."""
    return f"{_BLD}{_CYN}{s}{_RST}"


# ---------------------------------------------------------------------------
# Server reachability check
# ---------------------------------------------------------------------------


def check_server_reachable(url: str = GATEWAY_URL) -> None:
    """Verify the SEISMOGRAPH gateway is up before starting the demo.

    Exits with a helpful error message and non-zero status if the gateway
    is not reachable, rather than letting the probe SDK raise a raw
    httpx.ConnectError mid-run.

    Args:
        url: Base URL of the gateway (default: http://localhost:8000).
    """
    # SG-TRACE: REQ-DEMO-001 | assumption: /v1/weather returns 200 | test: check_server_reachable  # noqa: E501
    print(f"  Connecting to {url}/v1/weather ", end="", flush=True)
    try:
        resp = httpx.get(f"{url}/v1/weather", timeout=4.0)
        resp.raise_for_status()
    except httpx.ConnectError:
        print(_err("FAILED (connection refused)"))
        print()
        print(_err("  Gateway is not running.  Start it first:"))
        print("    uvicorn gateway.main:app --host 0.0.0.0 --port 8000")
        print()
        sys.exit(1)
    except httpx.TimeoutException:
        print(_err("FAILED (timeout after 4 s)"))
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(_err(f"FAILED (HTTP {exc.response.status_code})"))
        sys.exit(1)
    print(_ok("OK"))


# ---------------------------------------------------------------------------
# Canary round helper
# ---------------------------------------------------------------------------


def emit_round(
    sdk: ProbeSDK,
    label: str,
    *,
    json_valid: bool,
    output_tokens: int = 512,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run one canary probe cycle and flush the batch to the gateway.

    Workflow:
      1. Open an OTel-style span via sdk.start_canary_span().
      2. Set simulated gen_ai.* attributes.  No raw prompt/output stored.
      3. Close the span -- synthesises a CanaryResult with a SHA-256 hash.
      4. Flush: Aggregator applies Laplace DP noise then POSTs SignalBatch.

    Args:
        sdk:          ProbeSDK instance (one per simulated organisation).
        label:        Human-readable client name for log lines.
        json_valid:   Simulated JSON validation outcome for this round.
        output_tokens: Simulated token count (stable baseline: 512).
        verbose:      Print per-round status line when True.

    Returns:
        Parsed response dict from flush().
        Returns {} on network error so callers can continue gracefully.
    """
    # SG-TRACE: REQ-DEMO-002 | assumption: gen_ai.response.json_valid drives json_success_rate | test: test_valid_payload_returns_202  # noqa: E501
    span = sdk.start_canary_span(prompt_count=3)
    span.attributes["gen_ai.usage.output_tokens"] = output_tokens
    span.attributes["gen_ai.response.json_valid"] = json_valid
    span.attributes["gen_ai.prompt_id"] = "v1.0.0-format"
    sdk.finish_canary_span(status_code="OK")

    try:
        result = sdk.flush()
    except (httpx.ConnectError, httpx.TimeoutException, RuntimeError) as exc:
        if verbose:
            print(_err(f"    [{label}] flush error: {exc}"))
        return {}

    if verbose:
        batches: list[dict[str, Any]] = result.get("batches", [])
        if not batches:
            return result
        batch = batches[0]
        alerts: list[dict[str, Any]] = batch.get("alerts", [])
        v_str = _ok("json=True ") if json_valid else _warn("json=False")
        icon = _warn(f"  ALERT x{len(alerts)}") if alerts else _ok("  ok")
        print(f"    {icon}  [{label}]  {v_str}")
        for alert in alerts:
            metric = alert["metric_name"]
            score = alert["cusum_score"]
            direction = alert["direction"]
            print(
                f"         |-- metric={metric}"
                f"  score={score:.3f}"
                f"  dir={direction}"
            )

    return result


# ---------------------------------------------------------------------------
# Weather helpers
# ---------------------------------------------------------------------------


def get_weather() -> list[dict[str, Any]]:
    """Fetch the current drift-weather from GET /v1/weather."""
    resp = httpx.get(WEATHER_ENDPOINT, timeout=5.0)
    return list(resp.json())


def print_weather_banner(
    weather: list[dict[str, Any]], model_tuple: str
) -> None:
    """Pretty-print the weather status for one model tuple.

    Args:
        weather:     Response list from get_weather().
        model_tuple: The model tuple to display.
    """
    entry = next((e for e in weather if e["model_tuple"] == model_tuple), None)
    if entry is None:
        print(f"  Weather: (no entry for {model_tuple!r} yet)")
        return
    status = entry.get("status", "UNKNOWN")
    if status == "DRIFTING":
        status_disp = _err("DRIFTING")
        icon = _err("storm")
    else:
        status_disp = _ok("STABLE")
        icon = _ok("sunny")
    last_alert = entry.get("last_alert_timestamp") or "none"
    json_rate = entry.get("recent_json_success_rate")
    rate_str = f"{json_rate:.3f}" if json_rate is not None else "n/a"
    print(
        f"  Dashboard [{icon}] -> {status_disp}"
        f" | json_rate={rate_str}"
        f" | last_alert={last_alert}"
    )


# ---------------------------------------------------------------------------
# CUSUM baseline warmup
# ---------------------------------------------------------------------------


def build_cusum_baseline(sdk: ProbeSDK, label: str, n: int = 30) -> None:
    """Send n rapid stable batches to prime the Page-CUSUM detector.

    The CUSUM algorithm cannot detect drift until it has accumulated
    baseline_samples (default: 30 in gateway) observations to estimate
    mu0 and sigma0.  In production this happens across hours/days.  For
    the demo we fast-forward silently.

    CUSUM state is per (model_tuple, metric_name) and shared across all
    client_ids.  Client A priming the baseline also primes it for B.

    Args:
        sdk:   ProbeSDK instance to send from.
        label: Human-readable label for the progress line.
        n:     Number of stable batches to send (>= baseline_samples=30).
    """
    # SG-TRACE: REQ-DEMO-003 | assumption: baseline_samples=30 | test: test_single_org_noise_blocked  # noqa: E501
    print(f"  [{label}]  {n} stable rounds ", end="", flush=True)
    for i in range(n):
        emit_round(sdk, label, json_valid=True, verbose=False)
        if (i + 1) % 10 == 0:
            print(".", end="", flush=True)
    print(f"  {_ok('done')}")


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------


def main() -> None:  # noqa: PLR0912, PLR0915
    """Run the SEISMOGRAPH end-to-end demo simulation."""
    border = "=" * 67
    thin = "-" * 67

    print()
    print(_head(border))
    print(_head("  SEISMOGRAPH  --  End-to-End Demo Simulation"))
    print(_head("  Two orgs. One silent update. Quorum-gated detection."))
    print(_head(border))
    print()
    print(f"  Model:   {MODEL_TUPLE}")
    print(f"  Gateway: {GATEWAY_URL}")
    print("  Quorum:  >= 2 distinct organisations required for public alert")
    print()

    check_server_reachable()
    print()

    # Instantiate two independent probe clients.
    # Each ProbeSDK wraps its own Aggregator which generates a unique
    # client_id (UUID4) at init time.  The gateway AgreementScorer
    # tracks these as distinct contributing orgs for quorum evaluation.
    config_a = ProbeConfig(
        model_tuple=MODEL_TUPLE,
        suite_version_hash=SUITE_HASH,
        gateway_endpoint=SIGNALS_ENDPOINT,
    )
    config_b = ProbeConfig(
        model_tuple=MODEL_TUPLE,
        suite_version_hash=SUITE_HASH,
        gateway_endpoint=SIGNALS_ENDPOINT,
    )
    sdk_a = ProbeSDK(config_a)  # Client A -- fictional startup
    sdk_b = ProbeSDK(config_b)  # Client B -- fictional enterprise

    # ------------------------------------------------------------------
    # PRE-FLIGHT: CUSUM baseline warmup
    #
    # Page-CUSUM needs baseline_samples=30 stable observations before
    # drift detection activates.  We fast-forward this silently.
    # ------------------------------------------------------------------
    print(thin)
    print(_head("PRE-FLIGHT -- CUSUM Baseline Warmup"))
    print(thin)
    print("  Page-CUSUM needs 30+ stable samples to estimate mu0/sigma0.")
    print("  In production this happens across hours or days.")
    print("  Fast-forwarding...")
    print()
    build_cusum_baseline(sdk_a, "Client A (startup)", n=30)
    print()

    # ------------------------------------------------------------------
    # PHASE 1 -- The Stable Baseline  (t = 0 s to 10 s)
    # ------------------------------------------------------------------
    print(thin)
    print(_head("PHASE 1 -- The Stable Baseline  [t = 0 s to 10 s]"))
    print(thin)
    print("  Both orgs emit healthy probe signals.")
    print("  Expected: no alerts, weather STABLE.")
    print()

    for i in range(5):
        print(f"  Round {i + 1}/5:")
        emit_round(sdk_a, "Client A (startup)", json_valid=True)
        emit_round(sdk_b, "Client B (enterprise)", json_valid=True)
        time.sleep(SLEEP_STABLE_ROUND)

    print()
    weather = get_weather()
    print_weather_banner(weather, MODEL_TUPLE)
    print()

    # ------------------------------------------------------------------
    # PHASE 2 -- Client A detects silent degradation  (t = 10 s to 20 s)
    #
    # The model silently changed.  Client A JSON extraction probes fail.
    # CUSUM fires a LOCAL alert.  The public dashboard stays STABLE:
    # one org is not quorum.  This is the core safety property.
    #
    # A single-org signal could be a probe bug, network hiccup, or a
    # Sybil probe injecting false signals.  The quorum gate filters all.
    # ------------------------------------------------------------------
    print(thin)
    print(_head("PHASE 2 -- Client A Detects Drift  [t = 10 s to 20 s]"))
    print(thin)
    print("  Client A JSON probes begin failing -- silent model change.")
    print("  CUSUM accumulates.  When it fires, AgreementScorer records")
    print("  1 contributing org.  Quorum (>= 2) NOT reached.")
    print("  Public dashboard will remain STABLE.")
    print()

    cusum_fired_a = False
    for i in range(MAX_DRIFT_ATTEMPTS):
        print(f"  Round {i + 1}/{MAX_DRIFT_ATTEMPTS}:")
        result = emit_round(sdk_a, "Client A (startup)", json_valid=False)
        time.sleep(SLEEP_DRIFT_ROUND)

        batches: list[dict[str, Any]] = result.get("batches", [])
        if batches and batches[0].get("alerts"):
            cusum_fired_a = True
            break

    print()
    if not cusum_fired_a:
        print(_warn("  NOTE: CUSUM did not fire within the attempt window."))
        print(_warn("  Laplace DP noise may have smoothed the signal."))
        print(_warn("  Restart gateway (fresh CUSUM state) and rerun demo."))
        sys.exit(1)

    print(_warn("  [!] Client A fired a LOCAL drift alert."))
    print("  AgreementScorer: 1 contributing org pending for quorum.")
    print("  Checking public weather -- should still be STABLE...")
    time.sleep(0.5)

    weather = get_weather()
    print_weather_banner(weather, MODEL_TUPLE)

    mt_entry = next(
        (e for e in weather if e["model_tuple"] == MODEL_TUPLE), None
    )
    if mt_entry and mt_entry["status"] == "DRIFTING":
        print()
        print(_err("  UNEXPECTED: weather is DRIFTING after only 1 org."))
        print(_err("  Check quorum gate in gateway/main.py."))
        sys.exit(1)
    else:
        print(_ok("  [+] Quorum gate is holding -- dashboard still STABLE."))
    print()

    # ------------------------------------------------------------------
    # PHASE 3 -- The Quorum: Client B confirms  (t = 20 s to 30 s)
    #
    # An independent enterprise (Client B) observes the same degradation.
    # When their CUSUM fires, the AgreementScorer has 2 distinct org IDs.
    # Quorum >= 2 is reached.  PublicDriftAlert written to DB.
    # GET /v1/weather returns DRIFTING.
    #
    # Because CUSUM S- is already elevated from Client A, Client B
    # typically fires on its first or second degraded batch.
    # ------------------------------------------------------------------
    print(thin)
    print(_head("PHASE 3 -- The Quorum: Client B Confirms  [t=20s-30s]"))
    print(thin)
    print("  Client B -- an independent enterprise -- observes the same")
    print("  JSON validation failures.  When their CUSUM fires, the")
    print("  AgreementScorer has 2 distinct org IDs -> quorum reached.")
    print("  A PublicDriftAlert is written.  Dashboard -> DRIFTING.")
    print()

    quorum_reached = False
    final_weather: list[dict[str, Any]] = []
    for i in range(MAX_DRIFT_ATTEMPTS):
        print(f"  Round {i + 1}/{MAX_DRIFT_ATTEMPTS}:")
        emit_round(sdk_b, "Client B (enterprise)", json_valid=False)
        time.sleep(SLEEP_DRIFT_ROUND)

        final_weather = get_weather()
        mt_entry = next(
            (e for e in final_weather if e["model_tuple"] == MODEL_TUPLE),
            None,
        )
        if mt_entry and mt_entry["status"] == "DRIFTING":
            quorum_reached = True
            break

    print()
    print_weather_banner(final_weather, MODEL_TUPLE)
    print()

    if quorum_reached:
        print(_head(border))
        print(_err("  [DRIFT CONFIRMED] Federated quorum reached!"))
        print(_head(border))
        print()
        print(f"  Model:         {MODEL_TUPLE}")
        print("  Status:        DRIFTING (PublicDriftAlert written)")
        print("  Contributing:  >= 2 independent organisations")
        print()
        print("  Recommended actions:")
        print("    1. Pin deployments to a specific dated model version.")
        print("    2. Open the SEISMOGRAPH dashboard:")
        print(f"       {GATEWAY_URL}/")
        print("    3. Subscribe to drift webhooks (Phase 2 roadmap).")
    else:
        print(_warn("  NOTE: Quorum not reached within the attempt window."))
        print(_warn("  DP noise may have slowed Client B CUSUM convergence."))
        print(_warn("  Restart gateway for fresh CUSUM state and rerun."))

    print()
    print("Demo complete.")
    print()


if __name__ == "__main__":
    main()
