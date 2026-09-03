# mncs-actions

Machine-native GitHub Actions and reusable automation primitives for verification, evidence, provenance, coordination, and language-pressure workflows across the MNCS ecosystem.

> **Status:** experimental foundation. The contracts in this repository are versioned, but they are not yet presented as a completed MNCS standard.

## Purpose

mncs-actions is the GitHub integration layer around the MNCS family. It turns repository events into repeatable, inspectable operations while leaving canonical semantics to the repositories that own them.

The first primitive is actions/verify: a composite action that runs a project-defined verifier, validates its machine-readable result, creates an evidence manifest, and exposes a PASS, FAIL, or UNKNOWN verdict to the workflow.

~~~yaml
- uses: epi13/mncs-actions/actions/verify@bootstrap/verification-action
  with:
    command: ./scripts/verify-mncs.sh
    result-file: .mncs/verification-result.json
    evidence-directory: .mncs/evidence
    fail-on-unknown: true
~~~

Pin a reviewed commit or release tag in production. The branch above is used only for this initial development increment.

## Result contract

A verifier must write a JSON result before the action finishes:

~~~json
{
  "schema_version": "mncs.verification-result/1",
  "verdict": "PASS",
  "summary": "All required checks passed.",
  "checks": [
    {
      "id": "compile",
      "verdict": "PASS",
      "summary": "The project compiled successfully."
    }
  ]
}
~~~

The action validates the top-level contract, copies the result into an evidence directory, and writes an evidence manifest containing:

- the result digest;
- repository, ref, commit, workflow, run, actor, and event provenance;
- the command exit code;
- the final verification verdict.

UNKNOWN is intentionally not converted into PASS. It means that the system could not establish a positive or negative result. Callers decide whether an unknown result is acceptable for a particular boundary.

Malformed results are action failures. A valid FAIL result is also a failing action. A valid UNKNOWN result succeeds only when fail-on-unknown is false.

## Repository layout

~~~text
actions/verify/       Reusable verification action
schemas/              Versioned machine-readable contracts
examples/             Consumer workflow examples
docs/                 Architecture and integration notes
.github/workflows/    Repository self-tests
~~~

## Ecosystem boundary

| Concern | Owning repository | Role of this repository |
| --- | --- | --- |
| Development and promotion rules | MNCDS / MNCS | Invoke and transport checks |
| Language and compiler capability | mncs-language | Surface compiler results and capability gaps |
| Cross-repository coordination | Commons | Emit records that can be consumed by coordination workflows |
| Rights and provenance semantics | mncs-rights-provenance | Carry references and digests; do not redefine authorization |
| Pressure experiments and candidate work | Forge | Run bounded verification and preserve evidence |
| GitHub execution glue | mncs-actions | Provide reusable Actions and workflows |

The action should not silently invent canonical evidence relationships, rights, or promotion authority. Those belong to the appropriate MNCS-family contract.

## Development

The repository currently has no runtime dependency beyond Bash, Python 3, and standard GitHub-hosted runner capabilities. Run the local checks with:

~~~bash
bash -n actions/verify/verify.sh
python3 -m json.tool schemas/verification-result.schema.json >/dev/null
python3 -m json.tool schemas/evidence-manifest.schema.json >/dev/null
~~~

The GitHub workflow also exercises a passing result and verifies the generated manifest.

## Roadmap

1. Stabilize the result and evidence contracts through use in the family repositories.
2. Add provenance and rights references without duplicating the rights-provenance model.
3. Add coordination and ChangeSet actions for cross-repository development pressure.
4. Add capability-gap and promotion-boundary actions once their owning contracts are stable.
5. Implement the action logic in MNCS where the language can express it without weakening observability or security.
