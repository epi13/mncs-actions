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

The fixed canary now has a bounded three-stage topology:

1. one matrix job checks out and runs exactly one owner-native producer;
2. the job uploads only its own `family-producer-output/1` envelope, with
   SHA-256 bindings for every native and check-result file;
3. a fresh assembler job downloads those envelopes, validates membership and
   revisions, and runs only Actions-side adapters before aggregation.

The aggregator does not check out or import family repositories. A candidate
producer can falsify its own report, but it cannot overwrite a sibling's
artifact or the final aggregate through the workflow. This is still a hosted
runner boundary rather than a kernel sandbox: code in a producer job can
tamper with that job's workspace and its own claimed output, so the result is
evidence requiring owner semantics and independent review, not attestation.

## Moving-head drift observation

`moving-head-family-drift.yml` is a separate evidence class. On a schedule or
manual dispatch it resolves each family repository's `main` head to a full
commit SHA, writes `mncs-actions.family-contract-candidate/1`, checks out
exactly those SHAs, and runs the owner-native contract runner. The candidate is
tied to the SHA-256 digest of the fixed contract it was compared against. It
never edits `family-contracts.json`, and drift `UNKNOWN` is not promoted to
`PASS`.

The descriptor-driven runner covers the standard validator, rights/provenance,
MNCDS, Commons' compatibility validator, three `mncs-language` source studies,
and Forge Cell validation/assurance. Native reports remain in the producer
artifact; adapters project their result into `mncs.check-result/1`. Unresolved
compiler obligations and unmet Forge isolation therefore remain visible as
`UNKNOWN`.

`family-integration-evidence/1` binds the mode, exact contract and descriptor
digests, every family revision, every check's producer/revision/digest/path,
authority mapping, promotion prohibition, and execution topology. The
corresponding `development-pressure-evidence/1` bundle gives each UNKNOWN an
obligation key, pressure identity, owner, category, claim, limitation,
reproducer, references, affected surfaces, and history fields. It follows the
MNCDS `DevelopmentPressure` vocabulary; Actions transports and correlates the
observation but does not define rights, language, assurance, or promotion
semantics.

## Explicit advancement path

`family-contract-advance.yml` is a manual candidate-generation and review
workflow. The advancement sequence is:

1. resolve moving heads into a candidate and run the moving-head contract set;
2. inspect the candidate and all evidence, including each `PASS`/`FAIL`/
   `UNKNOWN` result and exact producer SHA;
3. use `scripts/family_contracts.py promote` to write a separate proposed fixed
   document;
4. review and merge the explicit `family-contracts.json` change;
5. rerun the fixed-revision canary and retain its evidence.

No workflow step silently performs steps 3 or 4. The fixed canary remains the
authoritative compatibility boundary after review; drift evidence is input to
that review, not a replacement for it.

## Descriptor and trust boundary rules

`family-producer-descriptors.json` is deliberately constrained to known
operation names, known adapter IDs, safe input/artifact paths, expected check
membership, and required capabilities. An unknown operation or executable
request fails closed. The descriptor registry is currently the compatibility
carrier while family owners converge on native declarations; the next
coordination increment can move the same schema into each owner repository
without changing the artifact protocol.

The moving-head workflow uses the same split topology. Candidate resolution,
promotion, and evidence assembly are separate concerns. All workflow jobs use
read-only contents permissions and `persist-credentials: false`; no candidate
job can update `family-contracts.json` or promotion state.
