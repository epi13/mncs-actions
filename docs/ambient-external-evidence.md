# Ambient external evidence

`mncs-actions` is the family owner of external (CI/GitHub) execution
transport and evidence. The ambient layer answers, without agent
choreography:

```text
which external obligation needs evidence
→ does exact prior evidence already apply
→ is a run already in flight for this exact subject
→ is a fresh dispatch justified (and granted)
→ does the completed receipt satisfy the obligation
```

## Authority boundary

Actions owns:

- GitHub API/process transport (`bin/mncs-actions-remote`)
- execution receipts and evidence envelopes
- external routing/admission/reuse policy
  (`native/mncs/actions/external_evidence.mncs`)
- CI-side check packaging (`actions/run-check`, `actions/verify`,
  `scripts/emit_obligation_check.py`)

Actions does NOT own test semantics (`mncs-test`), debugging
(`mncs-debug`), language semantics, health (`Doctor`), bounded generic
execution (`Forge`), claims (`Environment`), or adaptive strategy
(future RAVEL).

Base operation needs no RAVEL plan. A `mncs.verification-plan/1`
document is honored when supplied (selective verification), but
run-check/verify/test/debug operate plan-free. RAVEL is one optional
plan producer, declared accordingly in `.mncs/project.json`.

## Native policy

`mncs.actions.external_evidence` owns every routing/admission/reuse
decision. The host projects measurements; the module decides. Statuses:

- `current` — exact admitted evidence; the carried verdict stands
- `dispatch_eligible` — justified, awaiting a delegate grant
- `delegated_pending` — an exact run is in flight; attach, never redispatch
- `no_route` — non-external kind or no declared workflow binding
- `unavailable` — dirty, unpublished, or unmeasurable subject
- `deferred` — budget/attempts exhausted or remote unreachable
- `escalate` — explicit-only effects; never dispatched reactively

Laws: dirty worktrees are never remotely verifiable; unpublished
revisions have no runs; workflow green never establishes PASS (only an
ESTABLISHED receipt binding the exact obligation and subject admits);
explicit-only effects (deploy, release, publish, branch/tag mutation,
secrets) never dispatch reactively.

Evidence identity material (host-hashed, family digest): repository,
revision, workflow path, artifact name, check identity.

## Transport

`bin/mncs-actions-remote` is boundary only: `status`, `fetch`,
`validate`, `dispatch`, `probe`. Every subcommand prints one
`mncs.actions.remote-transport/1` envelope; failures are typed
(`auth_missing`, `network_error`, `workflow_missing`, `claim_held`,
...), never prose. Dispatch claims a per-machine ledger entry first so
two sessions rarely launch identical runs; the remote run itself is the
durable dedup key both sessions attach to.

## Declaring a route

An `external_integration` obligation earns a route with an `external`
executor block owned by its own repository:

```json
"executor": {
  "provider": "mncs-actions",
  "kind": "external_integration",
  "entrypoint": "python3 -m pytest tests/ -q",
  "external": {
    "repository": "epi13/mncs-actions",
    "workflow": "ci.yml",
    "job": "obligation-evidence",
    "artifact": "mncs-obligation-evidence",
    "check_identity": "mncs-actions.family-proof.affected-consumer",
    "explicit_only": false
  }
}
```

The named job must execute the declared entrypoint verbatim and package
the outcome through `run-check` with `check-id` set to the obligation
identity, so admission binds exactly. `explicit_only: true` marks
deployment/release-shaped obligations the ambient path must refuse.

Own entrypoints must be honest: a runner that collects zero tests is a
declaration bug, not a PASS. (`mncs-actions` fixed its own vacuous
unittest entrypoint to the real pytest suite in this campaign.)

## Check emission at the CI boundary

`scripts/emit_obligation_check.py` runs the entrypoint and emits the
owner-signed `check-result/1`: exit 0 → PASS, exit nonzero → FAIL,
timeout/launch failure → UNKNOWN (infrastructure, never a product
verdict). It exits 0 whenever a valid check was emitted; the verdict
carries the outcome. This mapping is a host boundary today because CI
runners carry no MNCS toolchain; a native check-emission app is the
recorded follow-up once they do.

## Environment capabilities

| capability | effect | role |
|---|---|---|
| `mncs.actions-external-evidence/1` | read | native policy app |
| `mncs.actions-remote-evidence/1` | read | status/fetch/validate transport |
| `mncs.actions-dispatch/1` | delegate | claim-gated dispatch transport |

## Failure taxonomy

CI outcomes stay typed end to end: semantic PASS/FAIL (owner receipt),
UNKNOWN (valid unknown), NOT_ESTABLISHED (green workflow, invalid or
missing result — never PASS), infrastructure (timeout, cancelled,
transport), unavailable (no auth/network), deferred (bounded retry).

## Dispatch mechanics

GitHub dispatches workflows by branch or tag, never by raw sha. The
caller resolves a remote-tracking ref pointing at the measured
revision and passes both; dispatch refuses without a ref, and the
returned run's `head_sha` is re-checked against the measured revision
so a ref that advanced mid-dispatch never satisfies the old subject.
A programmatic dispatch still needs the workflow's own `on:` triggers
to admit `workflow_dispatch`.

## Live proof (2026-10-02)

Run `36951425405` on `epi13/mncs-actions@2de0a37` (workflow `ci.yml`,
ref `main`), dispatched through `mncs-actions-remote dispatch`:

- dispatch → `ok`, returned `head_sha` exactly the measured subject
- immediate second dispatch → `claim_held` (ledger dedup)
- status observed `in_progress`, then `completed`/`failure`
- fetch of `mncs-obligation-evidence` → `artifact_missing`
  (normalized from gh's generic download failure; a completed run
  without evidence reads as terminally invalid, never retried hourly)
- fetch of `mncs-family-self-test-evidence` + validate → `valid`,
  projected `claim ESTABLISHED / verdict PASS` bound to the exact
  revision and run
- the workflow conclusion was red while the staged artifact claimed
  PASS: per-check evidence stands on its own contract, neither
  direction of the workflow conclusion decides admission; unbound
  artifacts mismatch at the obligation check and are never admitted
