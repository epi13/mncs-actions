"""The Actions receipt boundary preserves the selected proof and its cause."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from mncs_family_contract import plan_identity  # noqa: E402


def test_run_check_binds_verification_plan_to_receipt_and_manifest(tmp_path: Path) -> None:
    result = tmp_path / "check.json"
    result.write_text(
        json.dumps(
            {
                "schema_version": "mncs.check-result/1",
                "id": "mncs-test",
                "provider": "mncs-test",
                "verdict": "PASS",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    plan = tmp_path / "verification-plan.json"
    plan_document = {
        "schema_version": "mncs.verification-plan/1",
        "plan_id": "",
        "source": {"path": str(tmp_path / "source.mncs"), "sha256": "b" * 64},
        "impact": {
            "graph_identity": "c" * 64,
            "roots": ["mncs:function:changed"],
            "affected_count": 3,
            "direct_dependents": ["mncs:function:consumer"],
            "test_identities": ["mncs:test-case:one"],
            "risk_flags": [],
            "complete": True,
            "limitations": ["fixture impact is bounded"],
            "cross_repository": {
                "graph_identity": "d" * 64,
                "edges": [],
                "selected_repositories": [],
                "complete": True,
                "limitations": ["fixture does not exercise family topology"],
                "coverage": {
                    "registry_identity": "e" * 64,
                    "registered_family_project_count": 0,
                    "classified_project_count": 0,
                    "semantic_graph_participant_count": 0,
                    "explicit_nonparticipant_count": 0,
                    "unclassified_project_count": 0,
                    "unclassified_repositories": [],
                    "coverage_status": "not_requested",
                    "topology_status": "not_requested",
                },
            },
        },
        "selection": {
            "level": "direct_dependents",
            "selected_test_identities": ["mncs:test-case:one"],
            "available_test_count": 8,
            "escalation_reasons": [],
            "selected_repositories": [],
            "available_repository_count": 0,
        },
        "proof": {
            "sufficient_to_stop": True,
            "required_evidence": ["selected_test_cases_pass"],
            "boundary": {
                "claimed_scope": "direct_dependents",
                "established": True,
                "executor": "mncs-test",
                "stop_condition": "selected_test_cases_pass",
            },
        },
        "provenance": {"provider": "test-fixture", "policy": "fixture"},
    }
    plan_document["plan_id"] = plan_identity(plan_document)
    plan.write_text(
        json.dumps(plan_document)
        + "\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    env = {**os.environ, "GITHUB_OUTPUT": str(tmp_path / "output")}
    run = subprocess.run(
        [
            "bash",
            str(REPO / "actions/run-check/run_check.sh"),
            "--result-file",
            str(result),
            "--evidence-dir",
            str(evidence),
            "--command-exit-code",
            "0",
            "--command",
            "mncs-test selective",
            "--expected-id",
            "mncs-test",
            "--expected-provider",
            "mncs-test",
            "--scope-file",
            str(plan),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    receipt = json.loads((evidence / "execution-receipt.json").read_text(encoding="utf-8"))
    manifest = json.loads((evidence / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert receipt["inputs"]["verification_plan_sha256"] == hashlib.sha256(plan.read_bytes()).hexdigest()
    assert receipt["claim"]["verdict"] == "PASS"
    assert manifest["boundary"]["selection"]["level"] == "direct_dependents"
    assert manifest["boundary"]["selection"]["selected_test_count"] == 1
    assert manifest["boundary"]["impact"]["affected_count"] == 3
