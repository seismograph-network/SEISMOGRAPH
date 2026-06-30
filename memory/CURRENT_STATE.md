# SEISMOGRAPH — CURRENT STATE
# Lean session-start read. Full history: memory/project_session_log.md
# (append-only, never edit) + memory/archive/. Backlog: project_open_tasks.md.
# Last updated: 2026-06-30 (Session 027)

## Identity
- Director: Tatiana Radchenko (Aarhus). Claude = Lead Technical Co-Pilot.
- SEISMOGRAPH: federated, privacy-preserving early-warning network for silent
  LLM/agent API drift. OSS, Apache-2.0.
- Repo: github.com/Tania-coder/SEISMOGRAPH | pip install seismograph-probe.
- Branch convention: seismograph/task-{id}.

## Phase
- Phase 0 thesis VALIDATED (38-day lead). Phases 1-2 core complete; Phase 3
  partial. Product-realism pass: Track 1 (live probe) + Track 1b (live
  signed signal -> gateway -> dashboard) DONE and MERGED to main (S027).
  NEXT: Track 2 (first-touch clarity) + Track 3 (plain-language narrative).

## Baseline (re-verify at session start)
- Tests: 122 passed (was 118; +1 non-ASCII key guard, +3 Track 1b). Run from
  repo root: py -3.10 -m pytest -q.
- Sandbox CAN run the FULL suite now. Install deps first: opentelemetry-sdk
  (otel) + fastapi/uvicorn/sqlalchemy/cryptography/httpx + pytest/ruff. The
  ONLY full-suite blocker was test_adapters importing opentelemetry, NOT file
  truncation. The old "engine/gateway read corrupt by mount" lore is RETIRED:
  all modules parse, import, and the gateway runs under uvicorn + TestClient
  in-sandbox. Trust the sandbox full run again.

## HARD RULE — git ONLY from PowerShell (Tatiana)
- NEVER run git from the sandbox against the mounted repo. The sandbox sees
  .git through the mount inconsistently (branch reads "seism", log empty) and
  any write (status/add/commit) leaves a .git/index.lock the sandbox cannot
  unlink -- which BLOCKS Tatiana's PowerShell git. If a lock appears:
  Remove-Item .git\index.lock -Force. (Burned once in S027.)
- Sandbox is for: writing files (heredoc, LF), running pytest / ruff /
  uvicorn. Commits, merges, deletes, pushes, status = Tatiana in PowerShell.

## Live assets
- Dashboard: https://seismograph-weather.onrender.com/dashboard (Render)
- Landing:   https://tania-coder.github.io/drift-defense/
- PyPI:      https://pypi.org/project/seismograph-probe/1.0.0/
- DOI:       https://doi.org/10.5281/zenodo.21045518 (concept; cite for grant)
- Grant/market pack: docs/SEISMOGRAPH_Whitepaper_v1.pdf, _Pitch_Deck.pptx/pdf,
  _OnePager.pdf (all committed to main).

## Open now (full backlog: project_open_tasks.md)
- Track 2 -- first-touch clarity: dashboard "what is this / STABLE vs
  DRIFTING" panel (dashboard/static, in-repo) + landing legibility
  (drift-defense, separate Pages repo).
- Track 3 -- plain-language narrative (who/what/why) for partners + grant.
- Track 1b real-Mistral local run (nice-to-see): pipeline proven (mock batch
  accepted on Tatiana's local gateway + 122 tests); a real Mistral emission
  to the LOCAL dashboard still pending (Mistral API-key friction deferred it;
  API key is the long no-dash string from console.mistral.ai -> API Keys, NOT
  the org UUID from admin.mistral.ai).
- Hygiene: bulk CRLF renormalize of ~10 phantom files (.gitattributes eol=lf
  added S027; run git rm --cached -r . && git reset --hard on a CLEAN tree).
- Infra/security (deadline): GitHub 2FA TOTP before 2026-07-30; PyPI 1.0.1
  sole-author republish (#11202); dev.to OAuth.
- Go-to-market (private, business/): outreach pack ready; Tatiana sends.

## Last sessions
- S027 (2026-06-30): committed + pushed + MERGED live arc to main. First live
  Mistral probe run. Probe hardening (sys.path bootstrap, non-ASCII key
  guard, .gitattributes). Track 1b: live signed signal -> gateway ->
  dashboard (scripts/live_emit.py + tests/test_live_emit.py, 3 integration
  tests). Untracked runtime data/seismograph.db. Keystone S026 addendum +
  S027. 122 passed. Lore retired (sandbox full suite); git-only-PowerShell.
- S026 (2026-06-29): re-verify + grant/market pack + Zenodo DOI +
  ROADMAP/SECURITY + README; live-probe code (then uncommitted). See log.
