# Contributing

This repository is an experimental coordination layer for the MNCS family. Contributions should make automation more deterministic, inspectable, and useful to downstream repositories.

## Before changing a contract

- Identify the owning repository and contract boundary.
- Decide whether the change is additive, corrective, or breaking.
- Update the schema version or compatibility notes when consumers could observe a breaking change.
- Include a fixture or workflow test for the behavior.
- Preserve UNKNOWN when evidence is insufficient; do not collapse uncertainty into success.

## Action changes

Each action should:

- expose explicit inputs and outputs;
- avoid hidden network access and undeclared mutation;
- produce machine-readable evidence when it makes a decision;
- document its runner and permission assumptions;
- fail closed on malformed input;
- keep security-sensitive policy in the owning repository.

Use pinned action references for external actions in production workflows. Do not add credentials, tokens, or experimental-worker access to a public pull-request path without an explicit trust boundary.

## Pull requests

Describe:

1. the contract or workflow being changed;
2. the evidence produced by the change;
3. compatibility impact on MNCS-family repositories;
4. how the change was tested;
5. any capability gap or host-language escape introduced.

Small, focused pull requests are preferred because these actions become shared infrastructure.
