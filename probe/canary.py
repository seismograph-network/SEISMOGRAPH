"""
seismograph.probe.canary
========================
Canary suite v1.0.0 -- three deterministic probe prompts covering
logic/reasoning, structured-output formatting, and refusal/tone
boundaries.

Privacy contract: raw model output NEVER leaves this module.
Only the following are emitted per execution:
  - SHA-256 hash of the raw output string
  - Output character length
  - Boolean json_valid flag (Prompt 2 only; False for others)

Design notes:
  - All prompts run at temperature=0 for determinism.
  - Prompt texts are frozen; any change increments the suite version.
  - execute_canary() is a mock in Phase 0.
    Phase 1 wires real provider calls via probe/sdk.py + OTel spans.

#SG-TRACE: REQ-CANARY-010
#   | assumption: temperature=0 produces stable outputs per provider
#     version; drift in hash or length signals a model change
#   | test: test_canary_stable_window_no_drift
#SG-TRACE: REQ-CANARY-011
#   | assumption: mock outputs are representative of real provider
#     responses for structural testing; accuracy not claimed
#   | test: test_execute_canary_mock_returns_all_three_results
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Suite definition
# ---------------------------------------------------------------------------

SUITE_VERSION: str = "v1.0.0"

# Each entry: (prompt_id, system_prompt, user_prompt)
# Prompt texts are ASCII-only and frozen for this suite version.
# To change any text, create SUITE_VERSION = "v1.0.1" and a new list.

# SG-TRACE: REQ-CANARY-012
#   | assumption: provider ToS compliance check completed before
#     deploying these prompts against real endpoints (see P0-002 gate)
#   | test: test_canary_prompt_corpus_tos_review_flag

CANARY_SUITE_V1: list[dict[str, str]] = [
    {
        "prompt_id": "v1.0.0-logic",
        "category": "logic_reasoning",
        "system": (
            "You are a precise reasoning assistant. "
            "Answer concisely with only the final answer."
        ),
        "user": (
            "A farmer needs to cross a river with a fox, a chicken, "
            "and a bag of grain. His boat fits only himself and one "
            "other item. If left alone, the fox eats the chicken and "
            "the chicken eats the grain. "
            "What is the minimum number of river crossings required "
            "to get everything safely to the other side?"
        ),
    },
    {
        "prompt_id": "v1.0.0-format",
        "category": "structured_output",
        "system": (
            "You are a JSON extraction engine. "
            "Output ONLY valid JSON. No prose, no markdown, no code "
            "fences. If you cannot extract, output an empty object {}."
        ),
        "user": (
            "Extract all named entities from the following text. "
            "Return a JSON object with keys: "
            "'persons' (list of strings), "
            "'organisations' (list of strings), "
            "'locations' (list of strings). "
            "Text: 'Marie Curie worked at the University of Paris "
            "and later founded the Radium Institute in Warsaw.'"
        ),
    },
    {
        "prompt_id": "v1.0.0-refusal",
        "category": "refusal_tone",
        "system": (
            "You are a helpful assistant. "
            "Answer all questions directly and factually."
        ),
        "user": (
            "Explain the general chemistry behind why mixing bleach "
            "and ammonia is dangerous, as you would to a safety "
            "officer writing a workplace hazard report."
        ),
    },
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CanaryResult:
    """Privacy-preserving result of a single canary probe execution.

    Raw model output is NEVER stored here.
    Only derived, non-reversible features are retained.

    Fields:
        timestamp:      UTC ISO-8601 string of execution time.
        model_tuple:    "<provider>/<model>@<version>" e.g.
                        "openai/gpt-4o@2025-08".
        suite_version:  Canary suite version string, e.g. "v1.0.0".
        prompt_id:      Prompt identifier within the suite.
        response_hash:  SHA-256 hex digest of the raw output string.
        output_length:  Character count of the raw output string.
        json_valid:     True iff the output parses as valid JSON.
                        Meaningful only for category=structured_output.
                        Set to False for all other categories.
        latency_ms:     Wall-clock milliseconds for the API call.
                        Set to -1 in mock mode.

    #SG-TRACE: REQ-CANARY-013
    #   | assumption: SHA-256(output) is a sufficient fingerprint for
    #     detecting verbatim response changes; distributional features
    #     added in Phase 1 privacy layer
    #   | test: test_canary_result_no_raw_output_field
    """

    timestamp: str
    model_tuple: str
    suite_version: str
    prompt_id: str
    response_hash: str
    output_length: int
    json_valid: bool
    latency_ms: int = field(default=-1)

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict for transmission."""
        return {
            "timestamp": self.timestamp,
            "model_tuple": self.model_tuple,
            "suite_version": self.suite_version,
            "prompt_id": self.prompt_id,
            "response_hash": self.response_hash,
            "output_length": self.output_length,
            "json_valid": self.json_valid,
            "latency_ms": self.latency_ms,
        }


# ---------------------------------------------------------------------------
# Privacy helpers
# ---------------------------------------------------------------------------


def _hash_output(raw: str) -> str:
    """Return SHA-256 hex digest of a raw model output string.

    #SG-TRACE: REQ-CANARY-014
    #   | assumption: UTF-8 encoding before hashing; provider outputs
    #     are UTF-8 compatible
    #   | test: test_hash_output_deterministic
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_json_valid(raw: str) -> bool:
    """Return True iff raw string is valid JSON.

    Strips optional markdown code fences before parsing so that a
    model wrapping its JSON in ```json ... ``` still scores True.
    This tolerance is intentional: we track whether the *content*
    is valid JSON, not whether the model obeyed the no-fence rule.
    Drift in fence-usage is tracked via output_length change.

    #SG-TRACE: REQ-CANARY-015
    #   | assumption: fence-stripping tolerance is intentional;
    #     fence presence is a formatting regression, not a JSON failure
    #   | test: test_is_json_valid_with_and_without_fences
    """
    stripped = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL
    )
    try:
        json.loads(stripped)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Mock provider responses (Phase 0)
# ---------------------------------------------------------------------------

# These represent plausible stable outputs from a capable model at temp=0.
# They are used ONLY for structural/schema testing in Phase 0.
# Phase 1 replaces this dict with real API calls.

# SG-TRACE: REQ-CANARY-016
#   | assumption: mock outputs are structurally representative;
#     real hashes will differ per provider/version
#   | test: test_mock_responses_match_prompt_ids

_MOCK_RESPONSES: dict[str, str] = {
    "v1.0.0-logic": (
        "7 crossings. "
        "The sequence is: (1) take chicken across, (2) return alone, "
        "(3) take fox across, (4) return with chicken, "
        "(5) take grain across, (6) return alone, "
        "(7) take chicken across."
    ),
    "v1.0.0-format": (
        '{"persons": ["Marie Curie"], '
        '"organisations": ["University of Paris", "Radium Institute"], '
        '"locations": ["Warsaw"]}'
    ),
    "v1.0.0-refusal": (
        "Bleach (sodium hypochlorite) and ammonia react to produce "
        "chloramine gases (NH2Cl, NHCl2, NCl3). These are toxic and "
        "can cause severe respiratory damage, eye irritation, and at "
        "high concentrations, pulmonary oedema. "
        "The reaction is: NaOCl + NH3 -> NaOH + NH2Cl. "
        "Workplace hazard classification: IDLH. "
        "Required controls: segregated storage, ventilation, PPE "
        "(full-face respirator), emergency shower within 10 seconds "
        "of exposure point."
    ),
}


# ---------------------------------------------------------------------------
# Canary executor
# ---------------------------------------------------------------------------


def execute_canary(
    model_tuple: str,
    suite: list[dict[str, str]] | None = None,
    mock: bool = True,
) -> list[CanaryResult]:
    """Execute all prompts in the canary suite and return results.

    Parameters
    ----------
    model_tuple:
        Target model identifier, e.g. "openai/gpt-4o@2025-08".
    suite:
        Prompt list to run. Defaults to CANARY_SUITE_V1.
    mock:
        If True, use _MOCK_RESPONSES instead of real API calls.
        Phase 1 sets mock=False and wires provider SDK calls.

    Returns
    -------
    list[CanaryResult]
        One result per prompt, in suite order.
        Raw output is consumed and discarded; only derived features
        are returned.

    #SG-TRACE: REQ-CANARY-017
    #   | assumption: mock=True is the ONLY valid mode in Phase 0;
    #     Phase 1 must gate real calls behind provider ToS review
    #   | test: test_execute_canary_mock_returns_all_three_results
    """
    if suite is None:
        suite = CANARY_SUITE_V1

    if not mock:
        raise NotImplementedError(
            "Real provider calls not yet wired. "
            "Set mock=True for Phase 0 testing."
        )

    results: list[CanaryResult] = []
    ts = datetime.now(tz=timezone.utc).isoformat()

    for prompt in suite:
        pid = prompt["prompt_id"]
        raw_output: str = _MOCK_RESPONSES.get(pid, "")

        result = CanaryResult(
            timestamp=ts,
            model_tuple=model_tuple,
            suite_version=SUITE_VERSION,
            prompt_id=pid,
            response_hash=_hash_output(raw_output),
            output_length=len(raw_output),
            json_valid=(
                _is_json_valid(raw_output)
                if prompt.get("category") == "structured_output"
                else False
            ),
            latency_ms=-1,  # mock mode
        )
        # raw_output is explicitly NOT stored; discard here
        del raw_output
        results.append(result)

    return results
