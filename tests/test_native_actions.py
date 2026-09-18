"""Focused canaries for the Actions-owned MNCS semantic shadow."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))
from selective_family_verify import (  # noqa: E402
    _family_evidence,
    _run_native_actions_family_check,
    _run_native_actions_selected_proof,
)


def test_native_family_check_shadow_returns_typed_pass() -> None:
    binary = os.environ.get("MNCS_BINARY", "mncs")
    try:
        result = _run_native_actions_family_check(
            mncs_binary=binary,
            source_path=ROOT / "native/mncs/actions/family.mncs",
            verdict="PASS",
            cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"native MNCS launcher unavailable: {error}")
    assert result["authority"] == "mncs.actions.family"
    assert result["verdict"] == "PASS"
    assert result["proof_sufficient"] is True


def test_native_selected_proof_shadow_aggregates_bounded_consumers() -> None:
    binary = os.environ.get("MNCS_BINARY", "mncs")
    try:
        result = _run_native_actions_selected_proof(
            mncs_binary=binary,
            source_path=ROOT / "native/mncs/actions/family.mncs",
            records=[{"verdict": "PASS"}, {"verdict": "FAIL"}],
            cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"native MNCS launcher unavailable: {error}")
    assert result["total"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1


def test_native_actions_coverage_rejects_incomplete_family() -> None:
    binary = os.environ.get("MNCS_BINARY")
    if not binary:
        pytest.skip("set MNCS_BINARY to exercise native coverage policy")
    result = _run_native_actions_selected_proof(
        mncs_binary=binary,
        source_path=ROOT / "native/mncs/actions/family.mncs",
        records=[{"verdict": "PASS"}],
        cwd=ROOT,
        expected_count=2,
    )
    assert result["verdict"] == "PASS"
    assert result["coverage"]["verdict"] == "Incomplete"
    assert result["coverage"]["proof_sufficient"] is False


def test_native_actions_shadow_uses_bounded_process_capability() -> None:
    binary = os.environ.get("MNCS_BINARY")
    if not binary:
        pytest.skip("set MNCS_BINARY to exercise the native process-backed shadow")
    source = ROOT / "native/mncs/actions/family.mncs"
    family = _run_native_actions_family_check(
        mncs_binary=binary,
        source_path=source,
        verdict="PASS",
        cwd=ROOT,
    )
    proof = _run_native_actions_selected_proof(
        mncs_binary=binary,
        source_path=source,
        records=[{"verdict": "PASS"}, {"verdict": "FAIL"}],
        cwd=ROOT,
    )
    assert family["process"]["schema_version"] == "mncs.application-call/1"
    assert family["process"]["subprocess_count"] == 1
    assert proof["verdict"] == "FAIL"


def _actual_evidence(
    *,
    test_verdict: str = "PASS",
    check_verdict: str | None = None,
    producer_revision: str = "producer-revision-a",
    prior_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    plan = {
        "plan_id": "a" * 64,
        "source": {"sha256": "b" * 64},
    }
    edge = {
        "fingerprint": "c" * 64,
        "contract_identity": "d" * 64,
        "consumer_manifest_identity": "e" * 64,
        "verification": {
            "selector": {"test_identities": ["test:one"]},
            "check_identity": "f" * 64,
        },
    }
    behavioral_result = {
        "schema_version": "mncs.test-result/1",
        "verdict": test_verdict,
        "execution": {"run_identity": "1" * 64},
    }
    behavioral_check = {
        "schema_version": "mncs.check-result/1",
        "verdict": check_verdict or test_verdict,
        "id": "f" * 64,
    }
    return _family_evidence(
        plan=plan,
        graph_identity="1" * 64,
        edge=edge,
        behavioral_result=behavioral_result,
        behavioral_check=behavioral_check,
        producer_repository="ravel",
        producer_repository_revision=producer_revision,
        prior_receipt=prior_receipt,
    )


def test_native_actions_owns_receipt_reuse_and_dependency_invalidation() -> None:
    binary = os.environ.get("MNCS_BINARY")
    if not binary:
        pytest.skip("set MNCS_BINARY to exercise native receipt canaries")
    source = ROOT / "native/mncs/actions/family.mncs"
    first = _run_native_actions_family_check(
        mncs_binary=binary,
        source_path=source,
        evidence=_actual_evidence(),
        cwd=ROOT,
    )
    assert first["verdict"] == "PASS"
    assert first["reusable"] is False

    second = _run_native_actions_family_check(
        mncs_binary=binary,
        source_path=source,
        evidence=_actual_evidence(prior_receipt=first["receipt"]),
        cwd=ROOT,
    )
    assert second["verdict"] == "PASS"
    assert second["reusable"] is True
    assert second["receipt"] == first["receipt"]

    changed = _run_native_actions_family_check(
        mncs_binary=binary,
        source_path=source,
        evidence=_actual_evidence(
            producer_revision="producer-revision-b",
            prior_receipt=first["receipt"],
        ),
        cwd=ROOT,
    )
    assert changed["verdict"] == "PASS"
    assert changed["reusable"] is False


def test_native_actions_rejects_mismatched_binding_and_propagates_failure() -> None:
    binary = os.environ.get("MNCS_BINARY")
    if not binary:
        pytest.skip("set MNCS_BINARY to exercise native evidence canaries")
    source = ROOT / "native/mncs/actions/family.mncs"
    mismatch = _run_native_actions_family_check(
        mncs_binary=binary,
        source_path=source,
        evidence=_actual_evidence(check_verdict="FAIL"),
        cwd=ROOT,
    )
    assert mismatch["verdict"] == "UNKNOWN"
    assert mismatch["proof_sufficient"] is False

    failure = _run_native_actions_family_check(
        mncs_binary=binary,
        source_path=source,
        evidence=_actual_evidence(test_verdict="FAIL"),
        cwd=ROOT,
    )
    assert failure["verdict"] == "FAIL"
    assert failure["proof_sufficient"] is True


def test_native_actions_selected_proof_is_dynamic_and_fails_closed_on_overflow() -> None:
    binary = os.environ.get("MNCS_BINARY")
    if not binary:
        pytest.skip("set MNCS_BINARY to exercise native proof canaries")
    source = ROOT / "native/mncs/actions/family.mncs"
    family = _run_native_actions_family_check(
        mncs_binary=binary,
        source_path=source,
        evidence=_actual_evidence(),
        cwd=ROOT,
    )
    records = [{"native_family_result": family["family_result"]} for _ in range(9)]
    selected = _run_native_actions_selected_proof(
        mncs_binary=binary,
        source_path=source,
        records=records,
        cwd=ROOT,
        strict_native=True,
    )
    assert selected["verdict"] == "PASS"
    assert selected["total"] == 9
    assert selected["passed"] == 9
    assert selected["invalid"] == 0
    assert selected["proof_identity"]

    overflow = _run_native_actions_selected_proof(
        mncs_binary=binary,
        source_path=source,
        records=records * 8,
        cwd=ROOT,
        strict_native=True,
    )
    assert overflow["verdict"] == "UNKNOWN"
    assert overflow["total"] == 72
    assert overflow["invalid"] == 1
    assert overflow["reason_code"] == 30
