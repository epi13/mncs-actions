# Language pressure

`pressure/family-boundary.mncs` expresses required-check dominance
(`FAIL > UNKNOWN > PASS`) with `mncs.core.status.v1`, mirroring
`lib/mncs_actions.py::aggregate_verdict`.

`pressure/rights-projection.mncs` expresses the pure rights/provenance
projection core (`is_coherent`, `project`, `apply_binding`) with
`mncs.core.status.v1`, mirroring
`lib/mncs_actions.py::classify_rights_report`. `tests/test_mncs_pressure.py`
pins every outcome arm mechanically so host and MNCS cannot drift apart.

What MNCS can express today:

- pure verdict combination (total, effect-free lattice join)
- optional-observation carriage without deciding the boundary
- rights outcome projection over a closed enum vocabulary
- coherence gating (contradictory reports establish no claim)
- binding-failure downgrade (PASS + mismatch -> FAIL)

Explicit host escape (no safe MNCS surface yet):

- process execution (provider commands)
- filesystem access (result/evidence files)
- SHA-256 hashing and JSON canonicalization
- GitHub environment capture (`GITHUB_*`, `RUNNER_*`)
- network/artifact upload
- unbounded text handling: digest-format and safe-path checks over
  arbitrary strings need bounded text/byte abstractions before they can
  move into MNCS cleanly (capability-gap note; the host validates with
  `is_safe_relative_path` + hex patterns and rejects malformed bindings)

If the language later gains those surfaces observably, move them into
MNCS and verify through this same evidence system. Do not weaken
determinism or evidence quality to inflate MNCS percentage.

`pressure/changeset-boundary.mncs` pressure-tests the pure projection of a
mechanically complete versus incomplete coordination record. JSON parsing,
canonical hashing, filesystem confinement, and evidence-byte verification
remain host-side until the language exposes safe bounded text/byte and IO
capabilities.

`pressure/artifact-envelope-boundary.mncs` adds the next membrane pressure
case. It expresses only the pure tri-state projection used when declared and
observed membership statuses are combined; the v2 host protocol still owns
bounded byte enumeration, path identity, links, and SHA-256 verification.
The family language producer runs this source study alongside the existing
family, rights, and ChangeSet studies. This keeps the pressure loop concrete
without pretending that immature filesystem or byte capabilities are already
available in MNCS.
