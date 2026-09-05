# MNCS presentation badges

`actions/render-badge` is a presentation projection over an already-produced
aggregate verdict. It does not validate or replace the aggregate claim, and a
badge is not evidence by itself.

## State mapping

| Input state | Badge state | Meaning |
| --- | --- | --- |
| `PASS` | `PASS` | Required boundary checks established PASS. |
| `FAIL` | `FAIL` | A required check established FAIL. |
| `UNKNOWN` | `UNKNOWN` | A valid claim exists, but evidence is insufficient for PASS or FAIL. |
| `INVALID` | `INVALID` | No valid claim was established. |

The renderer rejects every other value. In particular, missing or malformed
aggregate output must not be guessed into `UNKNOWN` or `PASS`; callers should
pass `INVALID` when they want that execution state made visible.

## Outputs

The action writes an SVG at `output-file` and a canonical `mncs.badge/1`
sidecar at `sidecar-file`. The sidecar has no timestamp. Optional evidence
bindings include `aggregate_digest`, `manifest_digest`, and `boundary`; the
`revisions` block can identify the implementation and caller-asserted carrier
revision. These are transport annotations only and carry no policy authority.

The aggregate action exposes `aggregate-digest` in addition to its existing
`manifest-digest` output so a badge can bind to both the aggregate document
and its evidence manifest.

## Badge-carrier commit semantics

When a caller publishes the rendered badge back into its repository, the
publishing commit is a *descendant* of the tested revision, never the
revision itself: the sidecar's `subject-commit` names the tested parent,
while the carrier commit only transports the presentation files. Evidence
about revision R therefore always lives at a descendant of R. Consumers
must resolve the claim through the sidecar's subject binding, never by
assuming the carrier commit was itself verified.
