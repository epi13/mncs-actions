# MNCS agent execution contract

This file is the machine-discoverable binding between agents working in this
repository and the MNCS ecosystem. It is enforced by
`tests/test_agent_contract.py`: every `scripts/` and `schemas/` path named
below must exist, and the authority table must match
`schemas/development-pressure-evidence.schema.json` exactly. If you change
the machinery, update this contract; if you change this contract, update the
test. A contract that drifts from the machinery is a defect.

## 1. MNCS-language is the implementation default

New implementation logic belongs in MNCS source under `pressure/*.mncs`
first. Host Python under `lib/` and `scripts/` is transport, correlation,
and projection only: it must not choose solutions, close obligations, or
reimplement semantics that MNCS can express. Before writing host code, check
whether the capability already exists in mncs-language (`library/std`,
`library/core`) or belongs there.

## 2. Prefer MNCS stdlib over repository-local substitutes

Do not add a local helper for something the MNCS standard library already
provides or should provide. If the stdlib lacks a needed primitive, that is
a language-pressure event (section 3), not a license to duplicate it here.

## 3. Missing MNCS capability becomes pressure evidence, never a workaround

When MNCS cannot cleanly express or perform something this repository needs:

1. Record it as development-pressure evidence conforming to
   `schemas/development-pressure-evidence.schema.json`, produced via
   `scripts/development_pressure.py`.
2. Project it to obligations with `scripts/pressure_to_obligations.py`.
3. Route it to the owning authority. The owner vocabulary is fixed:

   | surface            | semantic authority      |
   |--------------------|-------------------------|
   | pressure semantics | MNCDS                   |
   | rights semantics   | mncs-rights-provenance  |
   | coordination       | MNCS-Commons             |
   | language capability| mncs-language           |
   | assurance semantics| mncs-forge-mcp          |
   | transport          | mncs-actions            |

4. Fix the deficiency in the owning repository, run that repository's suite,
   and only then resume the original task here.

Foreign-language scaffolding that hides a genuine MNCS deficiency is a
conformance failure, even when all tests pass.

## 4. Actions prove, they do not decorate

Run the deterministic suite through `scripts/mncs_project_check.py`, which
emits one `mncs.check-result/1` verdict. A PASS with no evidence behind it
is a defect in the check, not a success: claims must trace to check results,
evidence manifests (`schemas/evidence-manifest.schema.json`), and promotion
proofs (`schemas/family-acceptance-proof.schema.json`).

## 5. Distributed claims require execution receipts

Any claim involving remote execution must be backed by an execution receipt
(`schemas/execution-receipt.schema.json`) identifying where execution
happened: host identity, host OS/architecture, target architecture,
native versus emulated execution (plus emulator identity and version),
backend and toolchain versions, and artifact hash. A requested remote run
that silently fell back locally is a failure.

## 6. Badges derive from evidence

The badge (`docs/mncs-badge.svg` plus sidecar `docs/mncs-badge.json`, see
`docs/badges.md`) renders the current verdict. Never edit badge artifacts by
hand to look green; only evidence-driven promotion changes them, and the
rendered level must not overstate what was proven (compile versus
execution, emulated versus physical, single device versus device class).
