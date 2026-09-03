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
