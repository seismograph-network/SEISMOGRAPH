# SEISMOGRAPH — Project Open Tasks (LEAN)
# Quick-read backlog. Session-start summary: memory/CURRENT_STATE.md
# Full append-only log: memory/project_session_log.md (never edit)
# Last updated: 2026-06-30 (Session 027)

## Legend
[ ] open  [~] in progress  [x] complete  [D] deferred

---

## OPEN — Product clarity (current focus; next session)
- [ ] Track 2 -- first-touch clarity: dashboard "what is this / STABLE vs
      DRIFTING" explainer panel (dashboard/static, in-repo, Claude can build)
      + landing legibility (drift-defense, separate Pages repo).
- [ ] Track 3 -- plain-language narrative (who/what/why) for partners + grant.

## OPEN — Product realism (nice-to-finish)
- [ ] Track 1b real-Mistral LOCAL run: pipeline proven (mock batch accepted +
      122 tests); a real Mistral emission to the local dashboard still
      pending. Key = long no-dash string from console.mistral.ai -> API Keys.

## OPEN — Hygiene / Infra / Security
- [ ] Bulk CRLF renormalize of ~10 phantom files (.gitattributes eol=lf added
      S027): git rm --cached -r . && git reset --hard on a CLEAN tree.
- [ ] GitHub 2FA -- TOTP backup before 2026-07-30.
- [ ] PyPI recovery #11202 -> republish seismograph-probe 1.0.1 sole author.
- [ ] dev.to OAuth -- connect GitHub + Twitter.

## OPEN — Growth (PRIVATE detail in business/, gitignored)
- [ ] Outreach: business/outreach_pack_S026.md has 5 Tier-A notes ready.
      Send when product "reads" as finished. Tatiana sends from LinkedIn.

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
S026: re-verification; grant/market pack (whitepaper PDF, pitch deck,
  one-pager); Zenodo DOI 10.5281/zenodo.21045518; ROADMAP.md; SECURITY.md;
  README nav + citation; live-probe adapter (code).
S027: live-probe arc COMMITTED + MERGED to main; first live Mistral run;
  probe hardening (sys.path bootstrap, non-ASCII key guard, .gitattributes);
  Track 1b live signed signal -> gateway -> dashboard (live_emit.py + tests);
  untrack runtime db. 122 passed. Sandbox full-suite lore retired.
