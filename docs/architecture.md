# Architecture

## The role of mncs-actions

The repository is a deterministic execution and transport layer for GitHub-hosted development. It should make a decision observable without claiming to own the meaning of that decision.

~~~text
project verifier
      |
      v
mncs-actions/verify
      |
      +--> normalized verdict
      +--> result digest
      +--> provenance-bearing manifest
      +--> workflow output / artifact
      |
      v
owning MNCS-family contract
~~~

The action is intentionally narrow. It does not decide whether a project should be promoted, whether a person has a right to perform an operation, or whether two repositories are semantically compatible.

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
