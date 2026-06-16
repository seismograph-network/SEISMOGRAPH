# SEISMOGRAPH — Provider ToS Compliance Checks

**Maintained by:** Tatiana & Yehor  
**Last reviewed:** 2026-06-14  
**Standing rule:** Before pointing real probes at any new provider, complete a
row in this table and record the outcome in the relevant Keystone Report.

---

## Probe Methodology

SEISMOGRAPH probes utilise minimal, deterministic prompts (e.g., "Output the
following JSON exactly: {\"canary\": \"alive\"}") to measure API stability and
semantic degradation. This constitutes standard telemetry/observability. It
does NOT:

- Extract model weights or internal representations
- Bypass safety filters or content policies
- Systematically probe for vulnerabilities or jailbreaks
- Violate OpenAI, Anthropic, or any provider restrictions on reverse-engineering
- Store or transmit raw prompt text or raw model outputs (privacy invariant)
- Exceed the cost cap of ≤200 prompts per model per day at temperature 0

Each canary prompt is SHA-256 content-addressed in the canary suite. The probe
fleet sends at most 6 batches per model per day (one every 4 hours), at
temperature 0 and max_tokens=20. Total cost target: <$0.10/day across all
monitored model tuples.

---

## Provider Status Table

| Provider | ToS URL | Allowed? | Restriction | Reviewed | Notes |
|---|---|---|---|---|---|
| OpenAI | https://openai.com/policies/usage-policies | ✅ YES | Automated access permitted for API users; no reverse-engineering of weights | 2026-06-14 | Deterministic canary prompts are standard API usage. No safety bypass attempted. |
| Anthropic | https://www.anthropic.com/legal/aup | ✅ YES | Automated access permitted; no attempts to elicit harmful content or extract weights | 2026-06-14 | Canary prompt ("Return JSON") is benign and deterministic. Anthropic explicitly supports observability tooling. |
| Google (Gemini) | https://ai.google.dev/gemini-api/terms | ⬜ PENDING | Needs review before fleet targeting | — | Check for automated probe restrictions before adding gemini/* model tuples. |
| Mistral | https://mistral.ai/terms | ⬜ PENDING | Needs review | — | — |
| Cohere | https://cohere.com/terms-of-use | ⬜ PENDING | Needs review | — | — |

---

## Reasoning (OpenAI)

OpenAI's Usage Policies (reviewed 2026-06-14) permit:
- Automated API access for software and services
- Monitoring and observability tooling
- Deterministic test harnesses

SEISMOGRAPH probes satisfy all three. The probe does not attempt to:
- Probe the model with adversarial or harmful content
- Extract training data or system prompts via membership inference
- Systematically generate content that violates content policies

**Conclusion:** OpenAI targeting approved.

---

## Reasoning (Anthropic)

Anthropic's Acceptable Use Policy (reviewed 2026-06-14) permits:
- Building services and tools on top of the Claude API
- Automated API access for monitoring and evaluation

SEISMOGRAPH probes are lightweight semantic health checks — equivalent to a
ping for LLM outputs. No safety mechanisms are tested or bypassed.

**Conclusion:** Anthropic targeting approved.

---

## Standing Review Process

1. Before adding any new provider to `TARGET_MODELS` in
   `scripts/first_party_fleet.py`, add a row to the table above.
2. Read the ToS/AUP. Confirm no clause prohibits:
   - Automated deterministic API calls for observability
   - Measuring output consistency across time
3. Record the review date and your reasoning below the table.
4. Document the outcome in the session Keystone Report.
5. If in doubt, contact the provider's developer relations team before probing.

---

## Privacy Invariant (Aegis check)

Every signal batch transmitted by the probe fleet contains:
- `model_tuple` (public model identifier)
- `avg_output_length` (token count, DP-noised with ε=2.0 Laplace noise)
- `json_success_rate` (fraction, DP-noised)
- `result_count` (integer counter, not DP-noised)
- Canary prompt hashes (SHA-256, non-reversible)

**No raw prompt text, no raw model output, and no user data ever leaves
the probe perimeter.** This has been verified by the Aegis agent across
all probe/ code paths (tests: test_privacy.py DP1–DP10, test_sdk.py T1–T4).
