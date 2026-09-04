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
    MAX_ARTIFACT_BYTES,
    MAX_JSON_DEPTH,
    ProtocolError,
    descriptor_map,
    descriptor_outputs,
    document_digest,
    ensure_clean_directory,
    load_json,
    load_json_bytes,
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
            files.append({"path": relative, "sha256": digest, "size": path.stat().st_size, "kind": "check-result"})
        provenance_binding = None
        if descriptor.get("provenance_context"):
            context = descriptor["provenance_context"]
            native = producer_dir / "native" / context["transport_name"]
            write_json(native, {"fixture": "pinned authority input"})
            native_relative = native.relative_to(producer_dir).as_posix()
            native_digest = document_digest(native)
            files.append({
                "path": native_relative,
                "sha256": native_digest,
                "size": native.stat().st_size,
                "kind": "native",
            })
            provenance_binding = {
                "kind": context["kind"],
                "authority": context["authority"],
                "path": native_relative,
                "sha256": native_digest,
                "authority_status": context["authority_status"],
                "revision": by_name[producer]["revision"],
            }
        envelope = {
            "schema_version": "mncs-actions.family-producer-output/2",
            "mode": "fixed",
            "producer": producer,
            "repository": descriptor["repository"],
            "revision": by_name[producer]["revision"],
            "descriptor_digest": document_digest(actions_root / "descriptors.json"),
            "contract_digest": document_digest(actions_root / "family-contracts.json"),
            "files": files,
            "check_results": checks,
        }
        if provenance_binding is not None:
            envelope["provenance_bindings"] = [provenance_binding]
        if tamper == "foreign-check" and producer == "mncs-standard":
            envelope["check_results"][0]["id"] = "rights-provenance"
        if tamper == "path-traversal" and producer == "mncs-standard":
            envelope["check_results"][0]["path"] = "../escape.json"
        if tamper == "stale-digest" and producer == "mncs-standard":
            path.write_text(path.read_text() + "\n", encoding="utf-8")
        if tamper == "malformed-check" and producer == "mncs-standard":
            path.write_bytes(b"{\xff")
            digest = document_digest(path)
            envelope["check_results"][0]["sha256"] = digest
            envelope["files"][0]["sha256"] = digest
            envelope["files"][0]["size"] = path.stat().st_size
        if tamper == "wrong-revision" and producer == "mncs-standard":
            envelope["revision"] = "0" * 40
        if tamper == "contract-substitution" and producer == "mncs-standard":
            envelope["contract_digest"] = "0" * 64
        if tamper == "duplicate-file-entry" and producer == "mncs-standard":
            envelope["files"].append(copy.deepcopy(envelope["files"][0]))
        if tamper == "normalized-path-alias" and producer == "mncs-standard":
            alias = copy.deepcopy(envelope["files"][0])
            alias["path"] = "checks/MNCS-VALIDATION.json"
            envelope["files"].append(alias)
        if tamper == "control-file-declared" and producer == "mncs-standard":
            control = copy.deepcopy(envelope["files"][0])
            control["path"] = "producer-execution.json"
            envelope["files"].append(control)
        if tamper == "oversized-declaration" and producer == "mncs-standard":
            envelope["files"][0]["size"] = MAX_ARTIFACT_BYTES + 1
        if tamper == "duplicate-check-path" and producer == "mncs-standard":
            envelope["check_results"].append(copy.deepcopy(envelope["check_results"][0]))
        write_json(producer_dir / "producer-execution.json", envelope)
        if tamper == "undeclared-file" and producer == "mncs-standard":
            write_json(producer_dir / "native" / "undeclared.json", {"stale": True})
        if tamper == "unreferenced-check-file" and producer == "mncs-standard":
            extra = producer_dir / "checks" / "unreferenced.json"
            write_json(extra, {"foreign": True})
            extra_relative = extra.relative_to(producer_dir).as_posix()
            extra_digest = document_digest(extra)
            envelope = load_json(producer_dir / "producer-execution.json")
            envelope["files"].append({
                "path": extra_relative,
                "sha256": extra_digest,
                "size": extra.stat().st_size,
                "kind": "check-result",
            })
            write_json(producer_dir / "producer-execution.json", envelope)
        if tamper == "symlink-file" and producer == "mncs-standard":
            (producer_dir / "native").mkdir(parents=True, exist_ok=True)
            (producer_dir / "native" / "link.json").symlink_to(
                producer_dir / "checks" / "mncs-validation.json"
            )
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
    ("contract-substitution", "contract digest mismatch"),
    ("duplicate-file-entry", "duplicate producer output path"),
    ("normalized-path-alias", "ambiguous producer output paths"),
    ("control-file-declared", "control file cannot be declared"),
    ("oversized-declaration", "size must be between"),
    ("duplicate-check-path", "duplicate producer check identity"),
    ("undeclared-file", "transport membership mismatch"),
    ("unreferenced-check-file", "check-result file membership mismatch"),
    ("malformed-check", "bounded UTF-8 JSON"),
    ("symlink-file", "symlink is not permitted"),
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
    assert len(evidence["checks"]) == 10
    assert evidence["execution"]["aggregator_executes_producer_code"] is False
    assert evidence["execution"]["candidate_isolation"] is False
    assert evidence["development_pressure"]["obligation_count"] == 0
    assert pressure["obligations"] == []
    assert evidence["observation"]["resolution_status"] == "NOT_ESTABLISHED"
    assert evidence["provenance_bindings"][0]["authority"] == "mncs-rights-provenance"
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
        "schema_version": "mncs-actions.family-producer-output/2",
        "mode": "fixed",
        "producer": "mncs-standard",
        "repository": descriptor["repository"],
        "revision": entries[0]["revision"],
        "descriptor_digest": document_digest(REPO / "family-producer-descriptors.json"),
        "contract_digest": document_digest(REPO / "family-contracts.json"),
        "files": [{"path": "checks/mncs-validation.json", "sha256": "a" * 64, "size": 1, "kind": "native"}],
        "check_results": [{"id": "mncs-validation", "path": "checks/mncs-validation.json", "sha256": "a" * 64}],
    }
    with pytest.raises(ProtocolError, match="native files cannot be under checks"):
        validate_producer_output(
            envelope,
            descriptor=descriptor,
            family_entry=entries[0],
            mode="fixed",
            expected_descriptor_digest=document_digest(REPO / "family-producer-descriptors.json"),
            expected_contract_digest=document_digest(REPO / "family-contracts.json"),
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
        "evidence_provider": "mncs-language",
        "semantic_authority": "mncs-language",
        "remediation_owner": "epi13/mncs-language",
        "transport_authority": "mncs-actions",
        "originating_project": "epi13/mncs-actions",
    }
    bundle = build_pressure_bundle(
        [check], mode="fixed", contract_document="family-contracts.json",
        contract_digest="c" * 64, descriptor_document="family-producer-descriptors.json",
        descriptor_digest="d" * 64, actions_revision="e" * 40,
    )
    assert len(bundle["obligations"]) == 1
    obligation = bundle["obligations"][0]
    assert obligation["owner"] == "epi13/mncs-language"
    assert obligation["remediation_owner"] == "epi13/mncs-language"
    assert obligation["semantic_authority"] == "mncs-language"
    assert obligation["category"] == "language/compiler capability"
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


def test_not_reproduced_remains_open_and_not_resolved():
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
    previous = build_pressure_bundle(
        [check], mode="fixed", contract_document="family-contracts.json",
        contract_digest="c" * 64, descriptor_document="family-producer-descriptors.json",
        descriptor_digest="d" * 64, actions_revision="e" * 40,
    )
    current = build_pressure_bundle(
        [{**check, "verdict": "PASS", "unresolved": []}], mode="fixed",
        contract_document="family-contracts.json", contract_digest="c" * 64,
        descriptor_document="family-producer-descriptors.json", descriptor_digest="d" * 64,
        actions_revision="f" * 40, previous=previous,
    )
    assert current["obligations"] == []
    item = current["not_reproduced"][0]
    assert item["current_status"] == "NOT_REPRODUCED"
    assert item["lifecycle"]["resolution_status"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("relative", [
    "checks/stale.json",
    "development-pressure/stale.json",
    "aggregate-evidence/evidence.json",
])
def test_assembler_rejects_nested_stale_output(tmp_path, relative):
    actions_root, producer_root, contracts, descriptors = create_transport(tmp_path)
    output = actions_root / "out"
    stale = output / relative
    stale.parent.mkdir(parents=True)
    stale.write_text("{}\n", encoding="utf-8")
    with pytest.raises(AssemblyError, match="must be empty"):
        assemble(Namespace(
            actions_root=actions_root,
            contracts=contracts,
            fixed_contracts=contracts,
            descriptors=descriptors,
            producer_root=producer_root,
            output_dir=output,
            previous_pressure=None,
            implementation_revision="a" * 40,
        ))


def test_protocol_json_is_strict_and_bounded():
    with pytest.raises(ProtocolError, match="non-standard JSON constant"):
        load_json_bytes(b'{"value": NaN}', label="fixture")
    with pytest.raises(ProtocolError, match="depth limit"):
        load_json_bytes((b"[" * (MAX_JSON_DEPTH + 1)) + (b"]" * (MAX_JSON_DEPTH + 1)), label="fixture")
    with pytest.raises(ProtocolError, match="cannot parse"):
        load_json_bytes(b"{\xff", label="fixture")
    with pytest.raises(ProtocolError, match="protocol JSON limit"):
        load_json_bytes(b"{" + b'\"x\":\"' + (b"a" * (8 * 1024 * 1024)) + b"\"}", label="fixture")


def test_ensure_clean_directory_rejects_nested_entries(tmp_path):
    output = tmp_path / "output"
    (output / "nested").mkdir(parents=True)
    (output / "nested" / "stale.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ProtocolError, match="must be empty"):
        ensure_clean_directory(output)
