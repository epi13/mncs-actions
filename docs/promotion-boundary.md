# Promotion boundary transport

`mncs-actions` does not own promotion semantics. MNCDS describes
development state (`docs/mncds-check-catalog.md` in
`machine-native-complexity-development-specification`); MNCS owns the
promotion boundary (`docs/promotion-boundary.md` and
`schemas/mncs-promotion-boundary-0.1.schema.json` in
`machine-native-complexity-standard`, applied by the owner-native
`scripts/mncs_promotion_evaluate.py`). This document describes only what
transport does with their claims.

## Lifecycle

```text
Change
  |
  v
MNCDS (development record / obligations / unresolved evidence)
  |
  v
family providers (MNCS validation, rights, project, MNCDS, Forge, language)
  |
  v
MNCS promotion boundary (owner-native evaluation)
  |
  v
mncs-actions (execute + aggregate + preserve)
  |
  v
PASS / FAIL / UNKNOWN (+ promotion eligible / blocked / unresolved)
```

## Wiring a boundary

A consuming repository declares, in order:

```text
mncs-validation          (mncs-command)
mncds-development-record (mncds-command, first-class; no additional-checks seam)
promotion-boundary       (promotion-command, runs the MNCS evaluator
                          over this workflow's other result files)
```

See `examples/promotion.yml`. The `promotion-command` is caller-composed:
boundary document + `--authority-map` (pinned trust binding derived via
`scripts/authority_map.py` from `family-producer-descriptors.json`) +
`--checks` (the other result files) + exact subject
(`--subject-repository` + 40-hex `--subject-commit`) + `--output` the
promotion result file. The reusable workflow runs it after every evidence
provider, validates the `check-result/1` shape, preserves the receipt,
aggregates the check, and exposes `promotion-verdict`,
`promotion-result-path`, and `promotion-manifest-digest` as outputs.
Callers should additionally apply `validate_promotion_claim` and, wherever
consumed bytes are at hand, recompute bound digests: shape validation
proves the claim is well-formed, rebinding proves the bytes are the ones
evaluated.

## What transport checks (and what it never decides)

- `lib/mncs_actions.py::validate_promotion_claim`: the promotion result
  names the declared boundary and candidate subject, carries blockers for
  any non-PASS verdict, and preserves digest-bound evidence references.
  Verdict semantics are never re-derived here.
- `subject_stamp` (shared by every adapter via `--subject-repository` /
  `--subject-commit`): binds a claim to an exact revision. Moving refs
  are rejected at the membrane, never stamped.
- `scripts/project_obligations.py` and `scripts/pressure_to_obligations.py`
  implement the MNCDS obligation-projection mapping verbatim so pressure
  enters the obligation lifecycle instead of living beside it. Tolerance
  policy stays in the MNCS boundary, not in projection.

Malformed or contradictory promotion input establishes no claim
(`INVALID` / `NOT_ESTABLISHED`); it is never softened to `UNKNOWN`.
A valid negative stays `FAIL`; `UNKNOWN` never becomes `PASS`.

## Compatibility observation vs promotion gate

The fixed-family canary keeps `promotion-boundary` optional: it answers
whether pinned authorities still execute and produce coherent evidence,
and UNKNOWN may stay green there. That path never claims promotability.
The separate `promotion-gate` job answers whether an exact pinned fixture
universe satisfies an explicit required boundary and fails unless
promotion is exactly PASS. See `docs/family-promotion-v01.md`.

## The two promotion-adjacent check ids

- `promotion-boundary`: the actual MNCS promotion evaluation result.
- `mncs-language-promotion-boundary`: an mncs-language compilation study
  of the pure promotion-decision core (`pressure/promotion-boundary.mncs`,
  mirrored from the evaluator). Language pressure, not a promotion claim.
