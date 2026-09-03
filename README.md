# mncs-actions

Machine-native GitHub Actions and reusable automation primitives for verification, evidence, provenance, coordination, and language-pressure workflows across the MNCS ecosystem.

> **Status:** experimental foundation. The contracts in this repository are versioned, but they are not yet presented as a completed MNCS standard.

## Purpose

mncs-actions is the GitHub integration layer around the MNCS family. It turns repository events into repeatable, inspectable operations while leaving canonical semantics to the repositories that own them.

Primitives:

- `actions/verify`: run one project verifier, always emit an execution
  receipt, and package an evidence manifest only when a valid claim is
  established (`PASS`/`FAIL`/`UNKNOWN`, else `INVALID`).
- `actions/run-check`: run one family-owned provider and package its
  `check-result` plus receipt for later aggregation.
- `actions/aggregate`: compose validated check-results into one
  aggregate verdict with explicit required/optional policy.
- `actions/render-badge`: render a deterministic SVG presentation badge and
  a machine-readable sidecar bound to aggregate evidence.
- `.github/workflows/mncs-family-verify.yml`: reusable family workflow
  composing the above (`mncs-validation`, `rights-provenance`,
  `project-tests`, optional backends, and precomputed additional checks).
  Internal actions are pinned to a
  synchronized immutable commit SHA so a caller pinning workflow revision
  X executes exactly action revision X (never `./actions/...`, which
  would resolve inside the caller repo, and never `@main`; see
  `docs/revision-coherence.md`); empty provider commands mean
  intentionally absent (absent optional = no effect, absent required =
  `UNKNOWN`), while explicitly listed-but-missing files stay `INVALID`.

~~~yaml
- uses: epi13/mncs-actions/actions/verify@<pinned-sha>
  with:
    command: ./scripts/verify-mncs.sh
    result-file: .mncs/verification-result.json
    evidence-directory: .mncs/evidence
    fail-on-unknown: true
~~~

Pin an immutable reviewed commit SHA in production (see
`docs/revision-coherence.md`); never `@main` or another floating ref.

## Result contract

A verifier must write a JSON result before the action finishes:

~~~json
{
  "schema_version": "mncs.verification-result/1",
  "verdict": "PASS",
  "summary": "All required checks passed.",
  "checks": [
    {
      "id": "compile",
      "verdict": "PASS",
      "summary": "The project compiled successfully."
    }
  ]
}
~~~

The action validates the contract with the canonical `lib/mncs_actions.py`
validator, copies the result into an evidence directory, writes an
execution receipt (`execution-receipt.json`), and — only when the claim is
valid — writes an evidence manifest containing:

- the result digest;
- repository, ref, commit, workflow, run, actor, and event provenance;
- the command exit code;
- the final verification verdict;
- a reference to the sibling execution receipt.

UNKNOWN is intentionally not converted into PASS. It means that the system could not establish a positive or negative result under a valid contract. Callers decide whether an unknown result is acceptable for a particular boundary.

Malformed/missing/invalid results are `NOT_ESTABLISHED` (`INVALID` at the
action boundary) with a valid receipt but no manifest and no fabricated
UNKNOWN. A valid FAIL result is also a failing action. A valid UNKNOWN result succeeds only when fail-on-unknown is false.

`manifest-digest` is the correct digest output; `provenance-digest` is a
deprecated alias with the identical value (it always hashed the whole
canonical manifest, not only provenance).

## Composable checks

Providers emit `mncs.check-result/1` (`schemas/check-result.schema.json`):

~~~json
{
  "schema_version": "mncs.check-result/1",
  "id": "rights-provenance",
  "provider": "mncs-rights-provenance",
  "verdict": "UNKNOWN",
  "summary": "rights outcome review-required -> UNKNOWN",
  "unresolved": ["human review outstanding"]
}
~~~

`actions/aggregate` composes them (`schemas/aggregate-result.schema.json`):
required FAIL -> FAIL; required UNKNOWN/missing -> UNKNOWN; all required
PASS -> PASS. Optional UNKNOWN stays visible in `unresolved`. The reusable
family workflow accepts additional provider result paths without a new
hard-coded role and requires every supplied id to be explicitly declared as
required or optional.

Adapters (`adapters/`): `rights_adapter.py` maps
`pass -> PASS` (requires identity match), `blocked`/`invalid -> FAIL`
(valid negative), `pass-with-findings`/`review-required`/`unknown ->
UNKNOWN`, unrecognized vocabulary -> `UNKNOWN` with a drift note;
missing/contradictory reports establish no claim (`NOT_ESTABLISHED`,
never fabricated); `validator_adapter.py` maps `mncs-rs` `valid` +
`computed_status` directly, `valid=false -> FAIL`; `changeset_adapter.py`
mechanically validates the current rights-lineage ChangeSet bridge while
leaving MNCDS/Commons semantics with their owners. See `docs/adapters.md`.

## Repository layout

~~~text
actions/verify/       Single-verifier action (receipt + manifest)
actions/run-check/    One provider check + receipt
actions/aggregate/    Required/optional composition + receipt + manifest
actions/render-badge/ Deterministic SVG badge + machine-readable sidecar
adapters/             Family mappings (own no policy)
lib/mncs_actions.py   Canonical validation/aggregation implementation
schemas/              Versioned machine-readable contracts
tests/                Deterministic fixture-backed matrix
examples/             Consumer workflow examples
docs/                 Architecture and integration notes
.github/workflows/    Repository self-tests + reusable family workflow
~~~

## Ecosystem boundary

| Concern | Owning repository | Role of this repository |
| --- | --- | --- |
| Development and promotion rules | MNCDS / MNCS | Invoke and transport checks |
| Language and compiler capability | mncs-language | Surface compiler results and capability gaps |
| Cross-repository coordination | Commons | Emit records that can be consumed by coordination workflows |
| Rights and provenance semantics | mncs-rights-provenance | Carry references and digests; do not redefine authorization |
| Pressure experiments and candidate work | Forge | Run bounded verification and preserve evidence |
| GitHub execution glue | mncs-actions | Provide reusable Actions and workflows |

The action should not silently invent canonical evidence relationships, rights, or promotion authority. Those belong to the appropriate MNCS-family contract.

## Presentation badge

`actions/render-badge` is deliberately downstream of aggregation. It maps an
established `PASS`, `FAIL`, or `UNKNOWN` verdict to a deterministic SVG and
emits `mncs.badge/1` JSON beside it. If no claim was established, callers may
render `INVALID`; that is a presentation state, never a claim verdict.

The sidecar can carry the aggregate-result and manifest digests, subject
repository/revision, declared boundary, and revision-binding annotations. It
contains no timestamp, so identical inputs produce byte-identical output.

```yaml
- uses: epi13/mncs-actions/actions/render-badge@<pinned-sha>
  with:
    verdict: ${{ steps.aggregate.outputs.verdict || 'INVALID' }}
    aggregate-digest: ${{ steps.aggregate.outputs.aggregate-digest }}
    manifest-digest: ${{ steps.aggregate.outputs.manifest-digest }}
    output-file: .mncs/mncs-badge.svg
    sidecar-file: .mncs/mncs-badge.json
```

## Development

No runtime dependency beyond Bash, Python 3 (stdlib only), and standard GitHub-hosted runner capabilities. Run the local checks with:

~~~bash
bash -n actions/verify/verify.sh
bash -n actions/run-check/run_check.sh
bash -n actions/aggregate/aggregate.sh
python3 -m pytest tests/ -q
~~~

The GitHub workflow exercises the composed actions plus the reusable family workflow against deterministic fixtures.

## Roadmap

1. Stabilize the result and evidence contracts through use in the family repositories.
2. Carry rights/provenance and validator references without duplicating their models (done for v1 adapters; deepen with live integrations).
3. Extend coordination and ChangeSet coverage beyond the bounded rights-lineage bridge when MNCDS/Commons publish a dedicated stable contract (current provider composition seam and mechanical bridge are implemented).
4. Add capability-gap and promotion-boundary actions once their owning contracts are stable.
5. Implement the action logic in MNCS where the language can express it without weakening observability or security (current host-language escape: process execution, filesystem, hashing, GitHub environment, and JSON canonicalization have no safe MNCS expression yet).
