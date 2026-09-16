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
    _run_native_actions_family_check,
    _run_native_actions_selected_proof,
)


def test_native_family_check_shadow_returns_typed_pass() -> None:
    binary = os.environ.get("MNCS_BINARY", "mncs")
    try:
        completed = subprocess.run(
            [
                binary,
                "call",
                str(ROOT / "native/mncs/actions/family/v1.mncs"),
                "--module",
                "mncs.actions.family.v1",
                "--function",
                "family_check",
                "--args",
                str(ROOT / "tests/fixtures/native_family_pass.args.json"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"native MNCS launcher unavailable: {error}")
    if completed.returncode != 0 and (
        "unknown command \"call\"" in completed.stderr
        or "invalid choice: 'call'" in completed.stderr
    ):
        pytest.skip("native MNCS launcher predates the generic call entrypoint")
    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    assert document["schema_version"] == "mncs.application-call/1"
    assert document["status"] == "returned"
    value = document["call"]["returned"][0]["record"]["fields"]
    fields = dict(value)
    assert fields["verdict"]["finite"]["variant_identity"].endswith("::PASS")
    assert fields["proof_sufficient"]["boolean"]["value"] is True


def test_native_selected_proof_shadow_aggregates_bounded_consumers() -> None:
    binary = os.environ.get("MNCS_BINARY", "mncs")
    try:
        completed = subprocess.run(
            [
                binary,
                "call",
                str(ROOT / "native/mncs/actions/family/v1.mncs"),
                "--module",
                "mncs.actions.family.v1",
                "--function",
                "selected_proof",
                "--args",
                str(ROOT / "tests/fixtures/native_selective_proof_fail.args.json"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"native MNCS launcher unavailable: {error}")
    if completed.returncode != 0 and (
        "unknown command \"call\"" in completed.stderr
        or "invalid choice: 'call'" in completed.stderr
    ):
        pytest.skip("native MNCS launcher predates the generic call entrypoint")
    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    fields = dict(document["call"]["returned"][0]["record"]["fields"])
    assert fields["total"]["integer"]["value"] == 2
    assert fields["passed"]["integer"]["value"] == 1
    assert fields["failed"]["integer"]["value"] == 1


def test_native_actions_shadow_uses_bounded_process_capability() -> None:
    binary = os.environ.get("MNCS_BINARY")
    if not binary:
        pytest.skip("set MNCS_BINARY to exercise the native process-backed shadow")
    source = ROOT / "native/mncs/actions/family/v1.mncs"
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
    assert family["process"]["schema_version"] == "mncs.process-result/1"
    assert family["process"]["timed_out"] is False
    assert proof["verdict"] == "FAIL"
