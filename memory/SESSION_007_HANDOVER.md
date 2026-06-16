# SESSION_007_HANDOVER — P2-002 Complete / P2-003 Ready

## Session state (2026-06-12)

**Tests:** 43/43 PASSED  
**Ruff:** 0 violations, 26 files  
**clickhouse-connect:** 1.3.0 installed in sandbox

---

## What shipped this session

### P2-001 (recap)
- `probe/crypto.py`: KeyManager + canonical_json() + sign_payload()
- `probe/sdk.py`: _key_manager injection; flush() signs content=canonical_bytes
- `gateway/auth.py`: real Ed25519 verify_signature()
- `gateway/main.py`: raw body read BEFORE FastAPI parse (D21 fix)
- Tests: test_crypto.py (11 tests) + T12/T13 adversarial

### P2-002 (this session)
- `engine/repository.py`: SignalRow + AlertRow dataclasses; BaseRepository ABC;
  SignalRepository(BaseRepository)
- `engine/clickhouse.py`: ClickHouseRepository(BaseRepository); setup_tables()
  (3 MergeTree tables); all 6 interface methods via raw SQL
- `gateway/main.py`: STORAGE_BACKEND env var; ClickHouse branch in lifespan
- `tests/test_storage.py`: 7 new mocked ClickHouse tests (CU1-CU7)
- `pyproject.toml`: clickhouse-connect>=0.7

---

## ClickHouse env vars

| Var | Default |
|---|---|
| STORAGE_BACKEND | sqlite |
| CLICKHOUSE_HOST | localhost |
| CLICKHOUSE_PORT | 8123 |
| CLICKHOUSE_USER | default |
| CLICKHOUSE_PASSWORD | (empty) |
| CLICKHOUSE_DATABASE | default |

Tables: telemetry_signals, local_drift_alerts, public_drift_alerts
Engine: MergeTree, ORDER BY (model_tuple, timestamp)

---

## Phase 2 roadmap

| Task | Status | Description |
|---|---|---|
| P2-001 | COMPLETE | Ed25519 cryptographic identity |
| P2-002 | COMPLETE | ClickHouse migration + BaseRepository ABC |
| P2-003 | OPEN | Redis distributed state (multi-node quorum) |
| P2-004 | OPEN | DP composition (formal privacy accounting) |
| P2-005 | OPEN | OTel/MCP adapters |

---

## RULE-1 (carry forward every session)

ALL files on NTFS written via bash python3 open().write():
  python3 -B - << 'SCRIPT_EOF'
  with open('/sessions/zealous-inspiring-dijkstra/mnt/SEISMOGRAPH/...', 'w', newline='\n') as f:
      f.write(content)
  SCRIPT_EOF

Write/Edit tools silently truncate at ~1067 bytes.
Python target: 3.10. timezone.utc not datetime.UTC.
Ruff: /sessions/zealous-inspiring-dijkstra/.local/bin/ruff

---

## Suggested next: P2-003 Redis Distributed State

Goal: Move AgreementScorer state from in-process dict to Redis so that
multiple gateway instances can coordinate quorum without all traffic
hitting one node.

Acceptance criteria:
1. engine/scorer_redis.py: RedisAgreementScorer wrapping redis-py client.
   - ingest(): SADD org_id to Redis set sg:quorum:{model_tuple}; EXPIRE 24h.
   - promote_to_public_alert(): SCARD >= QUORUM_MIN -> return count; else None.
   - clear(): DEL the set.
2. QUORUM_BACKEND env var: 'memory' (default) or 'redis'.
3. Tests: mocked redis client. Assert SADD/SCARD/DEL calls with correct keys.
4. Adversarial: single-org SADD cannot promote (SCARD==1 < QUORUM_MIN).

Privacy: Redis key sg:quorum:{model_tuple} is a public identifier.
No raw prompts or outputs in any key or value.
