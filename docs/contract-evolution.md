# Contract evolution

The contracts in this repository are deliberately experimental. Their purpose is to give the ecosystem a stable point to pressure with real repositories and workflows.

## Compatibility rules

- Additive fields are preferred when existing consumers can ignore them safely.
- A change to required fields, verdict meaning, digest meaning, or path semantics is breaking.
- Breaking changes require a new schema version and an explicit migration note.
- Actions should keep accepting the prior contract for a documented compatibility window when practical.
- A workflow must not promote an artifact solely because a parser ignored a field it did not understand.

## Change log (this increment)

- `mncs.verification-result/1`: unchanged required fields; clarified that
  top-level PASS containing a failing check is structurally invalid
  (enforced by `lib/mncs_actions.py`, documented in the schema
  description). Extra fields remain permitted and ignored.
- `mncs.evidence-manifest/1`: additive clarification. `kind` now permits
  `verification | check | aggregation` (existing `verification` manifests
  remain valid). Optional `receipt`, `references`, `claim_status`
  (`ESTABLISHED`), `unresolved`, and `boundary` added; no required field
  changed.
- New `mncs.execution-receipt/1`: always emitted, even when no claim is
  established. `claim_status` distinguishes `ESTABLISHED` from
  `NOT_ESTABLISHED`.
- New `mncs.check-result/1` and `mncs.aggregate-result/1`: composable
  provider contract and required/optional composition.
- Outputs: added `claim-status`, `execution-receipt-path`, and
  `manifest-digest`. `provenance-digest` retained as a deprecated alias
  with the identical value (it always hashed the whole canonical
  manifest).

## Change log (revision coherence + hardened bindings)

- Reusable workflow: internal `epi13/mncs-actions/actions/...@main`
  floats replaced with synchronized immutable full-SHA pins plus
  `scripts/sync-pins.sh` and `tests/test_revision_coherence.py` (see
  `docs/revision-coherence.md`). Historical workflow revisions now
  execute exactly their baked action implementation, never a newer one.
- `mncs.aggregate-result/1`: `checks[].digest` / `checks[].path` /
  `provider` / `scope` explicitly described in the schema (digest and
  path carry patterns) and strictly validated when present by
  `validate_aggregate_result` (malformed bindings rejected; fields stay
  optional; extra fields still permitted). Schema and executable
  validation are mechanically pinned by `tests/test_evidence_bindings.py`.
- `mncs.check-result/1`: top-level `digest` and reference `path`
  patterns declared; present-but-malformed digests/paths rejected.
- Rights projection hardened (`classify_rights_report`): missing or
  self-contradictory native reports establish no claim
  (`NOT_ESTABLISHED`, never fabricated); identity mismatch downgrades an
  otherwise-pass to `FAIL`; unrecognized outcomes stay `UNKNOWN` with a
  drift note. Mirrored in MNCS by `pressure/rights-projection.mncs`
  (pinned by `tests/test_mncs_pressure.py`).

## Change log (portability fix)

- Reusable workflow: `./actions/...` replaced with
  `epi13/mncs-actions/actions/...@<revision>` (portable across callers;
  keep internal pins in sync with the workflow revision on release).
- Family workflow aggregate inputs are now conditional on providers that
  actually ran: intentional absence follows missing-required semantics
  (`UNKNOWN` for required, no effect for optional); explicitly listed but
  missing files stay `INVALID`/`NOT_ESTABLISHED`. Generic aggregate
  validation unchanged and strict.
- Aggregate composite invariant: implementation root is `.` after entering
  `working-directory` (fixes `src/src/...` double resolution).
- Aggregate evidence now carries component bindings: `checks[].digest` /
  `path` plus manifest `references[]` (`kind: check-result`) for later
  traversal. Additive only; schemas already permit extra fields.
- New `pressure/family-boundary.mncs`: pure dominance expressed in MNCS
  via `mncs.core.status.v1`; IO/hash/GitHub remain documented host escape.

## Pressure loop

~~~text
candidate implementation
        |
        v
workflow consumes contract
        |
        +--> contract holds
        |       |
        |       v
        |   preserve evidence
        |
        +--> capability gap
                |
                v
        record pressure and route to owner
~~~

A capability gap is evidence about the current language, standard, or integration surface. It is not permission to weaken the contract silently. Future actions may emit canonical capability-gap records once the owning MNCS-family contract is available.

## Family protocol increment

- `mncs-actions.family-producer-descriptors/1` is a data-only descriptor
  registry. It selects an allowlisted operation/adapter, safe input paths,
  expected output identities, and required capabilities. It cannot request
  arbitrary shell execution.
- `mncs-actions.family-producer-output/1` is a per-owner content-addressed
  artifact envelope. It binds the independently expected family revision,
  descriptor digest, native reports, and check-result paths/digests.
- `mncs-actions.family-integration-evidence/1` is schema-covered and
  mechanically validated. It records fixed versus moving-head mode, exact
  contract/descriptor digests, all producer identities and revisions, strict
  check membership, authority, promotion semantics, and the producer-job /
  artifact-only aggregation boundary.
- `mncs-actions.development-pressure-evidence/1` carries unresolved family
  obligations using the current MNCDS `DevelopmentPressure` vocabulary.
  Actions correlates observations and preserves owner references; absence in
  a later observation is explicitly not treated as proof of resolution.
- The required-family JUnit guard counts nested testcase elements rather than
  trusting a root counter, and rejects malformed, empty, skipped, or failed
  reports.

## Family revision advancement

`family-contracts.json` is the fixed family contract. It is never rewritten by
a moving-head observation. `scripts/family_contracts.py propose` resolves a
configured branch to exact SHAs and emits a candidate tied to the fixed
contract's SHA-256 digest. `validate` requires candidate metadata and base
revisions to match the fixed contract; `promote` writes a separate proposed
fixed document so review and merge remain visible repository changes.

The workflows preserve this distinction:

- `moving-head-family-drift.yml` observes current heads and reports drift;
- `family-contract-advance.yml` runs the candidate path for explicit review;
- `family-contract-canary.yml` validates only the committed fixed SHA set.

The advancement contract is moving-head -> candidate evidence -> reviewed
fixed-contract change -> fixed canary. No `UNKNOWN` result, scheduled run, or
generated file has promotion authority.

## Adding or deepening a family provider

Prefer an owner-native validator or source-study command over a duplicate
semantic implementation in Actions. Add a focused adapter that preserves the
native report and maps only its established result to `mncs.check-result/1`.
Add the provider to the integration runner, required aggregation list, tests,
and the fixed contract's artifact inventory. Keep unresolved obligations as
`UNKNOWN`, and make missing or malformed native output fail closed. Record the
provider's exact checkout SHA in references and in the family evidence.
