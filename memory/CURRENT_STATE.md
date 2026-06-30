# SEISMOGRAPH — CURRENT STATE
# Lean session-start read. Full history: memory/project_session_log.md
# (append-only, never edit) + memory/archive/. Backlog: project_open_tasks.md.
# Last updated: 2026-06-29 (Session 026)

## Identity
- Director: Tatiana Radchenko (Aarhus). Claude = Lead Technical Co-Pilot.
- SEISMOGRAPH: federated, privacy-preserving early-warning network for silent
  LLM/agent API drift. OSS, Apache-2.0.
- Repo: github.com/Tania-coder/SEISMOGRAPH | pip install seismograph-probe.
- Branch convention: seismograph/task-{id}.

## Phase
- Phase 0 thesis VALIDATED (38-day lead). Phases 1-2 core complete; Phase 3
  partial. NOW: product-realism + clarity pass so it reads as a finished
  product to grant reviewers and partners (Tatiana's call, S026).

## Baseline (re-verify at session start)
- Tests in git: 107 passed. After committing S026 live-probe work: expect 118
  (107 + 11 new). Run from repo root: py -3.10 -m pytest -q.
- Ruff: 0 on real disk. Sandbox shows false "errors" = NTFS-overlay truncation
  of file tails (correlation.py, main.py, canary.py); trust GitHub CI, not the
  sandbox, for lint/full-suite. To run fresh in sandbox use
  PYTHONPYCACHEPREFIX off the mount (probe-side only; engine/gateway corrupt).
- Sandbox cannot delete files or run git; Tatiana does both in PowerShell.

## Live assets
- Dashboard: https://seismograph-weather.onrender.com/dashboard (Render)
- Landing:   https://tania-coder.github.io/drift-defense/
- PyPI:      https://pypi.org/project/seismograph-probe/1.0.0/
- DOI:       https://doi.org/10.5281/zenodo.21045518 (concept; cite for grant)
- Grant/market pack: docs/SEISMOGRAPH_Whitepaper_v1.pdf, _Pitch_Deck.pptx/pdf,
  _OnePager.pdf (all committed to main).

## UNCOMMITTED on disk (commit first next session)
- Live-probe Track 1: probe/providers.py, probe/canary.py, tests/test_providers.py,
  scripts/live_probe.py, .env.example, docs/PROVIDER_TOS_CHECKS.md,
  KEYSTONE_REPORT_SESSION_026.md. Verified offline (11 new + 69 probe tests,
  ruff clean). Branch seismograph/task-live-probe. First real run not yet done.

## Open now (full backlog: project_open_tasks.md)
- Product realism: commit + live-run probe; then wire live SignalBatch ->
  gateway -> dashboard so it shows a REAL model (Track 1b).
- Product clarity: landing + dashboard legibility (Track 2); plain-language
  narrative (Track 3).
- Infra/security (deadline): GitHub 2FA TOTP before 2026-07-30; PyPI 1.0.1
  sole-author republish (#11202); dev.to OAuth.
- Go-to-market (private, business/): outreach pack ready; Tatiana sends.

## Last sessions
- S026 (2026-06-29): re-verify + grant/market pack + Zenodo DOI +
  ROADMAP/SECURITY + README; live-probe code (uncommitted). See log.
- S025 (2026-06-29): README badges, dep-graph, P3-002 closed (9f5b73b,80dbc10).
