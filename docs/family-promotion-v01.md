# MNCS Family Promotion v0.1

Status: experimental. The contracts below are versioned and enforced, but
`0.1`/`0.2` revisions may still evolve; do not treat this milestone as a
stability promise.

A promotion PASS is expensive to fake. It requires the right authoritative
claims, from the right owners, against the right exact revision, under the
right contracts, with the exact evidence set digest-bound to the decision.

## What v0.1 establishes

1. A consuming repository declares a versioned MNCS promotion boundary
   (`mncs-promotion-boundary/0.1`; see `docs/promotion-pattern.md`).
2. MNCDS supplies development state and obligations
   (`mncds-development-record`, `mncds-obligations`,
   `mncds-obligation-record/0.2`).
3. Family authorities supply revision-bound evidence (subject stamps).
4. Evidence is authority-bound (pinned `mncs-authority-map/0.1`),
   contract-bound (exact revision matching), revision-bound (40-hex
   subjects), and digest-bound (SHA-256 references including obligations
   and the map itself).
5. MNCS evaluates the boundary owner-natively
   (`scripts/mncs_promotion_evaluate.py` in
   machine-native-complexity-standard).
6. `mncs-actions` transports and aggregates the result without
   reinterpreting semantics (`validate_promotion_claim` proves carrier
   shape only).
7. Commons relates the evidence through a ChangeSet
   (`docs/changeset-promotion.md` plus the machine-readable fixture in
   `tests/`).
8. A deterministic PASS promotion canary exists
   (`promotion-gate` job: required boundary, fails unless exactly PASS,
   plus the negative matrix).
9. Trust-substitution cases are rejected (forged provider, wrong
   authority, wrong subject, missing revision, forged digests detected by
   rebinding, duplicates, contradictory resolutions).
10. A real MNCS-family repository dogfoods the lifecycle
    (`.github/workflows/promotion-dogfood.yml` with
    `promotion/boundary.json`).

## State semantics (consistent everywhere)

- PASS: required authoritative evidence established the condition.
- FAIL: authoritative negative evidence established the boundary is not
  satisfied.
- UNKNOWN: contracts and evidence are valid, but authoritative information
  is insufficient to decide. Never a softened INVALID, never a PASS.
- INVALID / NOT_ESTABLISHED: no valid claim exists (malformed,
  contradictory, untrusted, or unbound input).

## Out of scope for v0.1

Cryptographic attestation of producer identity (revision + digest binding
first, by design); global monolithic boundaries (repository-owned
boundaries instead); language-side JSON parsing, hashing, and filesystem
access (explicit host escapes with pressure artifacts).
