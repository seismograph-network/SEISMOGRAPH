# KEYSTONE REPORT — Session 021
# Task: P3-004-C — Bearer token auth on `/v1/alerts/{alert_id}/export`
# Date: 2026-06-22
# Phase: 0 (post-launch hardening)

---

## 1. Provenance

| Artifact | Origin |
|---|---|
| `gateway/main.py` — auth block (lines 810–836) | AI-generated |
| `gateway/main.py` — endpoint body (lines 837–852) | AI-generated |
| `tests/test_audit.py` — AU6/AU7 Bearer header additions | AI-generated |
| `tests/test_audit.py` — AU9, AU10, AU11 adversarial tests | AI-generated |
| `.env.example` — SEISMOGRAPH_EXPORT_TOKEN section | AI-generated |
| All commit messages | AI-generated, human-approved |

Human contribution: Tatiana ran all PowerShell commands (git add/commit/push,
index corruption repair), confirmed all test results, reviewed all diffs.

---

## 2. Verification Summary

| Test | Result |
|---|---|
| AU1 generate() resolves LocalDriftAlert | PASS |
| AU2 generate() resolves PublicDriftAlert | PASS |
| AU3 generate() raises AlertNotFoundError | PASS |
| AU4 baseline_evidence count correct | PASS |
| AU5 report_checksum matches SHA-256 | PASS |
| AU6 endpoint 200 + Content-Disposition with valid Bearer | PASS |
| AU7 endpoint 404 for unknown alert_id | PASS |
| AU8 tampered report fails checksum (adversarial) | PASS |
| AU9 no Authorization header → 401 (adversarial) | PASS |
| AU10 wrong Bearer token → 401 (adversarial) | PASS |
| AU11 SEISMOGRAPH_EXPORT_TOKEN unset → 503 (adversarial) | PASS |
| Full suite (97 non-audit tests) | PASS (no regressions) |

Tools: pytest 9.1.1, Python 3.10.12
Command: `python3 -m pytest tests/test_audit.py -v -p no:cacheprovider`

---

## 3. Defects Caught and Fixed

**D-PC-021-01 — test_audit.py truncated at line 336 (Edit-tool RULE-1 violation)**
Symptom: `grep -n "^def test_"` showed `def test_audit_export_wrong_token_401(monkeypatc`
with no closing paren; `python3 -c "import ast; ast.parse(...)"` confirmed SyntaxError.
Root cause: Edit tool truncated the file at ~1067 bytes on NTFS mount when
writing AU9–AU11 additions.
Diagnosis clue: pytest reported "2 failed, 6 passed" (8 tests) despite the file
appearing to have 11 test functions — the .pyc cache (Jun 22 19:49) contained the
pre-AU9 bytecode; Python fell back to cached bytecode because it could not recompile
the syntactically invalid source.
Fix: `head -n 335` to drop partial line 336, then `cat >>` heredoc to append
AU10, AU11, AU8 in full.

**D-PC-021-02 — gateway/main.py truncated at line 837 (same root cause)**
Symptom: `NameError: name 'genera' is not defined` at gateway/main.py:837.
The endpoint body was truncated to `genera` immediately after the auth block.
Fix: `head -n 836` to clean boundary, then `cat >>` heredoc to append
`generator = AuditReportGenerator(repo)`, try/except, and JSONResponse.

**D-PC-021-03 — Stale .pyc masking SyntaxError**
Symptom: pytest collected 8 tests from a file with a SyntaxError at line 336.
Root cause: tests/__pycache__/test_audit.cpython-310-pytest-9.1.1.pyc was
generated before the truncation; Python used the cached bytecode.
Fix: `python3 -m pytest -p no:cacheprovider` bypasses the cache; on NTFS, the
.pyc is not deletable from the sandbox (chmod restriction), so `-p no:cacheprovider`
is the reliable workaround.

**D-PC-021-04 — monkeypatch env var appeared to not work (misdiagnosis)**
Original hypothesis: monkeypatch.setenv not visible to os.getenv() in FastAPI's
anyio worker thread. This was INCORRECT. The actual cause was D-PC-021-03: the
.pyc cache was running a version of the file that had no setenv call at all
(AU9–AU11 didn't exist in the cached bytecode).
Confirmation: once the .pyc cache was bypassed, all monkeypatch tests passed
without any changes to the injection mechanism.

**D-PC-021-05 — git index corruption (recurring NTFS issue)**
Symptom: `error: index uses extension, which we do not understand / fatal: index file corrupt`
Fix (PowerShell): `Remove-Item .git\index -Force && git reset`
This is the third occurrence this project. Root cause: sandbox and Windows
filesystem interact on the git index in a way that corrupts the extension field.

---

## 4. Known Limitations

**KNOWN-LIMIT-P3-004-C-01**: SEISMOGRAPH_EXPORT_TOKEN is a single shared secret
(bearer token) scoped to the entire gateway instance. Phase 4 target: per-fleet
OAuth scoped tokens with short TTLs. Documented in #SG-TRACE: REQ-AUDIT-001.

**KNOWN-LIMIT-P3-004-C-02**: No rate limiting on the export endpoint. A valid
token holder can call it in a tight loop. Acceptable at Phase 3 scale (internal
audit use only); revisit before public API access is granted.

**KNOWN-LIMIT-P3-004-C-03**: Token rotation requires gateway restart (env var
change). No hot-reload mechanism.

**KNOWN-LIMIT-NTFS-01**: The NTFS/WSL2 overlay mount prevents sandbox from
deleting .pyc files or git index files. Workarounds:
  - Pytest: always use `-p no:cacheprovider`
  - Git: run `Remove-Item .git\index -Force && git reset` from PowerShell

---

## 5. Accountability Statement

P3-004-C is complete. The `/v1/alerts/{alert_id}/export` endpoint now requires
a valid Bearer token. Unset token returns 503; wrong or missing Bearer returns 401.
All 11 AU* tests pass. The export endpoint was not publicly accessible before
this change (no auth = anyone with network access could export audit reports).
This change closes that gap before any external traffic reaches the gateway.

Commit: 0b25c60
Pushed: 2026-06-22 to https://github.com/seismograph-network/SEISMOGRAPH.git

________________________
Tatiana
Date: 2026-06-22

---

## 6. Methodology Note

The RULE-1 (heredoc for file writes > ~50 lines) violation happened twice in
this session despite the rule being established and in memory. The pattern:
Edit tool is used for a targeted insertion into a large file → Edit truncates
at ~1067 bytes → the truncation is not immediately visible because (a) the
Read tool cache shows the pre-truncation content, and (b) the .pyc cache masks
the SyntaxError in pytest.

Suggested process improvement: after ANY file write to a file > 100 lines,
immediately run `wc -l <file>` and compare against the expected line count.
Add this as a mandatory post-write check step, not an optional verification.
