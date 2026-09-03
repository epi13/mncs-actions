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

## Naming correction

The `verify`/`run-check`/`aggregate` outputs previously exposed only
`provenance-digest`, which hashed the entire canonical manifest rather
than only provenance. The correct output is now `manifest-digest`;
`provenance-digest` is retained as a deprecated compatibility alias with
the identical value. New consumers should use `manifest-digest`.
