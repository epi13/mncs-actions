# Composable checks and aggregation

The one-verifier model is retained for compatibility (`actions/verify`),
but family verification composes independent provider results.

```text
check provider (any implementation language)
        |
        v
check-result (mncs.check-result/1)
        |
        +--> id, provider, claim/scope, verdict
        +--> evidence references, contract/producer revision
        +--> unresolved, digest
```

The orchestration layer validates the contract; the provider owns the
semantics. A provider may be an MNCS program, `mncs-validator-rs`,
`mncs-rights-provenance`, a compiler invocation, a project verifier, a
Forge adapter, or another explicitly declared evaluator.

## Aggregation

`actions/aggregate` composes validated check-results:

```text
required FAIL                -> aggregate FAIL
required UNKNOWN (or missing)-> aggregate UNKNOWN
all required PASS            -> aggregate PASS
```

- Required vs optional is an explicit declared boundary per invocation.
- Optional `UNKNOWN` (or `FAIL`) stays visible in `unresolved` even when
  it does not decide the boundary. Component uncertainty is never
  destroyed to produce a convenient top-level verdict.
- Missing required coverage is `UNKNOWN`, never `PASS`.
- Invalid/missing check inputs make the aggregate claim
  `NOT_ESTABLISHED` (`INVALID`), never a fabricated verdict.

Example (valid: eBPF not required for this boundary):

```text
MNCS validation               PASS
language reference backend    PASS
WASM backend                  PASS
eBPF backend                  UNKNOWN
rights/provenance             PASS
project tests                 PASS
-----------------------------------
declared boundary             PASS

unresolved:
- eBPF backend remains UNKNOWN
```

Contracts: `schemas/check-result.schema.json`,
`schemas/aggregate-result.schema.json`. Enforcement:
`lib/mncs_actions.py` (`validate_check_result`,
`validate_aggregate_result`, `aggregate_verdict`).
Primitives: `actions/run-check` (one provider + receipt),
`actions/aggregate` (composition + receipt + manifest).
