# Evidence and references

`mncs-actions` carries family-owned records without redefining them.

A reference may carry:

```text
kind
producer
contract/schema revision
URI or relative path
content digest
producer revision
```

- Use content-addressed identities wherever the owning contract already
  supports them (e.g. rights `manifest_identity`, validator package
  digests, Commons `contentDigest`).
- Do not invent competing identity rules in `mncs-actions`.
- `reference.path`, when present, must be a safe relative path (no
  absolute paths, no `..`, no backslashes). Artifact content never
  controls arbitrary filesystem writes: packaging copies to fixed
  filenames (`verification-result.json`, `check-result.json`,
  `aggregate-result.json`, `evidence-manifest.json`,
  `execution-receipt.json`, `observed-*.json`).
- Downloaded artifacts and external check results are untrusted until
  validated with the canonical `lib/mncs_actions.py` validators.
- Aggregate evidence preserves component bindings: each consumed
  check-result contributes its SHA-256 and path to `aggregate-result.json`
  (`checks[].digest`/`path`) and to the manifest `references[]`
  (`kind: check-result`). Both fields are optional but strict when
  present: `digest` must be hex64 or `sha256:`-prefixed hex64, and `path`
  must be a safe relative path (no absolute paths, no `..`, no
  backslashes). Malformed bindings are rejected, never silently
  accepted; unknown additive fields stay permitted. The executable
  validator (`lib/mncs_actions.py::validate_aggregate_result`) is
  authoritative and mechanically aligned with
  `schemas/aggregate-result.schema.json` (see
  `tests/test_evidence_bindings.py`). This is generic plumbing for
  future ChangeSet evidence graphs, not Commons/rights semantics.

## Naming correction

The `verify`/`run-check`/`aggregate` outputs previously exposed only
`provenance-digest`, which hashed the entire canonical manifest rather
than only provenance. The correct output is now `manifest-digest`;
`provenance-digest` is retained as a deprecated compatibility alias with
the identical value. New consumers should use `manifest-digest`.
