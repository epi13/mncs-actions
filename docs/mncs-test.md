# Native `mncs-test` action

`actions/mncs-test` is the transport and automation adapter for the
independent [`mncs-test`](https://github.com/epi13/mncs-test) provider. It
does not implement test semantics. For ordinary executable source, the
provider asks the compiler for the Profile 0.17 first-class test inventory;
the action never scans source or re-registers entries. The provider returns a typed
`mncs.test-result/1` document and this action validates its
`mncs.check-result/1` projection through the existing `run-check` contract.

## Caller contract

The caller checks out the source and supplies an executable `mncs-test`
launcher plus the current `mncs` binary. A repository that also checks out
`mncs-language` can ask this action to build the compiler:

```yaml
- name: Run native MNCS tests
  id: mncs-test
  uses: epi13/mncs-actions/actions/mncs-test@<pinned-mncs-actions-sha>
  with:
    source: ./mncs-test/tests/self_suite.mncs
    mncs-test-bin: ./mncs-test/bin/mncs-test
    build-mncs: "true"
    mncs-source: ./mncs-language
    library-path: ./mncs-test/native:./mncs-language/library
    # Deprecated compatibility input; native execution ignores it.
    embed-library: ./mncs-language/target/debug/libmncs_embed.so
    # Optional identity/name fragment selection.
    test-filter: arithmetic
    result-file: .mncs/mncs-test-check.json
    test-result-file: .mncs/mncs-test-result.json
    evidence-directory: .mncs/mncs-test-evidence
    artifacts-directory: .mncs/mncs-test-artifacts
```

`mncs-test-bin` is an executable path, not an interpolated shell command.
`build-mncs` uses the narrow platform/toolchain boundary to build both the
`mncs` compiler and `mncs-embed`. The native run is one inventory call, one
backend compilation, one retained session, and one batch of test calls. It
fails closed when the trusted bootstrap is unavailable; the Python
subprocess-per-test implementation is reachable only through the explicit
compatibility/oracle entrypoint.
Checkout/source acquisition remains the caller's responsibility, as it does
for the other provider actions.

`test-filter` is passed to `mncs-test` as a selection hint; it does not change
the compiler's inventory or semantic identities. The deprecated `manifest`
input remains only as an explicit compatibility label; native execution
requires `source`. Legacy manifests continue to describe source/module roots,
library paths, budgets, and external compile/diagnostic fixtures for the
oracle path. Ordinary runtime test registration belongs to the
language/compiler.

The action preserves the provider exit class and exposes it as
`failure-class`. The provider distinguishes assertion/test failure,
infrastructure failure, compile failure, runtime failure, timeout, and
unsupported capability. The result and standard execution receipt are still
packaged when the provider fails, so a failed workflow has reproducible
evidence instead of only a shell exit line.

The resulting check can be fed to `actions/aggregate`. A future Forge command
can invoke the same provider boundary and consume `mncs.check-result/1`
without understanding test declarations or assertion semantics. Forge is not
modified by this action.
