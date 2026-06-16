"""
seismograph.probe.adapters.mcp
================================
MCP (Model Context Protocol) adapter for SEISMOGRAPH.

Exposes a ``check_model_weather`` tool that queries the SEISMOGRAPH
gateway's GET /v1/weather endpoint and returns a human-readable
drift-status string.

The adapter implements a minimal JSON-RPC 2.0 stdio server compatible
with the MCP protocol (version 2024-11-05).  No external MCP SDK
dependency is required.

Usage in .mcp.json (Claude Code / Claude Desktop)::

    {
      "seismograph": {
        "command": "python3",
        "args": ["-m", "probe.adapters.mcp"],
        "env": {
          "SEISMOGRAPH_URL": "https://your-gateway.example.com"
        }
      }
    }

#SG-TRACE: REQ-MCP-001
#   | assumption: GET /v1/weather is unauthenticated; data is
#     aggregated and anonymised (no raw prompts, no client IDs)
#   | test: test_mcp_check_model_weather_stable
#SG-TRACE: REQ-MCP-002
#   | assumption: stdio JSON-RPC 2.0 is sufficient for Phase 2;
#     Phase 3 may migrate to the official MCP Python SDK
#   | test: (integration only -- not unit-tested in Phase 2)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL: str = "http://localhost:8000"
_WEATHER_PATH: str = "/v1/weather"
_MCP_VERSION: str = "2024-11-05"
_SERVER_VERSION: str = "0.2.0"


# ---------------------------------------------------------------------------
# MCP tool schema (used for tools/list response)
# ---------------------------------------------------------------------------

TOOL_SCHEMA: dict[str, Any] = {
    "name": "check_model_weather",
    "description": (
        "Query the SEISMOGRAPH federated drift network for the current"
        " semantic drift status of an LLM model. Returns STABLE or"
        " DRIFTING with recent signal metrics."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "model_tuple": {
                "type": "string",
                "description": (
                    "Model identifier in '<provider>/<model>@<version>'"
                    " format, e.g. 'openai/gpt-4o@2025-08' or"
                    " 'anthropic/claude-3-5-sonnet@global'."
                ),
            }
        },
        "required": ["model_tuple"],
    },
}


# ---------------------------------------------------------------------------
# Local response schema (avoids gateway package import)
# ---------------------------------------------------------------------------


@dataclass
class _WeatherEntry:
    """Parsed entry from GET /v1/weather response list.

    Local copy of the relevant fields from
    gateway.schema.ModelWeatherResponse to avoid cross-package
    import coupling between probe and gateway packages.
    """

    model_tuple: str
    status: str
    recent_json_success_rate: float | None = None
    recent_avg_output_length: float | None = None


def _parse_weather_list(
    data: list[dict[str, Any]],
) -> list[_WeatherEntry]:
    """Parse the raw JSON list returned by GET /v1/weather.

    Missing optional fields default to None so the adapter is
    forward-compatible with additional gateway response fields.

    Args:
        data: Deserialised JSON list from the weather endpoint.

    Returns:
        List of _WeatherEntry objects.
    """
    return [
        _WeatherEntry(
            model_tuple=item.get("model_tuple", ""),
            status=item.get("status", "UNKNOWN"),
            recent_json_success_rate=item.get("recent_json_success_rate"),
            recent_avg_output_length=item.get("recent_avg_output_length"),
        )
        for item in data
    ]


# ---------------------------------------------------------------------------
# Core tool logic
# ---------------------------------------------------------------------------


def _format_entry(entry: _WeatherEntry) -> str:
    """Format a _WeatherEntry as a human-readable status string.

    Example output::

        "Status for openai/gpt-4o@2025-08 is DRIFTING.
         Recent JSON success rate: 84%.
         Average output length: 312.5 tokens."

    Args:
        entry: Parsed weather entry for one model_tuple.

    Returns:
        Space-joined human-readable status string.
    """
    parts: list[str] = [
        f"Status for {entry.model_tuple} is {entry.status}.",
    ]
    if entry.recent_json_success_rate is not None:
        rate_pct = round(entry.recent_json_success_rate * 100)
        parts.append(f"Recent JSON success rate: {rate_pct}%.")
    if entry.recent_avg_output_length is not None:
        parts.append(
            f"Average output length:"
            f" {entry.recent_avg_output_length:.1f} tokens.",
        )
    return " ".join(parts)


def check_model_weather(
    model_tuple: str,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    http_client: httpx.Client | None = None,
) -> str:
    """Fetch and format the drift-weather status for a model_tuple.

    Queries GET /v1/weather on the SEISMOGRAPH gateway, finds the
    entry matching model_tuple, and returns a human-readable string.

    If the model_tuple is not yet tracked by the network, returns an
    informative "No data found" message rather than raising.

    Args:
        model_tuple: e.g. "anthropic/claude-3-5-sonnet@global".
        base_url: SEISMOGRAPH gateway base URL.  Defaults to
            localhost for local development.
        http_client: Injectable httpx.Client for testing.  If None,
            a fresh client is created and closed per call.

    Returns:
        Human-readable status string.

    Raises:
        httpx.HTTPStatusError: If the gateway returns a non-2xx
            HTTP status code.
        httpx.RequestError: If the gateway is unreachable.

    #SG-TRACE: REQ-MCP-001
    #   | assumption: GET /v1/weather returns a JSON list; malformed
    #     responses will raise json.JSONDecodeError
    #   | test: test_mcp_check_model_weather_drifting
    """
    _own_client: bool = http_client is None
    client: httpx.Client = http_client or httpx.Client()
    try:
        url = base_url.rstrip("/") + _WEATHER_PATH
        response = client.get(url, timeout=10.0)
        response.raise_for_status()
        entries = _parse_weather_list(response.json())
    finally:
        if _own_client:
            client.close()

    match = next(
        (e for e in entries if e.model_tuple == model_tuple),
        None,
    )
    if match is None:
        return (
            f"No data found for {model_tuple!r} in the SEISMOGRAPH"
            " network. The model may not be tracked yet."
        )
    return _format_entry(match)


# ---------------------------------------------------------------------------
# Minimal MCP stdio server (JSON-RPC 2.0)
# ---------------------------------------------------------------------------


def _rpc_ok(req_id: Any, result: Any) -> str:
    """Serialise a successful JSON-RPC 2.0 response."""
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_err(req_id: Any, code: int, message: str) -> str:
    """Serialise a JSON-RPC 2.0 error response."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
    )


def run_mcp_server(base_url: str = _DEFAULT_BASE_URL) -> None:
    """Run a blocking stdio MCP server.

    Reads JSON-RPC 2.0 messages from stdin (one per line) and
    writes responses to stdout.  Handles MCP methods:
    ``initialize``, ``notifications/initialized``,
    ``tools/list``, ``tools/call``.

    Args:
        base_url: SEISMOGRAPH gateway URL forwarded to
            check_model_weather().

    #SG-TRACE: REQ-MCP-002
    #   | assumption: single-tool server; production deployment
    #     should use the official MCP Python SDK for multi-tool
    #     servers and SSE transport
    #   | test: (integration only -- not unit-tested in Phase 2)
    """
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.stdout.write(
                _rpc_err(None, -32700, f"Parse error: {exc}") + "\n"
            )
            sys.stdout.flush()
            continue

        req_id = req.get("id")
        method: str = req.get("method", "")

        if method == "initialize":
            result: Any = {
                "protocolVersion": _MCP_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "seismograph",
                    "version": _SERVER_VERSION,
                },
            }
        elif method == "notifications/initialized":
            # Notification -- no response per MCP spec.
            continue
        elif method == "tools/list":
            result = {"tools": [TOOL_SCHEMA]}
        elif method == "tools/call":
            params = req.get("params", {})
            tool_name: str = params.get("name", "")
            tool_args: dict[str, Any] = params.get("arguments", {})
            if tool_name != "check_model_weather":
                sys.stdout.write(
                    _rpc_err(
                        req_id,
                        -32601,
                        f"Unknown tool: {tool_name!r}",
                    )
                    + "\n"
                )
                sys.stdout.flush()
                continue
            mt: str = tool_args.get("model_tuple", "")
            try:
                text = check_model_weather(mt, base_url=base_url)
                result = {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                }
            except Exception as exc:
                result = {
                    "content": [
                        {"type": "text", "text": f"Error: {exc}"},
                    ],
                    "isError": True,
                }
        else:
            sys.stdout.write(
                _rpc_err(req_id, -32601, f"Method not found: {method}") + "\n"
            )
            sys.stdout.flush()
            continue

        sys.stdout.write(_rpc_ok(req_id, result) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    _base_url = os.environ.get("SEISMOGRAPH_URL", _DEFAULT_BASE_URL)
    run_mcp_server(base_url=_base_url)
