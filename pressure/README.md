# Language pressure

`pressure/family-boundary.mncs` expresses required-check dominance
(`FAIL > UNKNOWN > PASS`) with `mncs.core.status.v1`, mirroring
`lib/mncs_actions.py::aggregate_verdict`.

What MNCS can express today:

- pure verdict combination (total, effect-free lattice join)
- optional-observation carriage without deciding the boundary

Explicit host escape (no safe MNCS surface yet):

- process execution (provider commands)
- filesystem access (result/evidence files)
- SHA-256 hashing and JSON canonicalization
- GitHub environment capture (`GITHUB_*`, `RUNNER_*`)
- network/artifact upload

If the language later gains those surfaces observably, move them into
MNCS and verify through this same evidence system. Do not weaken
determinism or evidence quality to inflate MNCS percentage.
