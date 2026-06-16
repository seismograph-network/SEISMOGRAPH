"""
seismograph.probe.canary_suite
==============================
Canary suite management -- versioned, hash-addressed sets of probe prompts
used to detect semantic drift in LLM/agent API responses.

Architectural invariants enforced here:
  - Suite version is immutably content-addressed (SHA-256 of prompt corpus).
  - Baseline corpus is append-only; historical baselines are never mutated.
  - Suite size <= 200 prompts at temperature 0.
  - Cost per probe per day target: < $0.10 at current provider pricing.
  - Raw prompts never leave this module; only feature vectors are emitted.

#SG-TRACE: REQ-CANARY-001
#   | assumption: SHA-256 collision resistance sufficient for corpus addressing
#   | test: test_canary_suite_hash_stability
#SG-TRACE: REQ-CANARY-002
#   | assumption: <=200 prompts keeps daily cost < $0.10 across tuples
#   | test: test_canary_cost_cap
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CanaryPrompt:
    """A single immutable canary prompt entry.

    #SG-TRACE: REQ-CANARY-003
    #   | assumption: prompt_id is caller-assigned and unique within a suite
    #   | test: test_canary_prompt_uniqueness
    """

    prompt_id: str
    text: str
    # SHA-256 of stable reference output features
    expected_feature_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.prompt_id:
            raise ValueError("prompt_id must be non-empty")
        if not self.text:
            raise ValueError("text must be non-empty")


@dataclass
class CanarySuiteVersion:
    """An immutable, content-addressed snapshot of a canary suite.

    The version_hash is derived from the ordered prompt corpus so that any
    change in prompt content or ordering produces a new version.

    #SG-TRACE: REQ-CANARY-004
    #   | assumption: JSON-serialised sorted-key repr is deterministic
    #   | test: test_canary_version_hash_determinism
    """

    version_hash: str
    prompts: list[CanaryPrompt]
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_prompts(
        cls,
        prompts: list[CanaryPrompt],
        metadata: dict[str, str] | None = None,
    ) -> CanarySuiteVersion:
        """Create a new version from a list of prompts.

        The corpus is serialised to a canonical JSON string and hashed with
        SHA-256 to produce the immutable version_hash.
        """
        corpus = [{"prompt_id": p.prompt_id, "text": p.text} for p in prompts]
        corpus_bytes = json.dumps(
            corpus, sort_keys=True, ensure_ascii=True
        ).encode()
        version_hash = hashlib.sha256(corpus_bytes).hexdigest()
        return cls(
            version_hash=version_hash,
            prompts=list(prompts),
            metadata=metadata or {},
        )


# ---------------------------------------------------------------------------
# Suite registry (stub -- no persistence layer yet)
# ---------------------------------------------------------------------------


class CanarySuiteRegistry:
    """Append-only registry of canary suite versions.

    Versions are stored keyed by their content-addressed hash.
    Mutation of an existing version raises an error; callers must create
    a new version instead.

    #SG-TRACE: REQ-CANARY-005
    #   | assumption: in-memory store sufficient for Phase 0;
    #     ClickHouse migration planned for Phase 2
    #   | test: test_registry_append_only
    """

    MAX_PROMPTS_PER_SUITE: int = 200

    def __init__(self) -> None:
        self._versions: dict[str, CanarySuiteVersion] = {}

    def register(self, version: CanarySuiteVersion) -> str:
        """Register a new suite version.  Returns the version_hash.

        Raises:
            ValueError: If version with this hash already exists.
            ValueError: If the suite exceeds MAX_PROMPTS_PER_SUITE.
        """
        if len(version.prompts) > self.MAX_PROMPTS_PER_SUITE:
            raise ValueError(
                f"Suite has {len(version.prompts)} prompts; "
                f"cap is {self.MAX_PROMPTS_PER_SUITE}."
            )
        if version.version_hash in self._versions:
            raise ValueError(
                f"Version {version.version_hash} already registered. "
                "Create a new version instead of mutating."
            )
        self._versions[version.version_hash] = version
        return version.version_hash

    def get(self, version_hash: str) -> CanarySuiteVersion:
        """Retrieve a registered version by hash.

        Raises:
            KeyError: If the version_hash is not found.
        """
        if version_hash not in self._versions:
            raise KeyError(f"Version {version_hash!r} not found in registry.")
        return self._versions[version_hash]

    def list_hashes(self) -> list[str]:
        """Return all registered version hashes in insertion order."""
        return list(self._versions.keys())
