# Security Policy

SEISMOGRAPH is privacy- and security-critical by design: it correlates signals
across organisations without ever seeing their prompts or outputs. We take
reports seriously and design the system so that trust is structural, not assumed.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** — do not open a public issue.

- Use GitHub's **Private vulnerability reporting** (Security tab → "Report a
  vulnerability") on this repository, or
- contact the maintainer directly via the address on the GitHub profile
  [@Tania-coder](https://github.com/Tania-coder).

Include a description, reproduction steps, affected component, and impact. We aim
to acknowledge within a few business days and will coordinate disclosure with you.

## Supported versions

Phase 0/1 is pre-1.x in spirit; security fixes target the latest release on
`main`. Pin to a tagged release for reproducibility
(see [doi.org/10.5281/zenodo.21045518](https://doi.org/10.5281/zenodo.21045518)).

---

## Threat model

SEISMOGRAPH's security posture is organised around four threats. Each maps to a
specific control and an adversarial test.

### 1. Privacy leakage — raw data crossing the probe perimeter

**Threat:** prompt text or model output escapes the probe and reaches the network
or a downstream store.

**Control:** raw prompts and outputs are destroyed at the aggregation boundary.
The only object permitted to leave the probe is an aggregated `SignalBatch`
carrying SHA-256 hashes and differentially-private numeric features (ε = 2.0 per
flush; a budget accountant enforces a rolling 24-hour limit). This invariant is
checked on every change.

**Adversarial test:** assert that `SignalBatch` is the sole outbound type and that
it contains no raw text or persistent identifiers.

### 2. Sybil / poisoned probes — fabricated drift signals

**Threat:** an attacker spins up probes that emit fabricated feature vectors to
manufacture (or suppress) a public drift alert.

**Control:** each probe holds an Ed25519 keypair; the engine identifies probes by
public key and reputation, never by organisation identity. New keys start at zero
reputation and are down-weighted before they can influence a quorum. Replayed
signals are de-duplicated by observer identity within a scoring round.
*(Full signature verification + cross-round reputation weighting is Phase-2
hardening; Phase 0/1 ships set-deduplication and the identity model.)*

**Adversarial test:** inject a probe emitting fabricated vectors and verify it is
rejected or down-weighted, and that a single identity cannot satisfy the quorum.

### 3. Single-org false promotion — local noise as a public alert

**Threat:** one organisation's transient noise, prompt regression, or tampering is
surfaced publicly as provider drift.

**Control:** **correlation-first** promotion. A drift candidate becomes a public
alert only when ≥ 2 **distinct** observers agree (`QUORUM_MIN = 2`). Single-org
signals are retained as private fleet data and never surfaced publicly.

**Adversarial test:** feed a correlated noise burst from a single org and verify it
is never promoted to a public alert.

### 4. Malformed / unsigned ingestion — gateway abuse

**Threat:** a malformed or unsigned batch corrupts state or is partially ingested.

**Control:** the ingestion gateway validates a strict, frozen Pydantic schema and
rejects malformed or unsigned batches with a logged error and **no partial
ingestion**.

**Adversarial test:** send a malformed/unsigned batch and verify clean rejection
(no state mutation).

---

## Privacy guarantees (summary)

- **No raw data** — prompts and outputs never leave the probe perimeter.
- **Non-reversible identifiers** — only SHA-256 digests cross the wire.
- **Differential privacy** — Laplace mechanism, ε = 2.0 per flush, with a 24-hour
  budget accountant; sequential-composition accounting is Phase-2 hardening.
- **Pseudonymous federation** — the engine knows public keys and reputation, never
  organisation identity.

## Known limitations (stated honestly)

- Ed25519 signature *verification* and cross-round reputation weighting are
  Phase-2 hardening; Phase 0/1 ships the identity model and set-deduplication.
- DP parameters (ε = 2.0; global-max sensitivity) are conservative Phase-0
  defaults; production deployment must recalibrate per metric on real traffic.
- Key revocation is not yet designed (Phase 2/3).

These are tracked openly in [ROADMAP.md](ROADMAP.md) and the architecture document.
