# Execution receipt vs verification claim

A verifier execution and a verification claim are not the same thing.

```text
verifier invoked
        |
        +--> command exit
        +--> runner identity
        +--> repository/revision
        +--> workflow/run identity
        +--> inputs
        +--> produced files/digests
        |
        v
execution receipt (always VALID when written)
```

Then independently:

```text
valid result contract available?
        |
       yes -> verification claim PASS / FAIL / UNKNOWN
       no  -> claim NOT ESTABLISHED (verdict INVALID at the action boundary)
```

Example:

```text
verifier invoked
command exit = 2
result file malformed

execution receipt = VALID (claim_status NOT_ESTABLISHED)
verification claim = NOT ESTABLISHED
```

Rules:

- `UNKNOWN` means evidence was insufficient to establish PASS or FAIL
  under a *valid* contract. It is a real claim verdict.
- Malformed, missing, or structurally invalid output is `NOT_ESTABLISHED`
  (`INVALID` at the GitHub output boundary). It is never rewritten as
  `UNKNOWN`.
- `actions/verify` always writes `execution-receipt.json`, even on
  negative paths. It writes `evidence-manifest.json` plus the copied
  result only when the claim is established.
- On negative paths the observed bytes (when present) are preserved as
  `observed-result.json` for forensics, plus the receipt. No manifest is
  fabricated.
- The workflow gate fails closed on `NOT_ESTABLISHED`/`INVALID` and on
  nonzero verifier exit, even when a valid PASS document was also emitted.

Contract: `schemas/execution-receipt.schema.json`
(`mncs.execution-receipt/1`). Executable enforcement:
`lib/mncs_actions.py` (`validate_execution_receipt`,
`build_execution_receipt`).
