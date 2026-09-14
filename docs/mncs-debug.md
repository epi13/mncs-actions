# Native `mncs-debug` action

`actions/mncs-debug` is the Actions membrane for the canonical
[`mncs-debug`](https://github.com/epi13/mncs-debug) provider. It accepts either
an existing `mncs.test-result/1` document (the preferred failure path) or an
explicit program/request pair. It invokes the debugger with bounded capture,
validates `mncs.debug-witness/1`, runs the structured debugger projections,
and packages `mncs.check-result/1`, an execution receipt, and an evidence
manifest through `actions/run-check`.

The action never parses terminal summaries, selects a test, evaluates an
assertion, constructs a trace, or claims replay semantics. A valid debug
evidence claim is explanatory evidence; it does not replace or modify the
originating test's `PASS`/`FAIL`/`UNKNOWN` verdict. With `failure-only` policy,
a passing test intentionally produces debug `UNKNOWN` with `not_requested`.

`actions/mncs-test` composes this action inline when `debug-on-failure: true`.
The debug step is gated by the structured test check-result being `FAIL`; the
test action's own gate remains independent. Set `debug-required: true` only
when the workflow explicitly requires a valid diagnostic witness.
