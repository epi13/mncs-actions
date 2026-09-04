# Architecture

## The role of mncs-actions

The repository is a deterministic execution and transport layer for GitHub-hosted development. It should make a decision observable without claiming to own the meaning of that decision.

~~~text
MNCS-family repository
        |
        v
mncs-actions orchestration
        |
        +--> MNCS / standard validation (mncs-validator-rs adapter)
        |
        +--> mncs-language compile / semantic / backend checks
        |
        +--> rights & provenance validation (mncs-rp adapter)
        |
        +--> ChangeSet / coordination validation (bounded lineage adapter)
        |
        +--> project-specific verification
        |
        +--> future Forge bounded evaluation
        |
        v
structured independent check results (check-result/1)
        |
        v
aggregate verdict PASS / FAIL / UNKNOWN (aggregate-result/1)
        |
        v
evidence graph / manifest / execution receipts
        |
        v
GitHub artifact / reusable workflow output
~~~

Each subsystem retains ownership of its own semantics; `mncs-actions`
orchestrates, invokes, transports, aggregates, and packages evidence.

## Contract ownership

- MNCS and MNCDS define the standard and development-pressure rules.
- Commons defines shared coordination records and evidence relationships.
- mncs-rights-provenance defines rights, identity, and provenance semantics.
- mncs-language defines the compiler and language capability surface.
- Forge supplies pressure cases and candidate implementations.
- mncs-actions connects those decisions to GitHub events and artifacts.

When a workflow needs information from another repository, it should reference that repository's published contract or emitted artifact. It should not copy the semantics into a shell script merely for convenience.

The fixed-revision family canary makes that rule executable: it consumes the
six repository identities and revisions in `family-contracts.json`, exercises
the currently published adapter inputs, and fails when a declared canary is
skipped. It is a compatibility observation for those revisions, not a
moving-head or promotion claim. `ubuntu-latest` remains an explicitly
documented hosted-runner boundary.

## Fixed, drift, and advancement boundaries

The family has three deliberately separate revision states:

- fixed: the six full SHAs in `family-contracts.json`, used by the
  deterministic compatibility canary;
- moving-head: a scheduled observation that resolves current `main` heads to a
  candidate document and checks out those exact candidate SHAs;
- advancement: a manually reviewed candidate that may become a new fixed set
  only through an explicit contract-file change and the fixed canary.

The candidate carries the fixed-contract digest and both base and candidate
SHA for every repository. This makes a moving-head result auditable without
allowing it to mutate the family contract or masquerade as fixed evidence.

The owner-native runner is an integration seam, not a policy replacement:
MNCS/MNCDS validation, rights/provenance validation, Commons compatibility,
mncs-language source studies, and Forge Cell checks each produce an independent
`mncs.check-result/1`. Native reports and exact producer revisions are retained
in the evidence bundle. PASS/FAIL/UNKNOWN remains explicit; malformed or
unavailable execution is not rewritten as a positive claim.

## The family security membrane

A participant does not gain authoritative MNCS standing merely by running
code. What crosses the family boundary must conform to the bounded v2
producer envelope, carry exact producer/revision/descriptor/contract and
file bindings, survive independent assembler validation, and remain subject
to domain authority and promotion rules. This establishes transport
integrity and reproducibility of the observation; it does not establish
semantic truth, authorization, rights correctness, or runner isolation.

The envelope validator rejects undeclared files, duplicate or alias paths,
kind substitutions, traversal, links and special files, digest/size changes,
foreign check identities, and pathological JSON. Owner-native execution is
therefore constrained at the conformance membrane, but it is not a sandbox:
producer code can still alter its own workspace or lie about domain facts.
Forge's assurance projection keeps policy binding, nonce/record evidence, and
unmet isolation visible without claiming kernel-level isolation or
attestation. `UNKNOWN` remains evidence; it is not silently upgraded.

## Extensible family composition

The three convenience provider inputs in the reusable workflow are retained
for compatibility. New providers do not require a new hard-coded workflow
role: a caller runs the domain-owned command, validates/packages its native
output into `mncs.check-result/1`, and lists the resulting path in
`additional-checks`. The family workflow requires every supplied check id to
be declared in exactly one of `required-checks` or `optional-checks`.
Aggregation uses only the declared required set for the boundary; optional
outcomes remain visible in `unresolved`.

~~~text
ChangeSet / coordination record
  +--> repository @ exact commit
  |      +--> independent check-result digest
  +--> rights/provenance record
  +--> Commons relationship reference
  |
  +--> aggregate-result checks[].digest + checks[].path
~~~

This is transport evidence: `mncs-actions` records which bytes were consumed
and how they were bound. MNCDS, Commons, rights/provenance, Forge, and MNCS
retain ownership of the meaning of each native relationship. The current
bridge targets rights/provenance lineage v0.3 because it is the first
published machine record carrying the cross-repository relationship; it is
bounded until MNCDS/Commons publish a dedicated stable ChangeSet contract.

## Verdict semantics

| Verdict | Meaning | Default action behavior |
| --- | --- | --- |
| PASS | Required checks established the claimed condition | Action succeeds |
| FAIL | A required check established a negative result | Action fails |
| UNKNOWN | Evidence was insufficient to establish either result under a valid contract | Succeeds only when fail-on-unknown is false |
| INVALID | No valid claim established (missing/malformed/invalid execution) | Always fails; receipt still emitted |

Malformed output is distinct from UNKNOWN and fails the action. This prevents missing or structurally invalid evidence from being mistaken for uncertainty. See `receipt-and-claim.md`.

## Evidence and provenance

The action copies the project result into a stable evidence directory, always writes an execution receipt, and — only for established claims — generates a manifest. The manifest records GitHub execution context, SHA-256 digests, a receipt reference, and generic family references (see `evidence.md`).

The timestamp in the manifest makes each execution record distinct. Consumers that need reproducibility should hash the result and referenced evidence independently and retain the exact commit, workflow inputs, runner class, and tool versions.

## Contract ownership

- MNCS and MNCDS define the standard and development-pressure rules.
- Commons defines shared coordination records and evidence relationships.
- mncs-rights-provenance defines rights, identity, and provenance semantics.
- mncs-language defines the compiler and language capability surface.
- Forge supplies pressure cases and candidate implementations.
- mncs-actions connects those decisions to GitHub events and artifacts.

When a workflow needs information from another repository, it should reference that repository's published contract or emitted artifact. It should not copy the semantics into a shell script merely for convenience.

## Verdict semantics

| Verdict | Meaning | Default action behavior |
| --- | --- | --- |
| PASS | Required checks established the claimed condition | Action succeeds |
| FAIL | A required check established a negative result | Action fails |
| UNKNOWN | Evidence was insufficient to establish either result | Succeeds only when fail-on-unknown is false |

Malformed output is distinct from UNKNOWN and fails the action. This prevents missing or structurally invalid evidence from being mistaken for uncertainty.

## Evidence and provenance

The action copies the project result into a stable evidence directory and generates a manifest. The manifest currently records GitHub execution context and SHA-256 digests. Later versions can add references defined by mncs-rights-provenance and Commons without silently changing this initial contract.

The timestamp in the manifest makes each execution record distinct. Consumers that need reproducibility should hash the result and referenced evidence independently and retain the exact commit, workflow inputs, runner class, and tool versions.

## Trusted execution boundary

Public pull-request verification and hardware-dependent experiments are different trust domains:

- ordinary pull-request checks should run on isolated GitHub-hosted runners;
- trusted experiment dispatch should use explicit workflow and repository permissions;
- self-hosted runners should be isolated from untrusted code and long-lived credentials;
- evidence from a runner is an input to a promotion decision, not authority by itself.

This repository can carry the evidence across the boundary, but the owning standard must define who or what may rely on it.
