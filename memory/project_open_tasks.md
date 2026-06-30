# SEISMOGRAPH — Project Open Tasks (LEAN)
# Quick-read backlog. Session-start summary: memory/CURRENT_STATE.md
# Full append-only log: memory/project_session_log.md (never edit)
# Last updated: 2026-06-29 (Session 026)

## Legend
[ ] open  [~] in progress  [x] complete  [D] deferred

---

## DO FIRST next session — commit uncommitted S026 work
- [ ] Live-probe files on disk are NOT in git. Branch
      seismograph/task-live-probe, commit the 7 files, run pytest (expect
      118), push. See NEXT_SESSION_PROMPT.md part 2.0.
- [ ] Cleanup QA leftovers: docs/_qa/, docs/pg-*.png, docs/op*.jpg.

## OPEN — Product realism (current focus; Tatiana's pivot S026)
- [x] Track 1 — live probe adapter: execute_canary(mock=False) calls a real
      OpenAI-compatible endpoint (providers.py + tests + live_probe.py).
      Verified offline (11 new + 69 probe tests). CODE DONE; commit + live
      run pending (see DO FIRST).
- [ ] Track 1 run — first real probe against a model (Mistral free / OpenAI /
      Ollama). Command: py -3.10 scripts\live_probe.py.
- [ ] Track 1b — wire live SignalBatch -> gateway -> dashboard so it shows a
      REAL model (privacy aggregator + crypto sign -> POST /v1/signals).
- [ ] Track 2 — first-touch clarity: landing + dashboard legibility for
      non-experts / grant reviewers / partners.
- [ ] Track 3 — plain-language narrative (who/what/why) for partners + grant.

## OPEN — Infra / Security (deadline)
- [ ] GitHub 2FA — TOTP backup before 2026-07-30.
- [ ] PyPI recovery #11202 -> republish seismograph-probe 1.0.1 sole author.
- [ ] dev.to OAuth — connect GitHub + Twitter.

## OPEN — Growth (PRIVATE detail in business/, gitignored)
- [ ] Outreach: business/outreach_pack_S026.md has 5 Tier-A notes ready
      (Corti, Legora, Nabla, Sana, Poolside). Send when product "reads"
      as finished. Tatiana sends from LinkedIn.

## DEFERRED — Phase 3 future
- [ ] SSO/RBAC, SOC 2, in-VPC probe, SLAs / canary-gated rollback, hires.

---

## COMPLETED — index (full detail in log + archive)
Phase 0: scaffold, canary suite, privacy+DP, ingestion, CUSUM+Bayesian,
  backtest (38d), architecture doc, OTel stub.
Phase 1: FastAPI gateway, SQLite, weather API, dashboard, quorum, e2e, launch.
Phase 2: Ed25519/Sybil design, ClickHouse, Redis, DP composition, OTel/MCP
  adapters, containerization.
Phase 3: multi-tenant isolation, audit-export auth.
S025: README badges, dep-graph generator, P3-002 webhooks closed.
S026: re-verification; grant/market pack (whitepaper PDF, pitch deck, one-pager);
  Zenodo DOI 10.5281/zenodo.21045518; ROADMAP.md; SECURITY.md; README nav +
  citation; live-probe adapter (code done, uncommitted).
