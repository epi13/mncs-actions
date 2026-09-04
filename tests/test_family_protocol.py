"""Adversarial and schema tests for bounded family artifact transport."""

from __future__ import annotations

import copy
import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "lib"))

from assemble_family_evidence import AssemblyError, run as assemble  # noqa: E402
from development_pressure import build_pressure_bundle  # noqa: E402
from family_contracts import validate_fixed  # noqa: E402
from family_protocol import (  # noqa: E402
    DESCRIPTOR_SCHEMA,
    ProtocolError,
    descriptor_map,
    descriptor_outputs,
    document_digest,
    load_json,
    validate_descriptor_registry,
    validate_producer_output,
    write_json,
)


def documents(tmp_path: Path):
    contract = load_json(REPO / "family-contracts.json")
    descriptors = load_json(REPO / "family-producer-descriptors.json")
    root = tmp_path / "actions"
    root.mkdir()
    shutil.copyfile(REPO / "family-contracts.json", root / "family-contracts.json")
    shutil.copyfile(REPO / "family-producer-descriptors.json", root / "descriptors.json")
    return contract, descriptors, root


def create_transport(tmp_path: Path, *, tamper: str = ""):
    contract, descriptors, actions_root = documents(tmp_path)
    entries = validate_fixed(contract)
    by_name = {entry["name"]: entry for entry in entries}
    descriptor_by_name = descriptor_map(descriptors, entries)
    producer_root = actions_root / "producers"
    for producer, descriptor in descriptor_by_name.items():
        producer_dir = producer_root / producer
        producer_dir.mkdir(parents=True)
        checks = []
        files = []
        for check_id, expected in descriptor_outputs(descriptor).items():
            check = json.loads((REPO / "tests/fixtures/check/pass.json").read_text())
            check.update(
                {
                    "id": check_id,
                    "provider": expected["provider"],
                    "contract_revision": expected["contract_revision"],
                    "producer_revision": by_name[producer]["revision"],
                }
            )
            path = producer_dir / "checks" / f"{check_id}.json"
            write_json(path, check)
            digest = document_digest(path)
            relative = f"checks/{check_id}.json"
            checks.append({"id": check_id, "path": relative, "sha256": digest})
            files.append({"path": relative, "sha256": digest, "kind": "check-result"})
        envelope = {
            "schema_version": "mncs-actions.family-producer-output/1",
            "mode": "fixed",
            "producer": producer,
            "repository": descriptor["repository"],
            "revision": by_name[producer]["revision"],
                "descriptor_digest": document_digest(actions_root / "descriptors.json"),
            "files": files,
            "check_results": checks,
        }
        if tamper == "foreign-check" and producer == "mncs-standard":
            envelope["check_results"][0]["id"] = "rights-provenance"
        if tamper == "path-traversal" and producer == "mncs-standard":
            envelope["check_results"][0]["path"] = "../escape.json"
        if tamper == "stale-digest" and producer == "mncs-standard":
            path.write_text(path.read_text() + "\n", encoding="utf-8")
        if tamper == "wrong-revision" and producer == "mncs-standard":
            envelope["revision"] = "0" * 40
        write_json(producer_dir / "producer-execution.json", envelope)
    if tamper == "duplicate-producer":
        duplicate = producer_root / "duplicate"
        shutil.copytree(producer_root / "mncs-standard", duplicate)
    return actions_root, producer_root, actions_root / "family-contracts.json", actions_root / "descriptors.json"


def test_descriptor_registry_is_versioned_and_allowlisted():
    contract = load_json(REPO / "family-contracts.json")
    descriptors = load_json(REPO / "family-producer-descriptors.json")
    assert descriptors["schema_version"] == DESCRIPTOR_SCHEMA
    assert {item["adapter_id"] for item in descriptors["descriptors"]} <= {
        "validator-json-v1", "rights-json-v1", "commons-family-v1",
        "language-study-v1", "forge-cell-v1",
    }
    assert all("command" not in item and "shell" not in item for item in descriptors["descriptors"])
    validate_descriptor_registry(descriptors, validate_fixed(contract))


@pytest.mark.parametrize("tamper, error", [
    ("foreign-check", "undeclared check id"),
    ("path-traversal", "safe relative path"),
    ("stale-digest", "changed after digest"),
    ("wrong-revision", "revision mismatch"),
    ("duplicate-producer", "duplicate producer artifact"),
])
def test_assembler_rejects_adversarial_transport(tmp_path, tamper, error):
    actions_root, producer_root, contracts, descriptors = create_transport(tmp_path, tamper=tamper)
    with pytest.raises((AssemblyError, ProtocolError), match=error):
        assemble(Namespace(
            actions_root=actions_root,
            contracts=contracts,
            fixed_contracts=contracts,
            descriptors=descriptors,
            producer_root=producer_root,
            output_dir=actions_root / "out",
            previous_pressure=None,
            implementation_revision="a" * 40,
        ))


def test_assembler_emits_schema_complete_evidence_and_pressure(tmp_path):
    actions_root, producer_root, contracts, descriptors = create_transport(tmp_path)
    output = actions_root / "out"
    assert assemble(Namespace(
        actions_root=actions_root,
        contracts=contracts,
        fixed_contracts=contracts,
        descriptors=descriptors,
        producer_root=producer_root,
        output_dir=output,
        previous_pressure=None,
        implementation_revision="a" * 40,
    )) == 0
    evidence = load_json(output / "family-contract-evidence.json")
    pressure = load_json(output / "development-pressure/development-pressure-evidence.json")
    assert len(evidence["checks"]) == 8
    assert evidence["execution"]["aggregator_executes_producer_code"] is False
    assert evidence["development_pressure"]["obligation_count"] == 0
    assert pressure["obligations"] == []
    schema = load_json(REPO / "schemas/family-integration-evidence.schema.json")
    pytest.importorskip("jsonschema").validate(evidence, schema)
    pressure_schema = load_json(REPO / "schemas/development-pressure-evidence.schema.json")
    pytest.importorskip("jsonschema").validate(pressure, pressure_schema)


def test_descriptor_cannot_select_unknown_operation(tmp_path):
    contract = load_json(REPO / "family-contracts.json")
    descriptors = load_json(REPO / "family-producer-descriptors.json")
    descriptors["descriptors"][0]["execution"]["operation"] = "run-user-shell"
    with pytest.raises(ProtocolError, match="not allowlisted"):
        validate_descriptor_registry(descriptors, validate_fixed(contract))


def test_producer_envelope_rejects_check_file_marked_native(tmp_path):
    contract = load_json(REPO / "family-contracts.json")
    descriptors = load_json(REPO / "family-producer-descriptors.json")
    entries = validate_fixed(contract)
    descriptor = descriptor_map(descriptors, entries)["mncs-standard"]
    envelope = {
        "schema_version": "mncs-actions.family-producer-output/1",
        "mode": "fixed",
        "producer": "mncs-standard",
        "repository": descriptor["repository"],
        "revision": entries[0]["revision"],
        "descriptor_digest": document_digest(REPO / "family-producer-descriptors.json"),
        "files": [{"path": "checks/mncs-validation.json", "sha256": "a" * 64, "kind": "native"}],
        "check_results": [{"id": "mncs-validation", "path": "checks/mncs-validation.json", "sha256": "a" * 64}],
    }
    with pytest.raises(ProtocolError, match="not a check-result"):
        validate_producer_output(
            envelope,
            descriptor=descriptor,
            family_entry=entries[0],
            mode="fixed",
            expected_descriptor_digest=document_digest(REPO / "family-producer-descriptors.json"),
        )


def test_unknown_check_becomes_mncds_shaped_pressure_and_correlates_history():
    check = {
        "id": "mncs-language-family-boundary",
        "provider": "mncs-language",
        "producer": "mncs-language",
        "contract_revision": "mncs:language:compilation-study-result:0.1",
        "producer_revision": "a" * 40,
        "verdict": "UNKNOWN",
        "claim": "language study stages establish the family boundary",
        "unresolved": ["compiler cannot express evidence-set validation"],
        "path": "checks/language.json",
        "digest": "b" * 64,
    }
    bundle = build_pressure_bundle(
        [check], mode="fixed", contract_document="family-contracts.json",
        contract_digest="c" * 64, descriptor_document="family-producer-descriptors.json",
        descriptor_digest="d" * 64, actions_revision="e" * 40,
    )
    assert len(bundle["obligations"]) == 1
    obligation = bundle["obligations"][0]
    assert obligation["owner"] == "mncs-language"
    assert obligation["reproducer"]["source"]["producer_revision"] == "a" * 40
    assert obligation["affected_surfaces"] == ["language", "compiler", "tooling"]
    assert obligation["history"]["same_obligation_appeared_previously"] == "NOT_OBSERVED"
    repeated = build_pressure_bundle(
        [check], mode="fixed", contract_document="family-contracts.json",
        contract_digest="c" * 64, descriptor_document="family-producer-descriptors.json",
        descriptor_digest="d" * 64, actions_revision="f" * 40,
        previous=bundle,
    )
    assert repeated["obligations"][0]["history"]["same_obligation_appeared_previously"] == "YES"
    assert repeated["obligations"][0]["history"]["prior_pressure_id"] == obligation["pressure_id"]
