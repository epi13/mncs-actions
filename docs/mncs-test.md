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
    manifest: mncs-test.toml
    mncs-test-bin: ./mncs-test/bin/mncs-test
    build-mncs: "true"
    mncs-source: ./mncs-language
    library-path: ./mncs-test/native:./mncs-language/library
    # Optional: the provider also discovers this beside mncs when built here.
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
`mncs` compiler and `mncs-embed`. With the embed library available, the normal
run is one inventory call, one backend compilation, one retained session, and
one batch of test calls. The subprocess-per-test path remains an explicit
fallback for environments without the shared library.
Checkout/source acquisition remains the caller's responsibility, as it does
for the other provider actions.

`test-filter` is passed to `mncs-test` as a selection hint; it does not change
the compiler's inventory or semantic identities. The manifest continues to
hold source/module roots, library paths, budgets, and external compile/diagnostic
fixtures. Ordinary runtime test registration belongs to the language/compiler.

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
