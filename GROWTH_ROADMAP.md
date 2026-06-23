# SEISMOGRAPH — Post-MVP Execution & Growth Roadmap

**Prepared for:** Tatiana Radchenko
**Date:** 2026-06-12
**Posture:** Two people, no brand, no SOC2, one validated thesis (38-day backtest lead), working MVP (91/91 tests, multi-tenant, Ed25519, DP, OTel/MCP adapters, containerized).
**Doctrine:** Consulting pays the bills. Open source builds the network. The network builds the moat. In that order.

---

## 0. Brutal Truths First

- **Base rates:** A two-person infra startup attacking observability incumbents has maybe a 5–10% chance of becoming a venture-scale company. But "venture-scale" is the wrong scoreboard for the next 12 months. The realistic, winnable game: a default-alive consulting firm with a famous open-source instrument and a slowly compounding data asset. That outcome is achievable with >50% probability if you execute below.
- **The cold start may never resolve.** Plan as if it won't. Every phase below produces standalone value even if the network stalls at 50 probes.
- **Nobody buys "drift detection."** They buy *incident attribution at 2am, after they've been burned*. All marketing sells the burn, not the feature. Your buyer has already lost a weekend to "did OpenAI change something?"
- **The clone threat is misread.** Datadog doesn't clone you when you're clever; they clone you when you're a budget line item in their customers' renewal conversations. That gives you 12–24 months of incumbent indifference. That window is the entire plan. Also: they *can* clone your code in a quarter. They *cannot* clone 18 months of cross-org incident history, and they structurally cannot be neutral (see §4).
- **Your real asset today is not the network. It's the receipt:** a reproducible, seeded, one-command backtest showing a 38-day detection lead on a real provider incident. Two-person teams almost never have a falsifiable artifact at launch. You do. Lead with it.

---

## 1. Phase 1 — The "Receipts" Launch (Weeks 0–6)

**Goal:** Maximum credibility with zero network. Convert the backtest into a public, falsifiable claim that the internet argues about.

### 1.1 The backtest as the launch asset (Week 0–2)

- Polish `scripts/anthropic_backtest.py` + report into a standalone, reproducible artifact: `git clone && make backtest` → identical output, SEED=42, CUSUM trace rendered. Reproducibility IS the marketing. Every "I don't believe it" comment becomes a free demo.
- Blog post (long-form, on your own domain, not Medium): **"We detected Anthropic's Aug–Sep 2025 degradation 38 days before the postmortem — with $0.10/day of probes."** Structure: the 2am problem → the incident timeline → the CUSUM math → the reproducible command → the privacy architecture → "imagine 500 orgs doing this."
- Backtest 1–2 more public incidents (any publicly documented provider regression) before launch. One incident is an anecdote; three is a methodology.
- **Pre-empt the methodology attacks in the post itself:** synthetic data disclosure (the probe stream is synthesized from the published postmortem timeline — say it loudly, in bold, before HN says it for you), multiple-comparisons/p-hacking, baseline-window sensitivity (you already have the D9 sigma0 story — publish it as "how we almost fooled ourselves"). Honesty about D9 buys more credibility than the headline number.
- **The killer move — pre-registration:** publish your detection methodology + thresholds, hash-committed (you already content-address canary suites — use the same mechanism). From now on you publish *predictions*, not postmortems. The first time you call a drift event *before* a provider acknowledges it, you are no longer a tool. You are a source.

### 1.2 Single-player mode kills the cold start excuse (Week 1–3)

- Run your **own first-party probe fleet** against the top 10 model tuples. At ≤200 prompts/day at temperature 0, this is pocket change (<$1/day total within your cost cap). The dashboard is never empty, the network is never useless, and day-one value requires zero adoption.
- The public **"Model Weather" dashboard goes live before the blog post** — the post needs a destination that shows live data, not a waitlist.
- ToS check per provider before pointing real probes at them (standing rule; document in the Keystone Report).

### 1.3 Launch surfaces (Week 3–4)

- **Show HN**, Tue–Thu, 8–10am ET. Title is the claim, not the product: *"Show HN: We detected a silent LLM model change 38 days before the provider's postmortem."* Both of you live in the comments for 12 hours. Prepared FAQ for: privacy ("only DP-noised aggregates leave the perimeter — here's the code"), Sybil ("Ed25519 + quorum — here's the test"), "isn't this just status pages" ("status pages measure uptime; we measure *semantics* — the model can be 100% up and 16% wrong").
- Cross-post: r/MachineLearning, r/LocalLLaMA, lobste.rs, the OTel community Slack/CNCF channels (you're OTel-native — that's an entry ticket, use it).
- One launch, two fallback retries with reworked titles if it doesn't land. After three failures, the problem is the pitch, not the timing — stop and rework (see kill criteria, §5).

### 1.4 Landing page & visual assets (Week 2–3)

- **Landing page spec:** single page. Background: near-black charcoal **#0a0a0f** (matches the existing dashboard), flat matte finish with **soft, diffuse dark-studio lighting** — a single low-intensity indigo glow (#818cf8) emanating from the live seismograph trace, no harsh highlights, no gradients-as-decoration. Full-bleed, edge-to-edge composition: **explicitly NO Polaroid-style white sides, borders, or frames on any image, screenshot, or chart asset.** Hero = live Model Weather widget (real data, not a mockup), one sentence, one `pip install` command, one link to the backtest.
- **Social/OG cards:** auto-generated static PNGs per incident/alert. Same spec: #0a0a0f background, soft diffuse dark-mode lighting, indigo trace accent, full-bleed with zero white borders or Polaroid-style framing. Monospace model tuple + status + timestamp. These cards are your distribution unit — every public alert ships one.
- **No video assets anywhere in the marketing plan.** Static images, live widgets, and reproducible terminal commands only. Video is production cost you can't afford and credibility you don't need.

**Phase 1 exit criteria:** blog post published, HN front page (or 3 documented attempts), live dashboard with 10 first-party tuples, ≥500 GitHub stars, ≥3 inbound "can you look at our setup" emails. The third one matters most — it's the Phase 3 funnel igniting.

---

## 2. Phase 2 — Open Source Seeding (Months 2–6)

**Goal:** 100–500 active probes. Every tactic below is chosen because it scales with *incidents*, not with your hours.

### 2.1 Install friction is the product (Month 2, before any promotion)

- Budget: **5 minutes from `pip install seismograph-probe` to first signal on the public dashboard.** If it's 20 minutes, fix that before doing anything else in this phase. Probe = Apache-2.0 (license decision is a moat decision — see §4.2).
- Default integration = 2 lines via the existing OTel `SeismographSpanProcessor`. If they already emit `gen_ai.*` spans, they're done. That's the pitch: "you've already done the integration work; we just listen."
- Day-one payoff for the installer, before any network effect: their **private fleet view** (the fleet_id path you already built) — "your p50 output length vs. the network baseline." Selfish value first, federation second.

### 2.2 The ambulance-chasing protocol (standing, forever)

This is the core growth tactic. Drift incidents are your only true marketing events, they are free, and incumbents can't respond at your speed.

- Standing watch (automate it — you build agents for a living) on r/OpenAI, r/ClaudeAI, r/LocalLLaMA, HN, and X for "model got dumber / lazier / different" eruptions.
- **Within 6 hours of an eruption, publish your data:** either *"Confirmed — our probes show json_success_rate breaking at 14:00 UTC, here's the trace"* or *"Not confirmed — our probes are flat; it's probably your prompt/stack."* **The disconfirmations are worth more than the confirmations** — they prove you're an instrument, not a hype machine.
- Each response post ends identically: "Want your org's traffic in the next correlation? `pip install seismograph-probe`." Expect installs to arrive in incident-driven spikes, not a smooth curve. Plan around that.
- Reply directly (politely, with data) to individual devs publicly complaining about drift. This is manual, unscalable conscription. For the first 50 probes, unscalable is correct.

### 2.3 Distribution parasitism (Months 2–4)

- PRs into ecosystems with existing installed bases: **LiteLLM callbacks, LangChain instrumentation, OpenTelemetry GenAI contrib, MCP server registries** (your MCP adapter already exists — list it everywhere agents are catalogued). Goal: appear in *their* docs. Their docs outrank yours and always will.
- **README status badge:** a shields.io-style live badge — `model weather: gpt-4o ✓ STABLE` — embeddable in any repo's README. Costless viral surface; every badge is an ad with a live data feed behind it. (Badge rendering: flat dark chip on **#0a0a0f**, soft diffuse lighting, no white border or Polaroid-style framing.)
- Public alert **RSS feed + webhook** — let people consume the network for free without installing anything. Consumption precedes contribution; that ordering is fine.
- **OTel standards play (start month 2, expect 12+ months):** propose your probe signal fields as an OpenTelemetry GenAI semantic-conventions extension. Slow-burn, but every month it advances makes cloning structurally harder (§4.3).

### 2.4 What NOT to do

- **No Discord/community server until >100 weekly-active probes.** A dead Discord is public proof of a dead project.
- No paid ads, no conference sponsorships, no "DevRel content calendar." Two people. Incidents are the calendar.
- No SaaS pricing page yet. A $99/mo tier now anchors you cheap and forecloses the Phase 3 positioning.

**Phase 2 exit criteria:** ≥100 active probes across ≥30 distinct orgs, ≥2 incidents where SEISMOGRAPH published a confirmation/disconfirmation that got cited by someone else, badge in ≥20 third-party READMEs.

---

## 3. Phase 3 — High-Ticket Consulting Monetization (Months 3–9, overlaps Phase 2)

**Goal:** $150–300k booked in 9 months. SEISMOGRAPH is not for sale — it is the proprietary instrument that justifies the fee.

### 3.1 Positioning

- You do not sell a subscription. You sell **"AI Reliability Audits"** and **"Proof-of-Process" implementations**, powered by an instrument nobody else has: a cross-org drift correlation network. The network is the *secret weapon in the room*, not the SKU.
- Why consulting beats SaaS right now, mechanically: at your trust level (no SOC2, no brand), software sells for $99/mo after a 3-month procurement fight. Consulting sells for $30k *next week* against the same trust deficit, because a senior human's signature absorbs the risk the missing SOC2 creates. You are arbitraging your own credibility gap.
- **Every consulting engagement plants a probe.** In-VPC private fleet deployment (the multi-tenant fleet_id path is already built and tested — EN1–EN5). Consulting revenue and network growth are the same motion. This is how two people beat the cold start: get *paid* to install nodes.

### 3.2 The offer ladder

- **Tier 1 — Drift Exposure Audit.** Fixed scope, 2–3 weeks, **$15–30k.** Instrument the client's LLM stack with OTel GenAI conventions, deploy an in-VPC probe, build a 30-day behavioral baseline, retro-attribute their past "weird weeks" (provider drift vs. their own deploys), deliver a signed report. The Keystone Report discipline you already run *is the deliverable format* — provenance, verification, defects, limitations, accountability signature. Mid-market companies never see engineering rigor like this; it's your differentiation against generic "AI consultants."
- **Tier 2 — Proof-of-Process Implementation.** 6–10 weeks, **$40–80k.** Full canary/eval/rollback architecture: canary-gated deploys, semantic regression gates in CI, drift-aware fallback routing, runbooks. Your agentic-workflow depth is the wedge here.
- **Tier 3 — Drift Desk Retainer.** **$3–6k/month.** You watch their model tuples on the private fleet, they get attributed alerts ("provider-side, not you — here's the cross-org evidence") and a quarterly model-migration risk review. This is the bridge to the eventual SaaS: retainer clients are your future design partners and first enterprise-tier customers, already paying.

### 3.3 Targeting & funnel

- **ICP:** $10M–$500M revenue companies with LLM features *in production* and no dedicated AI-infra team — burned at least once, unable to attribute it. Verticals where drift = money or liability: legal tech, fintech ops, healthcare documentation, e-commerce search/support automation.
- **The funnel is the content:** people who comment/email on your incident posts and audit-shaped inbound from HN. No outbound until inbound is exhausted — given Phase 1 lands, it won't be for months. Each (anonymized) audit becomes a case study post, which generates the next inbound. Flywheel: incident → post → audit → probe + case study → next post.
- **Capacity discipline:** max 2 concurrent engagements. Time split 60/40 consulting/product, enforced weekly. Past 60%, the product flatlines and you become a body shop with a GitHub repo.
- **Every engagement must end with (a) a running in-VPC probe and (b) an anonymized citable result.** A client who allows neither is the wrong client — price them 2x or decline.

**Phase 3 exit criteria:** ≥4 paid engagements, ≥$150k booked, ≥3 retainer clients, every client running a probe.

---

## 4. Defensive Moat Strategy

**Accept this first: the code is not the moat and never will be.** Datadog can replicate your entire repo in one quarter. Build the three things they can't replicate:

### 4.1 The longitudinal corpus (the real moat)

- Every month of cross-org, multi-provider, content-addressed incident history is **retroactively unclonable**. A 2027 Datadog clone starts with zero history; you'll have two years of "we called it on day X" receipts. Baselines are already append-only and hash-addressed — extend the same immutability discipline to a public, citable **incident archive** with permalinks. Make the history the product people reference.
- License posture: probe code Apache-2.0; **the corpus and the public alert authority are yours.** Data licensing, not code licensing, is where the eventual enterprise value sits.

### 4.2 Structural neutrality (the moat incumbents physically can't cross)

- Datadog, New Relic, and Cloudflare are vendors, partners, and resellers of the model providers. A service whose core function is to **publicly name and shame providers for silent degradations** is channel-conflicted inside any incumbent — their partnership team kills the press release before it ships. Langfuse/Helicone see single-org data and have no neutral publication mandate.
- You can be **Switzerland**. Lean into it loudly: publish methodology, publish disconfirmations (proof you're not a hype machine), never take provider money, state the no-provider-money rule publicly so it becomes a costly signal. Target identity in 18 months: *the independent rating agency for model behavior* — cited as "according to SEISMOGRAPH data" in every silent-degradation story. **Brand-as-source is the cheapest moat available to two people.**
- Open-sourcing the probe also *commoditizes the collection layer* — exactly the layer incumbents would monetize. You poison their margin pool at the edge while keeping the correlation authority.

### 4.3 The standard (slow poison for clones)

- If your probe schema becomes the OTel GenAI semconv extension for behavioral drift signals, then every future observability tool **emits in your format** — incumbents' installed base becomes your potential ingest, not the reverse. Champion it under the OTel umbrella, not as a "SEISMOGRAPH spec." It costs evenings and patience and is the single highest-leverage defensive act available now.

### 4.4 If/when an incumbent announces a clone

- Do not panic; it validates the category and is free marketing. Same-day response post: a neutrality + history-depth comparison ("they sell to the providers they'd have to indict; here are our 2 years of timestamped calls"). Accelerate the standards play. **Never** compete on enterprise feature breadth — that's their home turf and your grave.
- **Realistic endgames, ranked by probability:** (1) durable 2-person consulting firm with a famous OSS instrument and a real data asset — most likely, and a *good* outcome; (2) acquisition by an observability incumbent for the network, corpus, and brand — keep data rights, licensing, and contributor IP scrupulously clean from day one so this door stays open; (3) the venture-scale neutral "Moody's for model behavior" — possible only if the network catches fire; don't bet the runway on it, let it surprise you.

---

## 5. Scoreboard & Kill Criteria

| Checkpoint | Threshold | If missed |
|---|---|---|
| Week 6 | HN front page (≤3 attempts) AND ≥500 stars | The pitch is broken, not the product. Stop, rework the claim, relaunch. No new code. |
| Month 4 | ≥1 inbound paid audit closed | Pricing/positioning problem. Halve Tier 1 price once; if still nothing, the ICP is wrong. |
| Month 6 | ≥100 active probes, ≥2 cited incident calls | The *network* is a feature, not a company. Pivot weight fully to consulting; keep OSS as marketing. Not a failure — a finding. |
| Month 9 | ≥$150k booked | Below this, you have a hobby with excellent test coverage. Decide deliberately: lifestyle consultancy or wind-down. |
| Any time | A provider publicly disputes a SEISMOGRAPH call and is right | Existential. Publish a full postmortem within 72h, Keystone-style. Credibility survives honest misses; it never survives quiet ones. |

---

## 6. Immediate Next Actions (this week)

1. Harden `make backtest` reproducibility (clean-machine test), add 1–2 additional public-incident backtests.
2. Draft the launch post, including the D9 "how we almost fooled ourselves" section and the synthetic-data disclosure.
3. Stand up first-party probes on top 10 tuples; verify daily cost < $0.10/probe (existing cap); ToS check per provider, documented.
4. Landing page per §1.4 spec (#0a0a0f background, soft diffuse dark-studio lighting, indigo trace glow, full-bleed, no Polaroid-style white borders/frames, no video).
5. Hash-commit the methodology pre-registration (reuse the canary suite content-addressing path).
6. Set up the eruption-watch automation (§2.2) — it must be live before the launch post, because launch day *is* your first ambulance.

---

*Doctrine, restated: get paid to plant probes, publish receipts the internet can re-run, and let every incident do your marketing. The incumbents' indifference is the runway. The corpus is the moat. Neutrality is the brand.*
