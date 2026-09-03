# Family adapters: rights/provenance and MNCS validation

`mncs-actions` invokes and transports; it does not reimplement owning
semantics. Each adapter consumes a native report and emits a
`mncs.check-result/1` with documented mapping. Native details are
preserved, never hidden.

## Rights and provenance (`adapters/rights_adapter.py`)

Source: `mncs-rights-provenance`, `mncs-rp validate` JSON report.

| Native `outcome` | Check `verdict` | Rationale |
| --- | --- | --- |
| `pass` | `PASS` | Requirements satisfied under the profile |
| `blocked`, `invalid` | `FAIL` | Negative established |
| `pass-with-findings`, `review-required`, `unknown` | `UNKNOWN` | Review outstanding; insufficient for PASS |
| unrecognized | `UNKNOWN` | Vocabulary drift never becomes PASS |

The native `outcome`, `severity`, `findings`, `issues`, and manifest
identity are preserved in `summary`/`unresolved`/`references`
(`kind: rights-manifest`). `legal_conclusion` remains `NOT_MADE` upstream.

Fixtures: `rights PASS`, `review-required -> UNKNOWN`, `blocked -> FAIL`,
`invalid -> FAIL`, malformed input -> adapter exit 2 (caller emits
`NOT_ESTABLISHED`; nothing is fabricated).

## MNCS validation (`adapters/validator_adapter.py`)

Source: `mncs-validator-rs`, `mncs-rs validate --json` /
`validate-bundle --json` (`ValidationReport`).

| Native report | Check `verdict` |
| --- | --- |
| `valid=true`, `computed_status` PASS/FAIL/UNKNOWN | same verdict |
| `valid=true`, unrecognized status | `UNKNOWN` (never PASS) |
| `valid=false` (issues established) | `FAIL` |

Operational failures (exit 2, no report) must not be converted into a
check-result; let `run-check` emit `NOT_ESTABLISHED`.

Both adapters exit 2 on malformed native input and validate their output
with the canonical `validate_check_result` before writing.
