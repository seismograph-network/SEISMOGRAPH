# KEYSTONE REPORT — SESSION 010
# Task: P2-005 OTel and MCP Adapters
# Date: 2026-06-12
# Director sign-off: Tatiana (pending)

---

## 1. Provenance

| File | Origin | Human-edited post-generation |
|---|---|---|
| `probe/adapters/__init__.py` | AI-generated | No |
| `probe/adapters/otel.py` | AI-generated | Fix: `is not None` guard (D30) |
| `probe/adapters/mcp.py` | AI-generated | No |
| `tests/test_adapters.py` | AI-generated | Fix: `ProbeConfig` field names (D29) |
| `pyproject.toml` | AI-edited (dep added) | Rewrite via heredoc after Edit truncation |

All writes performed via `python3 -B - << 'SCRIPT_EOF'` bash heredoc
per RULE-1.  The Edit tool caused one TOML truncation (see D31);
corrected by full heredoc rewrite.

---

## 2. Verification Summary

| Stage | Tool | Result |
|---|---|---|
| Style + imports | `ruff check --fix probe/ tests/ engine/ gateway/` | 0 violations |
| Formatting | `ruff format --check` (31 files) | 31 files formatted |
| Unit tests | `pytest -q` | **75/75 passed** |
| Adapter-only | `pytest tests/test_adapters.py -v` | 12/12 passed |

Test labels:

OTel processor (OT1–OT5+2):
  OT1  test_otel_on_end_adds_canary_result
  OT2  test_otel_non_genai_span_skipped
  OT3  test_otel_model_tuple_constructed
  OT3b test_model_tuple_from_attrs_missing_system
  OT4  test_otel_output_length
  OT5  test_otel_latency_ms_computed
  OT5b test_otel_latency_ms_none_times

MCP tool (MC1–MC5):
  MC1  test_mcp_check_model_weather_stable
  MC2  test_mcp_check_model_weather_drifting
  MC3  test_mcp_check_model_weather_unknown
  MC4  test_mcp_check_model_weather_http_error
  MC5  test_mcp_parse_weather_list_missing_optionals

Regression: all 63 prior tests continue to pass (75 - 12 = 63).

---

## 3. Defects Caught and Fixed

**D29 — Wrong `ProbeConfig` keyword in `tests/test_adapters.py`**
  Generated `gateway_url="http://test-gateway"` but the field is
  `gateway_endpoint` (full URL including path).  Also missing required
  `suite_version_hash` field.
  Caught by: pytest ERROR at fixture setup on first run.
  Fix: replaced with `gateway_endpoint="http://test-gateway/v1/signals"`
  and added `suite_version_hash="sha256-test-otel-adapter"`.

**D30 — Falsy zero check in `probe/adapters/otel.py` `on_end()`**
  `if span.end_time and span.start_time:` evaluates False when
  `start_time == 0` (a valid monotonic-clock value), producing
  `latency_ms == -1` instead of the correct computed value.
  Caught by: OT5 `test_otel_latency_ms_computed` (start_ns=0, end=750ms).
  Fix: changed guard to `if span.end_time is not None and span.start_time
  is not None:`.

**D31 — Edit tool truncated `pyproject.toml`**
  Edit tool wrote only through "indent-styl" (53 chars into line 53),
  leaving the TOML file unparseable.  Ruff refused to run.
  Caught by: `ruff check` exit code 2 immediately after the Edit call.
  Fix: full file rewrite via bash heredoc; added `opentelemetry-sdk>=1.20`
  to dependencies list.

---

## 4. Known Limitations

**KNOWN-LIMIT-007** — `SeismographSpanProcessor.on_end()` calls
  `Aggregator.add_result()` which is not thread-safe in Phase 0.
  For multi-threaded TracerProviders, a per-thread SDK instance or
  external locking is required.  Deferred to Phase 2.

**KNOWN-LIMIT-008** — `run_mcp_server()` in `probe/adapters/mcp.py`
  uses a blocking stdin loop with no timeout or graceful shutdown signal.
  Production deployments should wrap it with the official Anthropic MCP
  Python SDK (which handles SSE transport, lifecycle, and multi-tool
  routing).  Documented in the module docstring.

**KNOWN-LIMIT-009** — `check_model_weather()` fetches the full
  `GET /v1/weather` list and filters client-side.  At Phase 2 cardinality
  this is negligible.  Phase 3 should add a `?model_tuple=` query param
  to the gateway endpoint to avoid over-fetching at scale.

**KNOWN-LIMIT-010** — `SeismographSpanProcessor` uses `SUITE_VERSION`
  from `probe/canary.py` (currently "v1.0.0") for all staged results,
  regardless of which canary suite the parent application actually ran.
  This is correct for Phase 0 passive tapping but will need a
  configurable suite_version parameter in Phase 1.

---

## 5. Accountability Statement

"I have reviewed the Keystone Report for P2-005 (OTel and MCP Adapters).
The two adapters, 12 unit tests, and all documented defects and known
limitations are accurate.  The 75/75 test pass rate and 0 ruff violations
are confirmed.  I approve the work as described."

Signed: Tatiana _____________________ Date: ___________

---

## 6. Methodology Note

Defect D30 (falsy zero) is a recurring Python pitfall with monotonic
timestamps and counters.  Add a project-wide linting rule or code-review
checklist item: **"Prefer `is not None` over truthiness checks for any
numeric variable that may legitimately be zero."**  Consider a ruff
custom rule or a team note in `SEISMOGRAPH_Architecture.md` under
"Python idioms to avoid."
