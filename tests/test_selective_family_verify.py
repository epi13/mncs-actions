from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mncs_family_contract import (
    bind_declaration_evidence,
    declaration_identity,
    generate_family_graph,
    plan_identity,
)
from selective_family_verify import build_selective_proof, _run_native_test_provider_identities


def _identity_text(value: list[int]) -> str:
    return bytes(value).decode("utf-8")


def _declaration(
    repository_id: str,
    *,
    consumes: bool = False,
    provides: bool = True,
    check_identity: str = "",
) -> dict:
    entry = {
        "contract_identity": "mncs.verification-plan/1",
        "contract_revision": "1",
        "consumer_identity": f"{repository_id}:verification-plan",
        "evidence": "contract/consumer.py",
        "verification": {
            "check_identity": check_identity,
            "executor": repository_id,
            "surface": "selected-consumer-contract",
            "evidence": "family-verification-checks-v1.json",
        },
    }
    return {
        "schema_version": "commons.mncs.semantic-contract-declarations/v1",
        "repository_id": repository_id,
        "revision": "1",
        "provides": (
            [{
                "contract_identity": "mncs.verification-plan/1",
                "contract_revision": "1",
                "exported_identity": f"{repository_id}:verification-plan",
                "evidence": "contract/provider.py",
            }]
            if provides and not consumes
            else []
        ),
        "consumes": [entry] if consumes else [],
    }


def _materialize(checkout: Path, declaration: dict) -> dict:
    checkout.mkdir()
    (checkout / "family-semantic-contracts-v1.json").write_text(
        json.dumps(declaration), encoding="utf-8"
    )
    for kind in ("provides", "consumes"):
        for entry in declaration[kind]:
            path = checkout / entry["evidence"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{declaration['repository_id']}:{kind}\n", encoding="utf-8")
            verification = entry.get("verification")
            if verification:
                manifest_path = checkout / verification["evidence"]
                manifest_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "commons.mncs.family-verification-checks/v1",
                            "repository_id": declaration["repository_id"],
                            "checks": [{
                                "identity": verification["check_identity"],
                                "contract_identity": entry["contract_identity"],
                                "runner": "declaration",
                                "surface": verification["surface"],
                            }],
                        }
                    ),
                    encoding="utf-8",
                )
    return bind_declaration_evidence(checkout, declaration)


def _plan(graph: dict) -> dict:
    edges = graph["edges"]
    value = {
        "schema_version": "mncs.verification-plan/1",
        "source": {"path": "/producer/change.mncs", "sha256": "a" * 64},
        "impact": {
            "graph_identity": "b" * 64,
            "roots": ["ravel:verification-plan"],
            "affected_count": 1,
            "direct_dependents": [],
            "test_identities": [],
            "risk_flags": [],
            "complete": True,
            "limitations": [],
            "cross_repository": {
                "graph_identity": graph["graph_identity"],
                "edges": edges,
                "selected_repositories": ["mncs-actions", "mncs-test"],
                "complete": True,
                "limitations": [],
                "coverage": graph["coverage"],
            },
        },
        "selection": {
            "level": "family",
            "selected_test_identities": [],
            "available_test_count": 0,
            "escalation_reasons": ["cross_repository_contract_changed"],
            "routing_scope": "selected_repositories",
            "selected_repositories": ["mncs-actions", "mncs-test"],
            "available_repository_count": 4,
        },
        "proof": {
            "sufficient_to_stop": False,
            "required_evidence": ["selected_consumer_proofs_pass"],
            "boundary": {
                "claimed_scope": "selected_repositories",
                "established": False,
                "executor": "mncs-actions",
                "stop_condition": "selected_consumer_proofs_pass",
            },
        },
        "provenance": {"provider": "ravel", "policy": "bounded-impact-v1"},
    }
    value["plan_id"] = plan_identity(value)
    return value


def test_contract_change_runs_selected_consumers_and_reuses_exact_receipts(tmp_path: Path) -> None:
    workspace = tmp_path / "family"
    workspace.mkdir()
    provider = _materialize(
        workspace / "ravel",
        _declaration("ravel"),
    )
    actions = _materialize(
        workspace / "mncs-actions",
        _declaration(
            "mncs-actions",
            consumes=True,
            check_identity="mncs-actions:verification-plan-receipt",
        ),
    )
    test = _materialize(
        workspace / "mncs-test",
        _declaration(
            "mncs-test",
            consumes=True,
            check_identity="mncs-test:verification-plan-contract",
        ),
    )
    unrelated = _materialize(
        workspace / "mncs-debug", _declaration("mncs-debug", provides=False)
    )
    declarations = [provider, actions, test, unrelated]
    graph = generate_family_graph(
        declarations,
        [{"id": name, "repository": name, "revision": "1"}
         for name in ("ravel", "mncs-actions", "mncs-test", "mncs-debug")],
    )
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    plan = _plan(graph)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    first_dir = tmp_path / "first-proof"
    first = build_selective_proof(
        plan_path=plan_path,
        graph_path=graph_path,
        workspace_root=workspace,
        output_dir=first_dir,
        compatibility_oracle=True,
    )
    assert first["status"] == "PASS"
    assert first["routing"]["selected_repository_count"] == 2
    assert first["routing"]["family_repository_count"] == 4
    provider_manifest_identity = declaration_identity(provider)
    assert first["producer"] == {
        "repository": "ravel",
        "declaration_revision": "1",
        "repository_revision": f"manifest:{provider_manifest_identity}",
        "repository_revision_kind": "manifest_identity",
        "manifest_identity": provider_manifest_identity,
        "evidence_sha256": [
            provider["_evidence_digests"]["provides"]["contract/provider.py"]
        ],
    }
    assert first["metrics"] == {
        "repositories_selected": 2,
        "repositories_available": 4,
        "semantic_graph_participants": 4,
        "registered_family_projects": 4,
        "coverage_classified_projects": 4,
        "unclassified_projects": 0,
        "coverage_status": "incomplete",
        "checks_executed": 2,
        "checks_available": 2,
        "receipts_reused": 0,
        "receipts_generated": 2,
    }
    assert not (first_dir / "evidence" / "mncs-debug").exists()

    second_dir = tmp_path / "second-proof"
    second = build_selective_proof(
        plan_path=plan_path,
        graph_path=graph_path,
        workspace_root=workspace,
        output_dir=second_dir,
        prior_proof=first_dir,
        compatibility_oracle=True,
    )
    assert second["status"] == "PASS"
    assert second["metrics"]["checks_executed"] == 0
    assert second["metrics"]["receipts_reused"] == 2
    assert all(item["status"] == "reused" for item in second["consumers"])


def test_mncs_test_provider_adapter_passes_compiler_identities_to_runner(tmp_path: Path) -> None:
    checkout = tmp_path / "mncs-test"
    checkout.mkdir()
    (checkout / "tests").mkdir()
    (checkout / "tests/self_suite.mncs").write_text("mncs 0.18;\nmodule tests.self_suite;\n", encoding="utf-8")
    (checkout / "mncs-test.toml").write_text(
        'schema_version = "mncs.test-manifest/1"\nsource = "tests/self_suite.mncs"\nlibraries = ["native"]\n',
        encoding="utf-8",
    )
    (checkout / "native").mkdir()
    runner = checkout / "identity_runner.py"
    result_rows = [
        {
            "test_case_identity": "mncs:0.2:test-case:tests.self_suite::arithmetic::ca0c84b11bb187d84ef7dc2af5bc784c917fde67c036c9affeeea5a72d50a2fb",
            "declaration_identity": "mncs:0.2:test:tests.self_suite::arithmetic",
            "callable_identity": "mncs:0.2:function:tests.self_suite::arithmetic",
            "signature_identity": "sha256:0180fa7ee91f0d2ac7503588e2577733730b043856c0ff39134d306f0263a8f2",
            "module": "tests.self_suite",
            "artifact_identity": "mncs:compiler:backend-artifact:f33cd0ad3bc5688e567099c6b0d742ad2ef5eba5ab7acc8e6c76bbaf68517e09",
        },
        {
            "test_case_identity": "mncs:0.2:test-case:tests.provider_cross_module::separate_module_identity::af52e6e7d52366446fcd2631f3d414c3b9c6cf5d98df65e712eea801aa2ae10d",
            "declaration_identity": "mncs:0.2:test:tests.provider_cross_module::separate_module_identity",
            "callable_identity": "mncs:0.2:function:tests.provider_cross_module::separate_module_identity",
            "signature_identity": "sha256:cb985a1174a53db59c2301fb860ff9fa602222de527ba091eb23ce416aa36f76",
            "module": "tests.provider_cross_module",
            "artifact_identity": "mncs:compiler:backend-artifact:f33cd0ad3bc5688e567099c6b0d742ad2ef5eba5ab7acc8e6c76bbaf68517e09",
        },
    ]
    runner.write_text(
        "import json, pathlib, sys\n"
        f"rows = json.loads({json.dumps(result_rows)!r})\n"
        "args = sys.argv[1:]\n"
        "bindings = [{'test_case_identity': row['test_case_identity'], 'declaration_identity': row['declaration_identity'], 'callable_identity': row['callable_identity'], 'signature_identity': row['signature_identity']} for row in rows]\n"
        "artifact_identity = rows[0]['artifact_identity']\n"
        "execution = {'artifact_identity': artifact_identity, 'compiler_callable_bindings': {'artifact_identity': artifact_identity, 'artifact_sha256': 'a' * 64, 'callable_bindings': bindings}}\n"
        "selected = [args[i + 1] for i, value in enumerate(args[:-1]) if value == '--test-identity']\n"
        "pathlib.Path(__file__).with_suffix('.selected.json').write_text(json.dumps(selected))\n"
        "result_path = pathlib.Path(args[args.index('--result') + 1])\n"
        "tests = []\n"
        "for row in rows:\n"
        "    semantic = {'test_case_identity': row['test_case_identity'], 'declaration_identity': row['declaration_identity'], 'function_identity': row['callable_identity'], 'signature_identity': row['signature_identity']}\n"
        "    invocation = {'test_case_identity': row['test_case_identity'], 'declaration_identity': row['declaration_identity'], 'callable_identity': row['callable_identity'], 'signature_identity': row['signature_identity'], 'artifact_identity': row['artifact_identity'], 'execution_status': 'returned'}\n"
        "    tests.append({'semantic': semantic, 'callable_invocation': invocation, 'native_result': {'verdict': 'PASS', 'verdict_code': 1, 'failure_kind_name': 'NoFailure', 'failure_code': 0, 'assertions': 1, 'failures': 0, 'expected': 1, 'actual': 1, 'assertion_code': 0}})\n"
        "result_path.write_text(json.dumps({'schema_version': 'mncs.test-result/1', 'selection': {'selected_test_identities': selected}, 'execution': execution, 'tests': tests}))\n",
        encoding="utf-8",
    )

    selected = [row["test_case_identity"] for row in result_rows]
    executions, bindings = _run_native_test_provider_identities(
        checkout=checkout,
        selector={"manifest": "mncs-test.toml"},
        selected_tests=selected,
        runner=str(runner),
        mncs_binary="mncs",
        libraries=[],
    )

    assert json.loads(runner.with_suffix(".selected.json").read_text(encoding="utf-8")) == selected
    assert [_identity_text(execution["test_case_identity"]) for execution in executions] == selected
    assert [_identity_text(execution["callable_identity"]) for execution in executions] == [
        row["callable_identity"] for row in result_rows
    ]
    assert executions[0]["callable_identity"] != executions[1]["callable_identity"]
    assert executions[0]["artifact_identity"] == executions[1]["artifact_identity"]
    assert len(bindings) == 2


def test_native_mncs_test_adapter_invokes_selected_compiler_identities(tmp_path: Path) -> None:
    checkout = tmp_path / "mncs-test"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "native").mkdir()
    (checkout / "tests/self_suite.mncs").write_text("mncs 0.18;\nmodule tests.self_suite;\n", encoding="utf-8")
    (checkout / "mncs-test.toml").write_text(
        'schema_version = "mncs.test-manifest/1"\nsource = "tests/self_suite.mncs"\nlibraries = ["native"]\n',
        encoding="utf-8",
    )
    rows = [
        {
            "test_case_identity": "mncs:0.2:test-case:tests.self_suite::arithmetic::ca0c84b11bb187d84ef7dc2af5bc784c917fde67c036c9affeeea5a72d50a2fb",
            "declaration_identity": "mncs:0.2:test:tests.self_suite::arithmetic",
            "callable_identity": "mncs:0.2:function:tests.self_suite::arithmetic",
            "signature_identity": "sha256:0180fa7ee91f0d2ac7503588e2577733730b043856c0ff39134d306f0263a8f2",
        },
        {
            "test_case_identity": "mncs:0.2:test-case:tests.self_suite::boolean::d0ba630684abae18f3c11b74c7a3340691d19fec4bae2e5f6bc4df1c1f5eb82b",
            "declaration_identity": "mncs:0.2:test:tests.self_suite::boolean",
            "callable_identity": "mncs:0.2:function:tests.self_suite::boolean",
            "signature_identity": "sha256:8a0c1a704918d3aa4ee9f79e8a99758a7d75d60f9e0fcd50857b5d375e9c4935",
        },
    ]
    artifact_identity = "mncs:compiler:backend-artifact:f33cd0ad3bc5688e567099c6b0d742ad2ef5eba5ab7acc8e6c76bbaf68517e09"
    runner = checkout / "mncs-test-native"
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"rows = json.loads({json.dumps(rows)!r})\n"
        f"artifact = {artifact_identity!r}\n"
        "args = sys.argv[1:]\n"
        "bindings = [{'test_case_identity': row['test_case_identity'], 'declaration_identity': row['declaration_identity'], 'callable_identity': row['callable_identity'], 'signature_identity': row['signature_identity']} for row in rows]\n"
        "execution = {'artifact_identity': artifact, 'compiler_callable_bindings': {'artifact_identity': artifact, 'artifact_sha256': 'a' * 64, 'callable_bindings': bindings}}\n"
        "selected = [args[i + 1] for i, value in enumerate(args[:-1]) if value == '--test-identity']\n"
        "pathlib.Path(__file__).with_suffix('.args.json').write_text(json.dumps(args))\n"
        "tests = []\n"
        "for row in rows:\n"
        "    if row['test_case_identity'] not in selected: continue\n"
        "    semantic = {'test_case_identity': row['test_case_identity'], 'declaration_identity': row['declaration_identity'], 'function_identity': row['callable_identity'], 'signature_identity': row['signature_identity']}\n"
        "    invocation = {'test_case_identity': row['test_case_identity'], 'declaration_identity': row['declaration_identity'], 'callable_identity': row['callable_identity'], 'signature_identity': row['signature_identity'], 'artifact_identity': artifact}\n"
        "    native = {'verdict': 'PASS', 'verdict_code': 0, 'failure_kind': 'nofailure', 'failure_code': 0, 'assertions': 1, 'failures': 0, 'expected': 1, 'actual': 1, 'assertion_code': 1001}\n"
        "    tests.append({'semantic': semantic, 'callable_invocation': invocation, 'execution': {'status': 'returned'}, 'native_result': native})\n"
        "pathlib.Path(args[args.index('--result') + 1]).write_text(json.dumps({'schema_version': 'mncs.test-result/1', 'selection': {'selected_test_identities': selected}, 'execution': execution, 'tests': tests}))\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)

    selected = [row["test_case_identity"] for row in rows]
    executions, bindings = _run_native_test_provider_identities(
        checkout=checkout,
        selector={"manifest": "mncs-test.toml"},
        selected_tests=selected,
        runner=str(runner),
        mncs_binary="mncs",
        libraries=[],
    )

    args = json.loads(runner.with_suffix(".args.json").read_text(encoding="utf-8"))
    assert args[0] == str(checkout / "tests/self_suite.mncs")
    assert "run" not in args and "--manifest" not in args
    assert [args[index + 1] for index, value in enumerate(args[:-1]) if value == "--test-identity"] == selected
    assert [_identity_text(execution["test_case_identity"]) for execution in executions] == selected
    assert [_identity_text(execution["callable_identity"]) for execution in executions] == [
        row["callable_identity"] for row in rows
    ]
    assert all(_identity_text(execution["artifact_identity"]) == artifact_identity for execution in executions)
    assert len(bindings) == 2
