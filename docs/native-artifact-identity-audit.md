# Native artifact identity audit

Phase IV keeps identity ownership at the contract that defines the artifact.
The generic structured artifact codec validates the Commons/native interface
identity, schema revision, canonical value digest, and bounded representation;
it does not decide what an application-level identity means.

| Identity | Semantic owner | Bound material | External source / transport | Native projection risk |
| --- | --- | --- | --- | --- |
| `family_identity` | Actions family contract | Family name plus the owning graph identity | Selected family graph | A host must not invent a new family namespace; native binding requires the graph relationship. |
| `plan_identity` | Commons VerificationPlan contract | Canonical plan document with its self-identity field omitted | `plan_id` in the plan artifact | Rehashing the whole JSON envelope would bind incidental transport metadata, so it is not the native plan identity. |
| `graph_identity` | Commons family graph contract | Canonical semantic graph | Graph artifact identity | A projected graph identity is rejected when it disagrees with the plan/edge bindings. |
| `edge_identity` | Commons semantic edge declaration | Canonical selected edge/fingerprint | Selected edge in the graph artifact | Host selection is still transport today; native binding checks the selected edge against every result. |
| `source_identity` | Commons plan/source contract | Declared source content identity | Plan source declaration | Native checks bind the same source identity across plan, graph, edge, and results. |
| `contract_identity` | Owning artifact contract (currently Commons verification-plan/check contract) | Declared contract/interface identity | Plan, graph, edge, check, and result declarations | Explicitly carried on all evidence records so a self-consistent projection cannot move evidence between contracts. |
| `consumer_identity` | Commons consumer declaration | Consumer manifest identity | Graph edge and consumer declaration | Native binding requires the selected consumer identity to agree across the edge and results. |
| `test_identity` | `mncs-test` selector contract | Canonical selected test identity sequence | Verification selector and TestResult | The compatibility adapter may serialize the selector, but it does not supply a validity boolean. |
| `check_identity` | Consumer check declaration | Declared check identity and contract binding | Verification/check manifest | Native check binding requires the check identity from the selected plan. |
| `producer_identity` | Commons provider declaration | Canonical producer repository identity | Graph edge/provider declaration | Native requires it to be present; declaration loading remains a migration target for Actions. |
| `producer_revision_identity` | Provider revision contract | Repository/declaration revision selected for the edge | Provider checkout/manifest | Revision is separate from content identity and is included in receipt material. |
| `test_result_identity` | `mncs-test` TestResult contract | Canonical TestResult document | TestResult artifact | The Phase IV raw canary hashes the decoded Commons wire view; the full TestResult contract and its canonical identity still need a native representation. |
| `check_result_identity` | Consumer CheckResult contract | Canonical CheckResult document | CheckResult artifact | The Phase IV raw canary hashes the decoded wire view; full CheckResult contract identity validation remains a migration target. |
| `execution_identity` | `mncs-test` execution contract | Declared execution/run identity | TestResult execution record | It is not substituted with a host process id or a verdict. |
| `evidence_identity` | Actions evidence contract | The pair of canonical TestResult and CheckResult identities | Result records | Native receipt material carries the relationship; the host must not decide sufficiency from it. |
| `receipt_identity` | Actions receipt contract | Canonical `ReceiptMaterial` | Receipt artifact | Native Actions computes and validates it; old receipts lacking `contract_identity` fail closed for reuse. |

## The reviewed `plan_digest` seam

The compatibility script also computes `plan_digest`, the SHA-256 of the
external plan file. That value remains useful as an external transport
reference in the compatibility receipt, but it is deliberately not passed to
the native family evidence projection. The native contract uses the
Commons-owned `plan_identity` instead. This distinction prevents an arbitrary
Python hash of an external envelope from becoming a family identity while
allowing the transport layer to detect a changed file.

The Phase IV raw ingress canary now admits the selected plan, edge, TestResult,
CheckResult, provider declaration, and prior receipt through the generic
decoder. It binds the fields represented by the Commons wire views and rejects
schema/type/selection mismatches. Those views intentionally do not yet model
every field in the external plan, graph, TestResult, and CheckResult schemas,
so a changed unknown field is not yet guaranteed to change the native
identity. Full contract validation and identity recomputation remain open.

In the canonical Python flow, `_family_evidence` is still an explicit
compatibility structural adapter. The native canary bypasses it, but it must
not be promoted to the canonical semantic path until the complete owning
contracts and selected-provider orchestration are native.
