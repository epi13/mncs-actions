"""Positive and negative contract tests for Commons, Language, and Forge adapters."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "adapters"))
sys.path.insert(0, str(REPO.parent / "mncs-forge-mcp" / "src"))

import commons_adapter
import forge_adapter
import language_adapter


def study(*, unresolved: list[str] | None = None, diagnostics: list[dict] | None = None) -> dict:
    return {
        "schema_version": "0.1",
        "contract_id": "mncs:language:compilation-study-result:0.1",
        "identity": "mncs:compiler:study:fixture",
        "compilation_status": "completed" if not unresolved else "completed_with_unresolved_obligations",
        "interpretation": "observation_only_not_assurance_or_conformance",
        "stage_fingerprints": {stage: "a" * 64 for stage in language_adapter.REQUIRED_STAGES},
        "diagnostics": diagnostics or [],
        "unresolved_obligations": unresolved or [],
        "module_resolutions": [{"requested_module": "mncs.core.status.v1"}],
    }


def test_language_study_pass_and_unresolved_are_distinct():
    passed = language_adapter.build_check(study(), source_path="pressure/test.mncs")
    assert passed["verdict"] == "PASS"
    unknown = language_adapter.build_check(
        study(unresolved=["mncs:obligation:resource-cost"]),
        source_path="pressure/test.mncs",
    )
    assert unknown["verdict"] == "UNKNOWN"
    assert unknown["unresolved"]


def test_language_study_missing_stage_or_module_is_not_established():
    malformed = study()
    del malformed["stage_fingerprints"]["semantic"]
    with pytest.raises(ValueError, match="stage fingerprints"):
        language_adapter.build_check(malformed, source_path="pressure/test.mncs")
    with pytest.raises(ValueError, match="required module"):
        language_adapter.build_check(study(), source_path="pressure/test.mncs", required_module="missing.module")


def test_commons_registry_and_owner_validator_projection(tmp_path):
    registry_path = REPO.parent / "MNCS-Commons" / "compat/family-record-producers.json"
    registry = commons_adapter.load_registry(registry_path)
    passed = commons_adapter.build_check(
        registry=registry,
        registry_path="family/commons/compat/family-record-producers.json",
        validator_returncode=0,
        validator_stdout="all compatibility fixtures valid",
        validator_stderr="",
    )
    assert passed["verdict"] == "PASS"
    failed = commons_adapter.build_check(
        registry=registry,
        registry_path="family/commons/compat/family-record-producers.json",
        validator_returncode=1,
        validator_stdout="",
        validator_stderr="drift",
    )
    assert failed["verdict"] == "FAIL"
    malformed = copy.deepcopy(registry)
    malformed["contracts"] = [item for item in malformed["contracts"] if item["producer"] != "mncs-language"]
    path = tmp_path / "family-record-producers.json"
    path.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(ValueError, match="mncs-language"):
        commons_adapter.load_registry(path)


def test_forge_native_validator_preserves_unknown_assurance():
    forge_root = REPO.parent / "mncs-forge-mcp"
    check = forge_adapter.build_check(
        forge_root=forge_root,
        policy_path=forge_root / "examples/forge-cell/policy.json",
        bundle_path=forge_root / "examples/forge-cell/test-bundle.json",
        record_path=forge_root / "examples/forge-cell/execution-record.json",
        expected_nonce="reference-nonce-0000000000000001",
    )
    assert check["verdict"] == "UNKNOWN"
    assert "process-isolated" in " ".join(check["unresolved"])


def test_forge_wrong_nonce_is_fail_and_malformed_record_is_rejected(tmp_path):
    forge_root = REPO.parent / "mncs-forge-mcp"
    check = forge_adapter.build_check(
        forge_root=forge_root,
        policy_path=forge_root / "examples/forge-cell/policy.json",
        bundle_path=forge_root / "examples/forge-cell/test-bundle.json",
        record_path=forge_root / "examples/forge-cell/execution-record.json",
        expected_nonce="wrong-nonce",
    )
    assert check["verdict"] == "FAIL"
    record = json.loads((forge_root / "examples/forge-cell/execution-record.json").read_text())
    del record["identities"]
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        forge_adapter.build_check(
            forge_root=forge_root,
            policy_path=forge_root / "examples/forge-cell/policy.json",
            bundle_path=forge_root / "examples/forge-cell/test-bundle.json",
            record_path=record_path,
            expected_nonce="reference-nonce-0000000000000001",
        )
