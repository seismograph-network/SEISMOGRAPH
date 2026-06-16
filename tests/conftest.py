"""
tests.conftest
==============
Shared pytest fixtures for the SEISMOGRAPH test suite.

SEISMOGRAPH_DB_URL override
----------------------------
The autouse fixture `use_memory_db` patches the SEISMOGRAPH_DB_URL
environment variable to "sqlite:///:memory:" for every test.  This
prevents the gateway lifespan from creating or writing to the real
SQLite file (data/seismograph.db) during the test run.

The patch is function-scoped (default) -- each test starts with a fresh
env var value and the original value is restored after the test exits.

The in-memory SQLite engine created by SignalRepository uses StaticPool,
so all sessions within one lifespan share the same in-memory database.
Multiple TestClient invocations (across tests) each run a new lifespan
and therefore get independent in-memory databases -- providing test
isolation with no filesystem side effects.

#SG-TRACE: REQ-TEST-001
#   | assumption: autouse monkeypatch is the least-intrusive way to
#     redirect the gateway DB without modifying the app code under test
#   | test: (all tests in test_gateway.py implicitly)
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def use_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all gateway DB writes to an in-memory SQLite instance.

    Applied automatically to every test in the suite.
    """
    monkeypatch.setenv("SEISMOGRAPH_DB_URL", "sqlite:///:memory:")
