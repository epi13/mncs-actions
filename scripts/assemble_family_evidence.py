#!/usr/bin/env python3
"""Assemble untrusted producer artifacts without executing family code.

The assembler is the only stage that creates family integration evidence and
aggregate inputs.  It reads producer envelopes and check-results, verifies
their exact bytes, membership, producer identity, and expected revisions, and
then runs only mncs-actions adapters/validation code.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from family_contracts import ContractError, _read, validate_against_fixed, validate_candidate, validate_fixed  # noqa: E402
from family_protocol import (  # noqa: E402
    MAX_TRANSPORT_BYTES,
    ProtocolError,
    descriptor_map,
    descriptor_outputs,
    document_digest,
    ensure_clean_directory,
    load_json,
    load_json_bytes,
    validate_transport_tree,
    validate_family_integration_evidence,
    validate_producer_output,
    write_json,
)
from development_pressure import build_pressure_bundle  # noqa: E402
from mncs_actions import sha256_hex, validate_check_result  # noqa: E402


class AssemblyError(RuntimeError):
    """Artifact transport did not establish an unambiguous family set."""


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise AssemblyError(f"artifact path escapes producer root: {relative}")
    return candidate


def _relative(path: Path, root: Path) -> str:
    try:
        value = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise AssemblyError(f"path is outside actions root: {path}") from exc
    if not value or value.startswith("../") or "/../" in value:
        raise AssemblyError(f"unsafe evidence path: {value}")
    return value


def _implementation_revision(actions_root: Path, asserted: str) -> str:
    if asserted:
        return asserted
    process = subprocess.run(
        ["git", "-C", str(actions_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _envelopes(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if root.is_symlink() or not root.is_dir():
        raise AssemblyError(f"producer artifact root must be a real directory: {root}")
    found = []
    envelope_dirs: set[Path] = set()
    for path in sorted(root.rglob("producer-execution.json")):
        if path.is_symlink() or not path.is_file():
            raise AssemblyError(f"producer envelope must be a regular file: {path}")
        directory = path.parent.resolve()
        envelope_dirs.add(directory)
        found.append((directory, load_json(path)))
    if not found:
        raise AssemblyError("producer artifact transport contains no envelopes")
    # Do not silently ignore files that are outside a producer envelope. This
    # catches stale downloads and artifact-name/path tricks before aggregation.
    for path in root.rglob("*"):
        if path.is_symlink():
            raise AssemblyError(f"symlink is not permitted in producer artifact root: {path}")
        if path.is_file():
            resolved = path.resolve()
            if any(directory == resolved.parent or directory in resolved.parents for directory in envelope_dirs):
                continue
            raise AssemblyError(f"file is outside a producer envelope: {path}")
    return found


def _load_previous(path: Path | None) -> dict[str, Any] | None:
    return load_json(path) if path else None


def run(args: argparse.Namespace) -> int:
    actions_root = args.actions_root.resolve()
    producer_root = args.producer_root.resolve()
    output_dir = args.output_dir.resolve()
    contract_path = args.contracts.resolve()
    descriptor_path = args.descriptors.resolve()
    contract = _read(contract_path)
    if contract.get("schema_version") == "mncs-actions.family-contracts/1":
        entries = validate_fixed(contract)
        mode = "fixed"
    else:
        entries = validate_candidate(contract)
        validate_against_fixed(contract, _read(args.fixed_contracts.resolve()))
        mode = "moving-head"
    by_name = {entry["name"]: entry for entry in entries}
    descriptors_document = load_json(descriptor_path)
    descriptors = descriptor_map(descriptors_document, entries)
    descriptor_digest = document_digest(descriptor_path)
    contract_digest = document_digest(contract_path)
    expected_revisions = {
        name: {
            "repository": entry["repository"],
            "branch": entry.get("branch", "fixed-contract"),
            "revision": entry.get("revision", entry.get("candidate_revision")),
        }
        for name, entry in by_name.items()
    }
    expected_checks: dict[str, dict[str, str]] = {}
    for producer, descriptor in descriptors.items():
        for check_id, output in descriptor_outputs(descriptor).items():
            expected_checks[check_id] = {
                **output,
                "producer": producer,
            }

    try:
        ensure_clean_directory(output_dir, label="assembly output directory")
    except ProtocolError as exc:
        raise AssemblyError(str(exc)) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    check_values: list[dict[str, Any]] = []
    check_records: list[dict[str, Any]] = []
    provenance_bindings: list[dict[str, Any]] = []
    seen_producers: set[str] = set()
    for producer_dir, envelope in _envelopes(producer_root):
        producer = envelope.get("producer")
        if producer in seen_producers:
            raise AssemblyError(f"duplicate producer artifact: {producer}")
        if producer not in by_name:
            raise AssemblyError(f"unexpected producer artifact: {producer}")
        seen_producers.add(producer)
        descriptor = descriptors[producer]
        family_entry = by_name[producer]
        output_entries = validate_producer_output(
            envelope,
            descriptor=descriptor,
            family_entry=family_entry,
            mode=mode,
            expected_descriptor_digest=descriptor_digest,
            expected_contract_digest=contract_digest,
        )
        try:
            validate_transport_tree(producer_dir, [item["path"] for item in envelope["files"]])
        except ProtocolError as exc:
            raise AssemblyError(str(exc)) from exc
        for binding in envelope.get("provenance_bindings", []):
            provenance_bindings.append({"producer": producer, **binding})
        transport_bytes = 0
        # Verify every transported byte, including native reports that are
        # not themselves aggregate inputs. A producer cannot record one
        # digest and upload another file after its job finishes.
        for file_entry in envelope["files"]:
            transported = _inside(producer_dir, file_entry["path"])
            if not transported.is_file():
                raise AssemblyError(f"declared producer file does not exist: {file_entry['path']}")
            raw_transport = transported.read_bytes()
            transport_bytes += len(raw_transport)
            if len(raw_transport) != file_entry["size"]:
                raise AssemblyError(f"producer file size changed after digest recording: {file_entry['path']}")
            if transport_bytes > MAX_TRANSPORT_BYTES:
                raise AssemblyError(f"producer transport exceeds {MAX_TRANSPORT_BYTES} bytes")
            if sha256_hex(raw_transport) != file_entry["sha256"]:
                raise AssemblyError(
                    f"producer file changed after digest recording: {file_entry['path']}"
                )
        for item in output_entries:
            source = _inside(producer_dir, item["path"])
            if not source.is_file():
                raise AssemblyError(f"declared producer file does not exist: {item['path']}")
            raw = source.read_bytes()
            actual_digest = sha256_hex(raw)
            if actual_digest != item["sha256"]:
                raise AssemblyError(f"producer file changed after digest recording: {item['path']}")
            try:
                value = load_json_bytes(raw, label=f"producer check {item['path']}")
            except ProtocolError as exc:
                raise AssemblyError(f"producer check is not bounded UTF-8 JSON: {item['path']}: {exc}") from exc
            errors = validate_check_result(value)
            if errors:
                raise AssemblyError(f"invalid transported check {item['path']}: {'; '.join(errors)}")
            expected = expected_checks.get(value.get("id"))
            if expected is None:
                raise AssemblyError(f"transported check is not a declared family check: {value.get('id')}")
            if expected["producer"] != producer:
                raise AssemblyError(f"producer substituted another producer's check: {value.get('id')}")
            expected_revision = expected_revisions[producer]["revision"]
            for field, expected_value in (
                ("provider", expected["provider"]),
                ("contract_revision", expected["contract_revision"]),
                ("producer_revision", expected_revision),
            ):
                if value.get(field) != expected_value:
                    raise AssemblyError(f"transported check {value.get('id')} has wrong {field}")
            for reference in value.get("references", []):
                if not isinstance(reference, dict):
                    continue
                reference_path = reference.get("path")
                reference_digest = reference.get("digest")
                if reference_path in {item["path"] for item in envelope["files"]}:
                    if not isinstance(reference_digest, str):
                        raise AssemblyError(f"transported check reference lacks digest: {reference_path}")
                    normalized_digest = reference_digest.removeprefix("sha256:")
                    declared_digest = next(
                        item["sha256"] for item in envelope["files"] if item["path"] == reference_path
                    )
                    if normalized_digest != declared_digest:
                        raise AssemblyError(f"transported check reference digest mismatch: {reference_path}")
            destination = output_dir / "checks" / f"{value['id']}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            evidence_path = _relative(destination, actions_root)
            check_values.append({
                **value,
                "path": evidence_path,
                "digest": actual_digest,
                "producer": producer,
                **expected["roles"],
            })
            check_records.append(
                {
                    "id": value["id"],
                    "provider": value["provider"],
                    "producer": producer,
                    "producer_revision": expected_revision,
                    "contract_revision": value["contract_revision"],
                    "verdict": value["verdict"],
                    "path": evidence_path,
                    "digest": actual_digest,
                    **expected["roles"],
                }
            )
    if seen_producers != set(by_name):
        raise AssemblyError(
            f"producer membership mismatch: missing={sorted(set(by_name) - seen_producers)}"
        )
    if {record["id"] for record in check_records} != set(expected_checks):
        raise AssemblyError("transported check membership does not match descriptors")

    check_values.sort(key=lambda value: value["id"])
    check_records.sort(key=lambda value: value["id"])
    implementation_revision = _implementation_revision(actions_root, args.implementation_revision)
    previous = _load_previous(args.previous_pressure.resolve() if args.previous_pressure else None)
    pressure = build_pressure_bundle(
        check_values,
        mode=mode,
        contract_document=_relative(contract_path, actions_root),
        contract_digest=contract_digest,
        descriptor_document=_relative(descriptor_path, actions_root),
        descriptor_digest=descriptor_digest,
        actions_revision=implementation_revision,
        previous=previous,
    )
    pressure_path = output_dir / "development-pressure/development-pressure-evidence.json"
    write_json(pressure_path, pressure)
    pressure_digest = sha256_hex(pressure_path.read_bytes())
    unresolved = [
        {
            "obligation_key": item["obligation_key"],
            "pressure_id": item["pressure_id"],
            "check_id": item["reproducer"]["source"]["check_id"],
            "owner": item["owner"],
            "producer": item["reproducer"]["source"]["producer"],
            "producer_revision": item["reproducer"]["source"]["producer_revision"],
            "category": item["category"],
            "claim": item["requested_capability"],
            "current_limitation": item["current_limitation"],
            "evidence_provider": item["evidence_provider"],
            "semantic_authority": item["semantic_authority"],
            "remediation_owner": item["remediation_owner"],
            "transport_authority": item["transport_authority"],
            "originating_project": item["originating_project"],
            "lifecycle": item["lifecycle"],
            }
        for item in pressure["obligations"]
    ]
    evidence = {
        "schema_version": "mncs-actions.family-integration-evidence/2",
        "mode": mode,
        "contract_document": _relative(contract_path, actions_root),
        "contract_digest": contract_digest,
        "descriptor_document": _relative(descriptor_path, actions_root),
        "descriptor_digest": descriptor_digest,
        "family_revisions": expected_revisions,
        "checks": check_records,
        "provenance_bindings": provenance_bindings,
        "unresolved_obligations": unresolved,
        "authority": {
            "mncs": "machine-native-complexity-standard",
            "mncds": "machine-native-complexity-development-specification",
            "commons": "MNCS-Commons",
            "rights": "mncs-rights-provenance",
            "language": "mncs-language",
            "forge": "mncs-forge-mcp",
            "orchestration": "mncs-actions",
        },
        "promotion": "observation-only; this document cannot update family-contracts.json",
        "execution": {
            "protocol": "mncs-actions.family-producer-output/2",
            "producer_jobs": True,
            "aggregator_executes_producer_code": False,
            "artifact_transport": "content-addressed",
            "candidate_isolation": mode == "moving-head",
        },
        "development_pressure": {
            "path": _relative(pressure_path, actions_root),
            "digest": pressure_digest,
            "obligation_count": len(pressure["obligations"]),
            "not_reproduced_count": len(pressure["not_reproduced"]),
        },
        "observation": pressure["observation"],
    }
    errors = validate_family_integration_evidence(
        evidence,
        expected_mode=mode,
        expected_contract_digest=contract_digest,
        expected_descriptor_digest=descriptor_digest,
        expected_revisions=expected_revisions,
        expected_checks=expected_checks,
    )
    if errors:
        raise AssemblyError("invalid family integration evidence: " + "; ".join(errors))
    write_json(output_dir / "family-contract-evidence.json", evidence)
    print(
        json.dumps(
            {
                "mode": mode,
                "checks": len(check_records),
                "unresolved_obligations": len(unresolved),
                "output": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actions-root", type=Path, default=ROOT)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--fixed-contracts", type=Path, default=ROOT / "family-contracts.json")
    parser.add_argument("--descriptors", type=Path, default=ROOT / "family-producer-descriptors.json")
    parser.add_argument("--producer-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--previous-pressure", type=Path)
    parser.add_argument("--implementation-revision", default="")
    args = parser.parse_args()
    try:
        return run(args)
    except (ContractError, ProtocolError, AssemblyError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
