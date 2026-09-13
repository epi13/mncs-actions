# Native `mncs-test` action

`actions/mncs-test` is the transport and automation adapter for the
independent [`mncs-test`](https://github.com/epi13/mncs-test) provider. It
does not implement test semantics. The provider returns a typed
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
    manifest: mncs-test.toml
    mncs-test-bin: ./mncs-test/bin/mncs-test
    build-mncs: "true"
    mncs-source: ./mncs-language
    library-path: ./mncs-test/native:./mncs-language/library
    result-file: .mncs/mncs-test-check.json
    test-result-file: .mncs/mncs-test-result.json
    evidence-directory: .mncs/mncs-test-evidence
    artifacts-directory: .mncs/mncs-test-artifacts
```

`mncs-test-bin` is an executable path, not an interpolated shell command.
`build-mncs` uses the narrow platform/toolchain boundary
`cargo build --locked --manifest-path <mncs-source>/Cargo.toml --bin mncs`.
Checkout/source acquisition remains the caller's responsibility, as it does
for the other provider actions.

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
