# Family verification workflow

`.github/workflows/mncs-family-verify.yml` is the first practical
reusable family verification workflow:

```yaml
jobs:
  mncs:
    uses: epi13/mncs-actions/.github/workflows/mncs-family-verify.yml@<pinned-sha>
    with:
      required-checks: mncs-validation,rights-provenance,project-tests
      additional-checks: .mncs/changeset-check.json .mncs/compiler-check.json
```

Pin an immutable commit SHA (see `revision-coherence.md`); never `@main`.

- Each provider (`mncs-command`, `rights-command`, `project-command`)
  runs via `actions/run-check` and emits an independent check plus receipt.
  Internal actions are pinned to a synchronized immutable SHA so a
  caller pinning workflow revision X executes exactly action revision X
  (never `./actions/...`, which would resolve inside the caller repo,
  and never a floating branch).
- `actions/aggregate` composes the declared `required-checks` /
  `optional-checks` boundary into one verdict plus manifest. Only result
  files for providers that actually ran are listed: empty commands mean
  intentionally absent (absent optional = no effect; absent required =
  `UNKNOWN`), while a listed-but-missing file means broken execution
  (`INVALID`/`NOT_ESTABLISHED`).
- Leave a command empty to mark that provider not applicable. Absence is
  never recorded as PASS; only the required set decides.
- `additional-checks` accepts whitespace/comma-separated paths to already
  established `mncs.check-result/1` documents. This is the extensibility seam
  for compiler, backend, ChangeSet, Commons, Forge, capability-gap, and
  repository-specific providers. Every supplied id must be explicitly listed
  in `required-checks` or `optional-checks`; duplicate ids, overlapping
  declarations, malformed results, and unsafe paths fail closed. A provider
  name is carried as evidence and gains no authority merely by appearing in
  the workflow.
- Role guidance:
  - language-heavy: MNCS validation + compiler/backend (scoped) +
    rights/provenance + project tests required; ChangeSet when applicable.
  - docs/spec: contract + rights/provenance + link/schema required;
    backend tests not applicable (absent, not PASS).
  - Forge: MNCS validation + rights/provenance + pressure contract +
    bounded evaluator required/scoped; ChangeSet when applicable.

## ChangeSet / coordination bridge

`adapters/changeset_adapter.py` consumes the currently published
`mncs-rights-provenance` v0.3 lineage record, which is the first machine
transport carrying the MNCDS/Commons ChangeSet relationships. MNCDS remains
the owner of ChangeSet semantics and Commons remains the owner of coordination
record exchange. The adapter only checks the bridge mechanically: supported
lineage revision and content digest, GitHub repository identities and full
participant commit SHAs, duplicate participants, relationship vocabulary,
safe paths, and SHA-256 evidence references. Optional expected
repository-to-revision bindings and local evidence-byte verification are
available for canaries.

It emits `changeset-coordination` as an independent `mncs.check-result/1`.
Malformed, contradictory, or unverifiable input emits no check-result and is
therefore `NOT_ESTABLISHED`/`INVALID`; a valid record with declared unresolved
coordination fields is `UNKNOWN`. It never decides promotion or interprets a
Commons relationship.

Public PR validation stays on GitHub-hosted runners. Trusted experiment
dispatch is a separate trust domain and never executes untrusted PR code
on privileged workers. Evidence records runner identity but grants no
promotion authority; Forge/MNCDS/Commons own promotion semantics.

## Fixed-revision family canary

`family-contract-canary.yml` is the CI integration path for the live sibling
contracts. It checks out MNCS, rights/provenance, MNCDS, Commons,
mncs-language, and Forge at the exact SHAs in `family-contracts.json`, then
runs the adapter canaries and verifies the checked-out contract artifacts.
The canary fails closed when a required checkout or test is absent; a passing
job therefore means the fixed revisions were actually exercised, not that a
local test suite happened to skip unavailable repositories.

This job is deterministic with respect to sibling revisions and action
dependencies. It still runs on `ubuntu-latest`, whose image and toolchain are
not immutable, and its evidence is therefore bounded rather than absolute.
Moving sibling heads are not silently substituted for the recorded revisions.
