# Durable family acceptance proof

An accepted MNCS family graph is a durable machine-verifiable historical
fact. The proof that a graph legitimately crossed the family advancement
boundary must survive the CI workspace that produced it: an external
machine retrieves the proof bundle, recomputes every identity, reruns
the owner-native decision procedures at exact revisions, and reaches
its own PASS/refusal without trusting the historical run.

## The five identities

These are deliberately separate. Confusing any two breaks the model.

1. **Graph identity** (`graph.digest`): SHA-256 over the canonical
   constellation only — schema version, base, members, dependencies.
   Evidence accumulation never mutates it: candidate, validated,
   promoted, and related rebuilds of one constellation share one digest.
2. **Acceptance proof identity** (`proof.proof_digest`): SHA-256 over the
   canonical proof closure (manifest minus itself). Covers members,
   boundary digests, artifact digests, generator revisions, tool digests,
   contracts transition, Commons binding, and the acceptance tier.
3. **Accepted graph binding** (`accepted_graph_digest` + `proof.digest`):
   the accepted file carries the proof digest; the manifest covers the
   accepted bytes minus the proof block. Replay checks both directions,
   so neither side can be swapped.
4. **Commons relationship identity** (`contentDigest`, owner-defined):
   computed with the owner-native Commons canonicalization, validated
   by the owner validator. Transport never invents this digest.
5. **Contract transition** (`contracts.before_digest/after_digest`):
   the exact accepted family-contract change, by canonical digest.
   Members must equal the after-state exactly.

## Subject members vs proof generators

The graph's members are the **subject**: the exact revisions whose
compatibility was evaluated — including the `mncs-actions` member,
which is the orchestration *baseline* the constellation was defined
under. The proof manifest's `generators` block is separate: the exact
revisions of the tools that produced, transformed, related, or verified
the evidence (orchestrator, MNCS evaluator with contract revision,
Commons validator) plus content digests of the tool files actually
invoked. A later transport bugfix changes generators, never what graph
was evaluated; the proof never pretends it was produced by older
transport code.

## Semantic authorities vs transport vs governance

- **mncs-actions orchestrates and transports.** It checks bindings,
  digests, and document linkage. It never decides verdicts.
- **MNCS and each member decide their own domain.** Promotion verdicts
  come only from the owner-native evaluator at the recorded revision;
  obligations only from owner records; relationship validity only from
  the owner Commons validator.
- **Commons relates and publishes; it never decides promotion.** The
  ChangeSet carries exactly one `promotes` edge from the genuine
  promotion result.
- **Human merge is the final governance action.** The machine proposes
  (`advance`, `build-proof`); only a merged PR changes accepted trust
  state. No workflow mutates accepted state.

`advance` refuses a no-op constellation: when the candidate's member
revisions already equal the accepted contracts, there is nothing to
cross the boundary and no bundle is built. `build-proof` reaffirms an
accepted file that predates acceptance records: the bundle copy gains
the recomputed acceptance record while the repository file waits for
human review; replay then checks the enriched copy both directions.

## Acceptance tiers

The boundary's required evidence decides; optional observations never
decide. `core` means the graph crossed the required boundary.
`full` additionally means every boundary-listed optional authority
returned PASS with no open required obligations. Consumers must not
mistake core acceptance for full-family convergence: the tier is
machine-readable in every accepted graph and proof (`acceptance.tier`),
projected by the same pure rule in `family_graph.acceptance_tier`
and `pressure/family-acceptance.mncs`. UNKNOWN is preserved in the
per-check listing, never folded into PASS.

## The bundle

`proof/family-graph-N/` (committed with the advancement):

- `proof.json` — the manifest above.
- `graph.json` — the related graph exactly as evaluated (sanitized:
  no absolute or machine-local paths anywhere).
- `accepted-graph.json` — status accepted, acceptance record, proof block.
- `boundary.json` + `boundary-template.json` — materialized boundary
  and the template bytes; replay requires the operator's own template
  copy as the trust anchor and proves the materialized file is exactly
  template plus graph declaration.
- `authority-map.json`, `coherence.json`, `capability.json`,
  `descriptors.json` — the exact policy inputs used.
- `checks/`, `obligations/` — owner-native evidence by digest.
- `promotion-check.json` — the MNCS claim (bundle-relative ref
  `promotion-check.json`; accepted graphs never carry temp paths).
- `commons-record.json` — the ChangeSet with owner content digest.
- `contracts-before.json`, `contracts-after.json` — the transition.

The inventory is closed: every file except `proof.json` itself is
listed (`accepted-graph.json` via its dedicated binding); extras,
missing files, or digest mismatches refuse replay.

## Replay

```bash
python scripts/family_proof.py verify-accepted \
  --proof proof/family-graph-N \
  --boundary-template promotion/family-advancement.boundary.json \
  --evaluator <exact MNCS evaluator script> \
  --commons-root <exact Commons checkout> \
  [--checkouts-root <member checkouts by name>] \
  [--previous-proof proof/family-graph-N-1]
```

Replay recomputes the proof digest, verifies every artifact digest,
revalidates schemas with owner contracts, reruns the MNCS evaluator
over bundle bytes and byte-compares the claim, revalidates the Commons
record with the owner validator, recomputes the acceptance tier,
walks the predecessor link, and — with `--checkouts-root` — reruns
coherence and anchors every recorded generator revision to exact
`git show rev:path` bytes. Exit 0 prints `replay PASS`; anything else
prints refusals and exits 2. Missing material refuses; nothing
downgrades to PASS.

## Predecessor chain

`family-graph-0 → family-graph-1 → …`: each proof binds the
predecessor graph digest (and its proof digest once a bundle exists;
the genesis link records `null` with the reason documented in the
bundle). Each Commons record chains `predecessorGraph`. Tampering
with a prior accepted graph breaks the next replay. This is a boring
content-addressed chain, not a ledger: no consensus, no voting.

## Commons publication

The bundle's `commons-record.json` is staging, not publication. The
durable home of a family ChangeSet is the Commons repository itself:

```bash
python scripts/family_proof.py publish-commons \
  --proof proof/family-graph-N \
  --commons-checkout <checkout at the recorded Commons revision>
```

`publish-commons` re-derives the owner content digest with the
owner-native Commons code from that checkout, checks the manifest
binding, and writes the bundle bytes verbatim to
`family/changesets/changeset.family-graph-N.json`. It is append-only:
an existing different record refuses; identical bytes restage as a
no-op. The checkout must be at the recorded validator revision, and
merging the staged file is human governance reviewed in Commons. A
Commons-side test revalidates every file under `family/changesets/`
with the owner validator, so publication stays self-checking.

## What stays a host escape (and why)

Digest comparison, JSON canonicalization, Git revision resolution,
subprocess control, and filesystem access remain host responsibilities:
the language exposes no bounded digest-comparison type, and hashing,
checkout, and execution are legitimately host-side. The
`.mncs` decision layer (`pressure/family-acceptance.mncs`) owns the
tier projection, replay gating, and chain-link validity over
host-established booleans; agreement tests pin it to the host mirror.
