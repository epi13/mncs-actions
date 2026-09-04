# Security

Please do not disclose security-sensitive issues in a public issue.

Use GitHub's private vulnerability reporting for this repository when available. If that is unavailable, contact the repository owner privately with:

- the affected action or workflow;
- the minimum reproduction;
- the security impact;
- whether the issue affects GitHub-hosted or self-hosted runners.

## Reusable workflow and provider trust model

- Provider `command` inputs are workflow-author controlled. Never forward
  untrusted PR/fork content (titles, bodies, branch names) into a command;
  GitHub expressions in `command` and shell interpolation of workflow
  inputs are the caller's responsibility. The actions quote all
  filesystem inputs via environment variables and fixed output filenames.
- `result-file` / `evidence-directory` / `checks` entries are
  confinement-checked: absolute escape and `..` traversal are rejected,
  and artifact content never controls write paths.
- Artifact `name` inputs flow only to `actions/upload-artifact` (not to
  shells) and workflows run with `contents: read` least privilege.
- Fork PRs run on GitHub-hosted runners only. Never execute untrusted PR
  code on privileged self-hosted MNCS workers; hardware-gated coverage
  that is unavailable must surface as `UNKNOWN` (or `FAIL` per boundary),
  never silent `PASS`.
- Provenance (`repository`, `ref`, `commit`, `workflow`, `run_id`, `actor`,
  `event`, `runner`) comes from the GitHub runner environment, not from
  provider-generated data, and is carried verbatim into receipts/manifests.

## Self-hosted runners

Never allow untrusted pull-request code to execute on a privileged self-hosted runner. A runner can expose the filesystem, environment, network, and credentials made available to its host.

In particular:

- keep public validation on isolated, disposable runners;
- separate trusted experiment dispatch from ordinary pull-request verification;
- use least-privilege workflow permissions;
- do not place long-lived credentials on experiment runners;
- treat artifact contents as untrusted input;
- record the runner boundary in provenance when hardware-dependent execution is introduced.

The verification action is evidence plumbing. It does not make an unsafe runner safe.

## Family producer boundary

The fixed and moving-head family canaries run one owner-native producer per
hosted matrix job. Each job has read-only repository permissions,
`persist-credentials: false`, one family checkout, and one producer artifact.
The later assembler job has no family checkout: it verifies the recorded
SHA-256 bytes, descriptor-selected membership, repository identity, and
expected exact revision before it runs Actions adapters and aggregation.

This prevents a producer from silently overwriting sibling evidence, changing
the aggregator's code, or mutating promotion state across jobs. It does not
make the producer job a kernel-level sandbox: owner code can still alter its
own job workspace, lie about domain facts, or exploit a runner vulnerability.
The independent revision binding and artifact validation detect substitution
and malformed transport, but they do not establish semantic truth or
attestation. Moving-head results therefore remain observations and never gain
fixed-contract or promotion authority.
