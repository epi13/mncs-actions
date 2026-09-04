# Repository-owned promotion pattern

A participating repository declares its own promotion boundary instead of
inheriting a monolithic global one. Recommended layout (shown here with
this repository's own dogfood):

```text
promotion/
  boundary.json       # mncs-promotion-boundary/0.1 declaration
  authority-map.json  # mncs-authority-map/0.1 trust binding
```

## Declaring

`boundary.json` names required evidence by `check_id` plus owning
`authority`, pins `contract_revision` where the requirement is exact,
lists tolerated obligations explicitly, and requires subject binding.
Only declare authorities that actually apply to the repository; do not
pretend unrelated checks are required.

`authority-map.json` binds each required check id to its exact provider
string and semantic authority. Derive it mechanically where the checks
come from pinned family producer descriptors
(`scripts/authority_map.py`); hand-write it only for repository-local
checks (such as `project-tests`), where the originating project itself
is the authority.

Duplicate `check_id` declarations in the descriptors follow one
mechanical policy (enforced by `scripts/authority_map.py`, which owns no
authority semantics): a field-identical repeat (provider, authority, and
repository attribution, including its absence) deduplicates
deterministically with first-declaration-wins; any repeat differing in
any field -- provider, semantic authority, or repository attribution
(changed, added, or removed) -- is rejected with exit 2. Repository
attribution can neither silently change nor silently disappear through
deduplication. Producers must not depend on accidental behavior here.

## Candidates

A boundary may evaluate the checked-out revision (subject is the run's
`GITHUB_SHA`, as in this repository's dogfood) or a recorded candidate
revision (`promotion/candidate.json` naming an exact commit, as in MNCDS
and MNCS). The candidate form fits repositories whose promotion evidence
is bound before the run: both required checks are stamped for the
candidate, the evaluator rejects any other subject, and advancing the
candidate is a reviewed change that rebinds the evidence set.

## Executing

`mncs-actions` executes the owner-native decisions and transports them:

```yaml
jobs:
  promotion:
    uses: epi13/mncs-actions/.github/workflows/mncs-family-verify.yml@<pinned-sha>
    with:
      required-checks: project-tests,promotion-boundary
      project-command: <run project verification, write .mncs/project-check.json>
      promotion-command: <run the owner-native MNCS evaluator over the
        result files with --boundary promotion/boundary.json
        --authority-map promotion/authority-map.json and the exact
        subject, writing .mncs/promotion-check.json>
      promotion-boundary: <boundary-id>
```

Pin the reusable workflow to an immutable SHA and advance it with the
two-step procedure in `docs/revision-coherence.md`. Pin the evaluator to
its owner revision the same way (sparse checkout at an exact SHA inside
the promotion command, or a checked-out owner tree).

## Trust division

- The boundary and map are repository policy, versioned with the repo.
- Verdict semantics belong to the MNCS evaluator; authority semantics to
  the owning authorities; transport validates carrier shape only.
- `UNKNOWN` stays red when `fail-on-unknown` is true; `FAIL` is always red.

## Adoption

Three repositories dogfood this pattern (all pinned, never `@main`):

- `epi13/mncs-actions` (`promotion/boundary.json`): `project-tests` plus
  its own `promotion-boundary` output over the checked-out revision.
- `epi13/machine-native-complexity-development-specification`
  (`promotion/mncds-promotion.boundary.json`): owner-native development
  record plus candidate-bound obligation set over the recorded candidate.
- `epi13/machine-native-complexity-standard`
  (`promotion/mncs-promotion.boundary.json`): owner-native validation
  gate plus candidate-bound obligation set over the recorded candidate,
  requiring its own promotion output (evaluator skips the self entry;
  aggregation enforces its presence).

Compatibility canaries observe what pinned family checkouts currently
produce and stay UNKNOWN while blockers stand; they never imply
promotability. A promotion gate (`fail-on-unknown: true`) going green
means the subject revision genuinely satisfied the boundary. The full
loop -- development pressure to MNCDS obligations to authoritative
evidence to MNCS promotion to transport/gate to Commons ChangeSet
relation -- is recorded per-repository in each adopter's `docs/promotion.md`.
