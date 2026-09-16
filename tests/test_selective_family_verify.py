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
from selective_family_verify import build_selective_proof


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
        "coverage_status": "complete",
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
