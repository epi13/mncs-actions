# Family verification workflow

`.github/workflows/mncs-family-verify.yml` is the first practical
reusable family verification workflow:

```yaml
jobs:
  mncs:
    uses: epi13/mncs-actions/.github/workflows/mncs-family-verify.yml@<pinned-revision>
    with:
      required-checks: mncs-validation,rights-provenance,project-tests
```

- Each provider (`mncs-command`, `rights-command`, `project-command`)
  runs via `actions/run-check` and emits an independent check plus receipt.
  Internal actions use `epi13/mncs-actions/actions/...@<revision>` so a
  caller pinning the workflow gets the same pinned actions (never
  `./actions/...`, which would resolve inside the caller repo).
- `actions/aggregate` composes the declared `required-checks` /
  `optional-checks` boundary into one verdict plus manifest. Only result
  files for providers that actually ran are listed: empty commands mean
  intentionally absent (absent optional = no effect; absent required =
  `UNKNOWN`), while a listed-but-missing file means broken execution
  (`INVALID`/`NOT_ESTABLISHED`).
- Leave a command empty to mark that provider not applicable. Absence is
  never recorded as PASS; only the required set decides.
- Role guidance:
  - language-heavy: MNCS validation + compiler/backend (scoped) +
    rights/provenance + project tests required; ChangeSet when applicable.
  - docs/spec: contract + rights/provenance + link/schema required;
    backend tests not applicable (absent, not PASS).
  - Forge: MNCS validation + rights/provenance + pressure contract +
    bounded evaluator required/scoped; ChangeSet when applicable.

Public PR validation stays on GitHub-hosted runners. Trusted experiment
dispatch is a separate trust domain and never executes untrusted PR code
on privileged workers. Evidence records runner identity but grants no
promotion authority; Forge/MNCDS/Commons own promotion semantics.
