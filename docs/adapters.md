# Family adapters: rights/provenance and MNCS validation

`mncs-actions` invokes and transports; it does not reimplement owning
semantics. Each adapter consumes a native report and emits a
`mncs.check-result/1` with documented mapping. Native details are
preserved, never hidden.

## Rights and provenance (`adapters/rights_adapter.py`)

Source: `mncs-rights-provenance`, `mncs-rp validate` JSON report.

| Native `outcome` | Check `verdict` | Rationale |
| --- | --- | --- |
| `pass` | `PASS` | Requirements satisfied under the profile (requires identity match and no structural contradiction) |
| `blocked`, `invalid` | `FAIL` | Negative established (a structurally-invalid artifact is a valid negative claim about the artifact) |
| `pass-with-findings`, `review-required`, `unknown` | `UNKNOWN` | Review outstanding; insufficient for PASS |
| unrecognized non-empty | `UNKNOWN` | Vocabulary drift never becomes PASS; drift noted in `unresolved` |

FAIL vs NOT_ESTABLISHED (never conflated):

- A well-formed domain report establishing a negative (`blocked`,
  artifact-`invalid`, identity mismatch downgrading an otherwise-pass)
  is `FAIL`: the execution succeeded and the domain spoke negatively.
- No well-formed report means no claim: unreadable JSON, a missing
  `outcome`, or a self-contradictory report (`pass` for a structurally
  invalid manifest, `invalid` for a structurally valid one) makes the
  adapter exit 2 emitting nothing, so `run-check` records
  `NOT_ESTABLISHED` (`INVALID`). Malformed output is never rewritten as
  `UNKNOWN` or `FAIL`.
- Identity mismatch downgrades `pass` to `FAIL` (binding failure is a
  valid negative; tampering is Fail, never a pass, never silent).

The native `outcome`, `severity`, `findings`, `issues`, and manifest
identity are preserved in `summary`/`unresolved`/`references`
(`kind: rights-manifest`). The reference carries the rights authority,
authority record identity when the owner report provides one, exact digest
and `authority_status`; missing authority status remains `UNKNOWN`.
`legal_conclusion` remains `NOT_MADE` upstream. The family producer also
copies the pinned rights manifest into its native transport and binds its
raw digest/revision, so the assembler can verify that a rights reference
points at the exact transported record.

Fixtures: `rights PASS`, `review-required -> UNKNOWN`, `blocked -> FAIL`,
`invalid -> FAIL`, malformed input -> adapter exit 2 (caller emits
`NOT_ESTABLISHED`; nothing is fabricated).

## MNCS validation (`adapters/validator_adapter.py`)

Source: `mncs-validator-rs`, `mncs-rs validate --json` /
`validate-bundle --json` (`ValidationReport`).

| Native report | Check `verdict` |
| --- | --- |
| `valid=true`, `computed_status` PASS/FAIL/UNKNOWN | same verdict |
| `valid=true`, unrecognized status | `UNKNOWN` (never PASS) |
| `valid=false` (issues established) | `FAIL` |

Operational failures (exit 2, no report) must not be converted into a
check-result; let `run-check` emit `NOT_ESTABLISHED`.

Both adapters exit 2 on malformed native input and validate their output
with the canonical `validate_check_result` before writing.

## ChangeSet / coordination (`adapters/changeset_adapter.py`)

The current owning repositories describe ChangeSets as an experimental MNCDS
protocol and Commons as a transport/index, rather than exposing a standalone
stable ChangeSet schema. The first bounded integration therefore consumes the
published `mncs-rights-provenance` v0.3 `lineage-record` shape, whose
`changesets[]` entries preserve the owning ChangeSet identity and exact base
revisions. The adapter is a transport validator, not a second ChangeSet
semantic model.

| Native lineage state | Check verdict |
| --- | --- |
| digest-bound, structurally complete, no unresolved fields | `PASS` |
| structurally valid but missing participant/evidence coverage or explicit unresolved fields | `UNKNOWN` |
| malformed, contradictory, wrong digest/revision, duplicate participant, or unverifiable binding | no check; `NOT_ESTABLISHED` |

The output retains a digest reference to the exact lineage bytes. When local
evidence bytes are available, `--evidence-root` verifies the declared
SHA-256; unavailable bytes stay `UNKNOWN` rather than becoming `PASS`.

The adapter's canonicalization is the producer-defined
`mncs-rights-provenance/rfc8785-compatible-v0.3` transport profile. It matches
the current producer implementation, including finite-number rendering and
UTF-16BE object-key ordering; it is intentionally not described as a general
RFC 8785 implementation. Tests compare consumer bytes with the checked-out
producer and exercise a numeric/Unicode vector. The local derivation relation
set is copied from the v0.3 producer schema only to reject malformed
transport; Commons and MNCDS retain semantic ownership.

The six-repository fixed-revision canary is defined in
`family-contracts.json` and runs in
`.github/workflows/family-contract-canary.yml`. Its checked revisions are
deterministic compatibility inputs. A future moving-head drift job would be a
different evidence class and must not be used as reproducibility proof. The
implemented `moving-head-family-drift.yml` resolves current `main` heads to a
candidate SHA set and preserves this distinction; `family-contract-advance.yml`
provides the manual review path without mutating the fixed contract.

## Owner-native family integration runner

`scripts/run_family_contracts.py` is the fixed/candidate integration harness.
It verifies that every checkout and declared artifact is at the exact contract
SHA, then invokes the owner surfaces and writes both native reports and
independent check-results:

- MNCS standard manifest validation and MNCDS development-record validation;
- rights/provenance JSON validation;
- Commons' `scripts/validate_compat.py` plus its family producer registry;
- `mncs-language` `source-study` for the family boundary, rights projection,
  and ChangeSet pressure programs;
- Forge's native Forge Cell document validator and assurance assessor.

The runner does not recreate any of those semantics. It preserves native
digests, producer revisions, and unresolved details in the evidence bundle.
The current source studies report unresolved compiler obligations and the
reference Forge Cell reports unmet process isolation, so those projections are
`UNKNOWN` until their owning repositories establish more. That is deliberate
pressure evidence rather than an Actions-level waiver.

## Forge assurance projection

The Forge adapter preserves Forge's requested, enforced, and unmet assurance
sets, policy binding, process-isolation status, attestation record, and
declared limitations in an `assurance_projection`. This is a typed projection
of the Forge-owned assessor, not a second assurance implementation. In
particular, a policy-bound record with unmet process isolation stays
`UNKNOWN`; a transport digest cannot turn it into a sandbox or attestation
claim. The projection scope explicitly excludes kernel and attestation
inference.

## Capability gaps and promotion boundaries

The current MNCS Language and MNCDS checkouts expose capability-gap and
promotion-boundary material, but no stable transport contract that
`mncs-actions` can validate without becoming a second semantic owner. No
local schema is invented here. Once an owning contract is stable, its native
report can be adapted through `additional-checks`; until then, providers may
carry the existing records as evidence and must leave unresolved capability
or promotion obligations explicit. A capability gap is evidence of a missing
capability, never permission to omit a required check, and a runner or badge
never grants promotion authority.
