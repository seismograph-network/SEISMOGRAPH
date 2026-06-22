# SEISMOGRAPH — Provider ToS Compliance Checks

**Maintained by:** Tatiana  
**Last reviewed:** 2026-06-16  
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
| Google (Gemini) | https://ai.google.dev/gemini-api/terms | ✅ YES | No reverse-engineering; no competing model development; safety filters must not be bypassed | 2026-06-16 | SEISMOGRAPH does not develop competing models or extract weights. Deterministic API calls for observability are standard usage. Paid tier recommended for production probing (Unpaid tier allows Google to use prompts for model improvement). |
| Mistral | https://mistral.ai/terms-of-service | ✅ YES | Standard API usage permitted; users responsible for monitoring own billing | 2026-06-16 | ToS explicitly lists "testing of LLM models" as a core permitted capability. No prohibition on automated observability. |
| Cohere | https://cohere.com/terms-of-use | ✅ YES | Standard API terms; no reverse-engineering; no scraping | 2026-06-16 | Standard API access for monitoring/observability is permitted. SEISMOGRAPH probes fall within normal API usage patterns. No safety bypass or weight extraction attempted. |

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

## Reasoning (Google Gemini)

Google Gemini API Additional Terms of Service (effective 2026-03-23, reviewed 2026-06-16):

Key restrictions reviewed:
- "You may not use the Services to develop models that compete with the Services" — SEISMOGRAPH is an observability tool, not a model. Not applicable.
- "You may not attempt to reverse engineer, extract or replicate any component of the Services, including the underlying data or models" — SEISMOGRAPH only measures output distributions via DP-noised aggregates. No weight extraction. Not applicable.
- Safety features must not be bypassed — SEISMOGRAPH canary prompts are benign JSON-output requests. Not applicable.

Important note on data use: On the **Unpaid tier**, Google may use submitted prompts to improve its models. SEISMOGRAPH canary prompts are frozen and non-sensitive, so this is acceptable. However, for production fleet deployment, the **Paid tier** is recommended to benefit from the Data Processing Addendum and prevent prompt data use for model training.

**Conclusion:** Google Gemini targeting approved. Use Paid tier API key for production probing.

---

## Reasoning (Mistral)

Mistral AI Terms of Service (Sep 2023, reviewed 2026-06-16):

Key points:
- "Users may employ the Mistral AI development platform to create software applications tailored to their target user groups"
- "testing of LLM models" is explicitly listed as a core permitted capability alongside prompt engineering, embedding, and fine-tuning
- No prohibition on automated or scheduled API access
- No restriction on observability or monitoring use cases

SEISMOGRAPH canary probes are automated deterministic API calls for semantic
consistency measurement — squarely within the "testing of LLM models" capability
Mistral explicitly permits.

**Conclusion:** Mistral targeting approved.

---

## Reasoning (Cohere)

Cohere Terms of Use (reviewed 2026-06-16):

Key points reviewed:
- API access is granted for building applications and services
- No prohibition on automated deterministic API calls for monitoring
- Standard restriction: no reverse-engineering of models or extraction of weights
- Standard restriction: no systematic scraping of outputs to build competing datasets

SEISMOGRAPH probes transmit only DP-noised distributional features and SHA-256
hashes — no raw outputs stored or transmitted. This satisfies Cohere's data
handling expectations. Canary prompts are benign and deterministic.

**Conclusion:** Cohere targeting approved.

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
