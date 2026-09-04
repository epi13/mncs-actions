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
