# Revision coherence

Desired invariant:

```text
caller pins mncs-actions revision X
              |
              v
workflow revision X
              |
              v
internal action revision X
```

A historical workflow revision must not silently execute a newer action
implementation.

## Why this is hard on GitHub Actions

- `uses:` requires a literal `<repo>/<path>@<ref>`; expressions and
  variables are not expanded there. The workflow cannot say
  `uses: epi13/mncs-actions/actions/run-check@${{ <own-ref> }}`.
- There is no built-in context exposing the reusable workflow's own
  pinned ref to its steps, so the workflow cannot check out or dispatch
  to "itself" dynamically.
- `./actions/...` inside a reusable workflow resolves to the CALLER
  repository, not to mncs-actions, so colocating via relative paths is
  broken for every real cross-repository caller.
- Checking out mncs-actions into the caller workspace and then using
  `./._mncs-actions/actions/...` reduces N pins to one checkout pin but
  does not remove the literal pin: the checkout `ref:` is still a literal
  that can drift from the workflow revision, while adding checkout
  ordering, path, and working-directory complexity. It was rejected as
  more moving parts for the same fundamental limitation.

## Chosen strategy: synchronized immutable SHA pins

Internal actions are referenced as:

```yaml
uses: epi13/mncs-actions/actions/run-check@<40-hex-sha>
uses: epi13/mncs-actions/actions/aggregate@<40-hex-sha>
```

with all pins identical. Rules:

- Pins are full-length (40-char) commit SHAs, never branches (`@main`),
  never mutable major tags (`@v1`). SHAs are immutable; branches and
  major tags float.
- `scripts/sync-pins.sh <release-sha>` rewrites every `uses:`
  self-reference to the given SHA. It touches only `uses:` lines; doc
  comments use the placeholder `@<pinned-sha>`.
- `tests/test_revision_coherence.py` fails on any floating pin
  (`@main`, `@master`, `@vN`, short SHAs) and on skew between pins.
- Release process: merge the release, run
  `scripts/sync-pins.sh <release-commit-sha>`, commit the result, tag
  the release. At any tagged release X, workflow X executes exactly
  action X.
- Between releases, `main` may lag (pins point at the last release
  commit, which is older than HEAD). That state is still reproducible
  and safe: workflow X always executes exactly the implementation baked
  at X -- never newer. It is never floating.
- `revision-binding.json` records the same baked implementation SHA and the
  action directories covered by the binding. Runtime evidence carries this
  annotation in receipt inputs and the manifest boundary block. A caller may
  additionally pass its carrier revision; that value is caller-asserted
  provenance, not an independently verified fact.

Callers pin the same way:

```yaml
uses: epi13/mncs-actions/.github/workflows/mncs-family-verify.yml@<40-hex-sha>
```

## What the tests prove

- No internal `uses:` self-reference floats on a branch or tag.
- All internal pins are the same immutable SHA.
- Re-running `scripts/sync-pins.sh` with the baked SHA is a no-op
  (single source of truth, no hidden pins or stale binding file).
- Examples document SHA pinning and never `@main` for production use.
- `scripts/check-action-pins.py` scans executable workflow and composite-action
  YAML, requiring every remote `uses:` reference (including third-party
  actions) to use a lowercase full commit SHA. Release/tag comments next to
  those lines preserve maintenance readability. Development package versions
  are pinned in `requirements-dev.txt`; the hosted runner image remains
  mutable and is recorded as provenance rather than misrepresented as pinned.

## Cross-repository two-step pin procedure

Downstream pins cannot name upstream revisions that do not exist yet, so
coordinated promotion work lands in two steps with no floating refs at any
point:

1. Upstream owner repositories merge first (MNCDS, MNCS, Commons). Their
   merge commits are immutable the moment they land.
2. `mncs-actions` then advances `family-contracts.json` and any hardcoded
   canary checkout refs to those merge SHAs, in the same branch that
   consumes the new contracts, before its own merge.
3. Repositories that pin `mncs-actions` transport (for example the MNCDS
   CI transport job, or a promotion-dogfood reusable-workflow pin) advance
   to the `mncs-actions` merge SHA in a fast follow-up. The previously
   pinned commit remains immutable and valid; the follow-up only moves to
   newer transport.

At every step each repository references one coherent immutable graph of
exact revisions. There is no self-referential loop: `mncs-actions` never
pins a revision of itself that does not exist yet, and consumers never
float.

## Future evolution

If GitHub ever provides the reusable workflow's own ref to its steps
(or allows expressions in `uses:`), the sync script can be replaced by
a dynamic self-reference. Until then, synchronized immutable pins plus
a regression test are the least fragile reproducible mechanism.
