"""Shared validation for the bounded family producer protocol.

This module contains protocol mechanics only.  It deliberately has no owner
commands and no domain adapters: producer operations are an allowlisted
implementation detail of ``family_producer.py`` and the assembler consumes
their typed, content-addressed output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "lib"))
from mncs_actions import (  # noqa: E402
    canonical_bytes,
    is_safe_relative_path,
    sha256_hex,
    validate_check_result,
)

DESCRIPTOR_SCHEMA = "mncs-actions.family-producer-descriptors/1"
PRODUCER_OUTPUT_SCHEMA = "mncs-actions.family-producer-output/1"
INTEGRATION_EVIDENCE_SCHEMA = "mncs-actions.family-integration-evidence/1"
PRESSURE_EVIDENCE_SCHEMA = "mncs-actions.development-pressure-evidence/1"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

OPERATIONS = {
    "mncs-standard-validate",
    "rights-provenance-validate",
    "mncds-development-record-validate",
    "commons-compatibility-validate",
    "language-source-study",
    "forge-cell-validate",
}
ADAPTERS = {
    "validator-json-v1",
    "rights-json-v1",
    "commons-family-v1",
    "language-study-v1",
    "forge-cell-v1",
}
CAPABILITIES = {
    "python",
    "mncs-language-binary",
    "owner-python-package",
    "owner-compatibility-script",
    "owner-forge-package",
}


class ProtocolError(ValueError):
    """A malformed or ambiguous bounded family protocol document."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read JSON document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"JSON document must be an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def document_digest(path: Path) -> str:
    try:
        return sha256_hex(path.read_bytes())
    except OSError as exc:
        raise ProtocolError(f"cannot hash protocol document {path}: {exc}") from exc


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{label} must be non-empty text")
    return value


def _require_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not is_safe_relative_path(value):
        raise ProtocolError(f"{label} must be a safe relative path")
    return value


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise ProtocolError(f"{label} must be a lowercase 40-character SHA")
    return value


def _require_hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64_RE.fullmatch(value):
        raise ProtocolError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _check_output(output: Any, label: str) -> dict[str, str]:
    if not isinstance(output, dict):
        raise ProtocolError(f"{label} must be an object")
    return {
        "check_id": _require_text(output.get("check_id"), f"{label}.check_id"),
        "provider": _require_text(output.get("provider"), f"{label}.provider"),
        "contract_revision": _require_text(
            output.get("contract_revision"), f"{label}.contract_revision"
        ),
    }


def validate_descriptor_registry(
    document: dict[str, Any], family_entries: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    if document.get("schema_version") != DESCRIPTOR_SCHEMA:
        raise ProtocolError(f"descriptor schema_version must be {DESCRIPTOR_SCHEMA}")
    raw_descriptors = document.get("descriptors")
    if not isinstance(raw_descriptors, list) or not raw_descriptors:
        raise ProtocolError("descriptors must be a non-empty array")
    entries = list(family_entries)
    by_name = {entry.get("name"): entry for entry in entries}
    descriptors: list[dict[str, Any]] = []
    producers: set[str] = set()
    check_ids: set[str] = set()
    for index, raw in enumerate(raw_descriptors):
        label = f"descriptors[{index}]"
        if not isinstance(raw, dict):
            raise ProtocolError(f"{label} must be an object")
        producer = _require_text(raw.get("producer"), f"{label}.producer")
        if producer in producers:
            raise ProtocolError(f"duplicate producer descriptor: {producer}")
        entry = by_name.get(producer)
        if entry is None:
            raise ProtocolError(f"descriptor contains unknown producer: {producer}")
        repository = _require_text(raw.get("repository"), f"{label}.repository")
        if repository != entry.get("repository"):
            raise ProtocolError(f"descriptor repository mismatch for {producer}")
        paths = raw.get("artifact_paths")
        if not isinstance(paths, list) or not paths:
            raise ProtocolError(f"{label}.artifact_paths must be a non-empty array")
        normalized_paths = [_require_path(path, f"{label}.artifact_paths[]") for path in paths]
        if normalized_paths != entry.get("artifacts"):
            raise ProtocolError(f"descriptor artifact inventory mismatch for {producer}")
        contract = raw.get("contract")
        if not isinstance(contract, dict):
            raise ProtocolError(f"{label}.contract must be an object")
        _require_text(contract.get("id"), f"{label}.contract.id")
        _require_text(contract.get("revision"), f"{label}.contract.revision")
        adapter_id = raw.get("adapter_id")
        if adapter_id not in ADAPTERS:
            raise ProtocolError(f"{label}.adapter_id is not allowlisted")
        capabilities = raw.get("required_capabilities")
        if not isinstance(capabilities, list) or not capabilities or not all(
            isinstance(capability, str) and capability in CAPABILITIES for capability in capabilities
        ):
            raise ProtocolError(f"{label}.required_capabilities are invalid")
        execution = raw.get("execution")
        if not isinstance(execution, dict) or execution.get("mode") != "owner-native":
            raise ProtocolError(f"{label}.execution must declare owner-native mode")
        operation = execution.get("operation")
        if operation not in OPERATIONS:
            raise ProtocolError(f"{label}.execution.operation is not allowlisted")
        input_paths = execution.get("input_paths")
        if not isinstance(input_paths, dict):
            raise ProtocolError(f"{label}.execution.input_paths must be an object")
        for key, path in input_paths.items():
            _require_text(key, f"{label}.execution.input_paths key")
            _require_path(path, f"{label}.execution.input_paths.{key}")
        if "cases" in execution:
            cases = execution["cases"]
            if not isinstance(cases, list) or not cases:
                raise ProtocolError(f"{label}.execution.cases must be a non-empty array")
            case_names: set[str] = set()
            for case_index, case in enumerate(cases):
                case_label = f"{label}.execution.cases[{case_index}]"
                if not isinstance(case, dict):
                    raise ProtocolError(f"{case_label} must be an object")
                name = _require_text(case.get("name"), f"{case_label}.name")
                if name in case_names:
                    raise ProtocolError(f"duplicate descriptor case: {name}")
                case_names.add(name)
                _require_path(case.get("source_path"), f"{case_label}.source_path")
                _require_text(case.get("check_id"), f"{case_label}.check_id")
        if "expected_nonce" in execution:
            nonce = execution["expected_nonce"]
            if not isinstance(nonce, str) or not nonce or len(nonce) > 128 or any(
                ord(char) < 0x20 for char in nonce
            ):
                raise ProtocolError(f"{label}.execution.expected_nonce is invalid")
        outputs = raw.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ProtocolError(f"{label}.outputs must be a non-empty array")
        normalized_outputs = []
        for output_index, output in enumerate(outputs):
            normalized = _check_output(output, f"{label}.outputs[{output_index}]")
            if normalized["check_id"] in check_ids:
                raise ProtocolError(f"duplicate declared check id: {normalized['check_id']}")
            check_ids.add(normalized["check_id"])
            normalized_outputs.append(normalized)
        producers.add(producer)
        descriptors.append(raw)
    expected_producers = set(by_name)
    if producers != expected_producers:
        missing = sorted(expected_producers - producers)
        extra = sorted(producers - expected_producers)
        raise ProtocolError(f"descriptor membership mismatch; missing={missing}, extra={extra}")
    return descriptors


def descriptor_map(document: dict[str, Any], family_entries: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    descriptors = validate_descriptor_registry(document, family_entries)
    return {descriptor["producer"]: descriptor for descriptor in descriptors}


def descriptor_outputs(descriptor: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {output["check_id"]: output for output in descriptor["outputs"]}


def validate_producer_output(
    document: dict[str, Any],
    *,
    descriptor: dict[str, Any],
    family_entry: dict[str, Any],
    mode: str,
    expected_descriptor_digest: str,
) -> list[dict[str, str]]:
    if document.get("schema_version") != PRODUCER_OUTPUT_SCHEMA:
        raise ProtocolError(f"producer output schema_version must be {PRODUCER_OUTPUT_SCHEMA}")
    if document.get("mode") != mode or mode not in {"fixed", "moving-head"}:
        raise ProtocolError("producer output mode is inconsistent")
    if document.get("producer") != descriptor["producer"]:
        raise ProtocolError("producer output identity does not match descriptor")
    if document.get("repository") != descriptor["repository"]:
        raise ProtocolError("producer output repository does not match descriptor")
    expected_revision = family_entry.get("revision", family_entry.get("candidate_revision"))
    _require_sha(document.get("revision"), "producer output revision")
    if document["revision"] != expected_revision:
        raise ProtocolError(f"producer output revision mismatch for {descriptor['producer']}")
    if document.get("descriptor_digest") != expected_descriptor_digest:
        raise ProtocolError(f"producer output descriptor digest mismatch for {descriptor['producer']}")
    files = document.get("files")
    if not isinstance(files, list) or not files:
        raise ProtocolError("producer output files must be a non-empty array")
    seen_paths: set[str] = set()
    file_kinds: dict[str, str] = {}
    for index, item in enumerate(files):
        label = f"producer output files[{index}]"
        if not isinstance(item, dict):
            raise ProtocolError(f"{label} must be an object")
        path = _require_path(item.get("path"), f"{label}.path")
        digest = _require_hex(item.get("sha256"), f"{label}.sha256")
        kind = _require_text(item.get("kind"), f"{label}.kind")
        if path in seen_paths:
            raise ProtocolError(f"duplicate producer output path: {path}")
        seen_paths.add(path)
        file_kinds[path] = kind
        if kind not in {"native", "check-result"}:
            raise ProtocolError(f"unsupported producer output file kind: {kind}")
        item["path"] = path
        item["sha256"] = digest
    expected_ids = set(descriptor_outputs(descriptor))
    outputs = document.get("check_results")
    if not isinstance(outputs, list) or not outputs:
        raise ProtocolError("producer output check_results must be a non-empty array")
    seen_ids: set[str] = set()
    for index, output in enumerate(outputs):
        label = f"producer output check_results[{index}]"
        if not isinstance(output, dict):
            raise ProtocolError(f"{label} must be an object")
        check_id = _require_text(output.get("id"), f"{label}.id")
        if check_id in seen_ids:
            raise ProtocolError(f"duplicate producer check identity: {check_id}")
        seen_ids.add(check_id)
        if check_id not in expected_ids:
            raise ProtocolError(f"producer emitted undeclared check id: {check_id}")
        path = _require_path(output.get("path"), f"{label}.path")
        if path not in seen_paths:
            raise ProtocolError(f"producer check path is absent from files: {path}")
        if file_kinds[path] != "check-result":
            raise ProtocolError(f"producer check path is not a check-result file: {path}")
        _require_hex(output.get("sha256"), f"{label}.sha256")
    if seen_ids != expected_ids:
        raise ProtocolError(
            f"producer check membership mismatch for {descriptor['producer']}: "
            f"missing={sorted(expected_ids - seen_ids)} extra={sorted(seen_ids - expected_ids)}"
        )
    return [{"id": item["id"], "path": item["path"], "sha256": item["sha256"]} for item in outputs]


def validate_family_integration_evidence(
    evidence: dict[str, Any],
    *,
    expected_mode: str,
    expected_contract_digest: str,
    expected_descriptor_digest: str,
    expected_revisions: dict[str, dict[str, str]],
    expected_checks: dict[str, dict[str, str]],
) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != INTEGRATION_EVIDENCE_SCHEMA:
        errors.append(f"schema_version must be {INTEGRATION_EVIDENCE_SCHEMA}")
    if evidence.get("mode") != expected_mode:
        errors.append("mode does not match the executed contract")
    if evidence.get("contract_digest") != expected_contract_digest:
        errors.append("contract_digest does not match exact contract bytes")
    if evidence.get("descriptor_digest") != expected_descriptor_digest:
        errors.append("descriptor_digest does not match exact descriptor bytes")
    for field in ("contract_document", "descriptor_document"):
        if not isinstance(evidence.get(field), str) or not is_safe_relative_path(evidence[field]):
            errors.append(f"{field} must be a safe relative path")
    revisions = evidence.get("family_revisions")
    if not isinstance(revisions, dict) or set(revisions) != set(expected_revisions):
        errors.append("family_revisions membership does not match the contract")
    else:
        for producer, expected in expected_revisions.items():
            item = revisions.get(producer)
            if not isinstance(item, dict):
                errors.append(f"family_revisions.{producer} must be an object")
                continue
            for field in ("repository", "branch", "revision"):
                if item.get(field) != expected[field]:
                    errors.append(f"family_revisions.{producer}.{field} mismatch")
    checks = evidence.get("checks")
    checks_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(checks, list):
        errors.append("checks must be an array")
    else:
        seen_ids: set[str] = set()
        seen_producers: set[str] = set()
        for index, item in enumerate(checks):
            label = f"checks[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{label} must be an object")
                continue
            check_id = item.get("id")
            if not isinstance(check_id, str) or not check_id:
                errors.append(f"{label}.id must be non-empty")
                continue
            if check_id in seen_ids:
                errors.append(f"duplicate evidence check id: {check_id}")
            seen_ids.add(check_id)
            checks_by_id[check_id] = item
            expected = expected_checks.get(check_id)
            if expected is None:
                errors.append(f"unexpected evidence check id: {check_id}")
                continue
            if item.get("provider") != expected["provider"]:
                errors.append(f"{label}.provider mismatch")
            if item.get("producer") != expected["producer"]:
                errors.append(f"{label}.producer mismatch")
            if item.get("producer_revision") != expected_revisions[expected["producer"]]["revision"]:
                errors.append(f"{label}.producer_revision mismatch")
            if item.get("contract_revision") != expected["contract_revision"]:
                errors.append(f"{label}.contract_revision mismatch")
            if not isinstance(item.get("path"), str) or not is_safe_relative_path(item["path"]):
                errors.append(f"{label}.path is unsafe")
            if not isinstance(item.get("digest"), str) or not HEX64_RE.fullmatch(item["digest"]):
                errors.append(f"{label}.digest is invalid")
            if expected["producer"] in seen_producers:
                # A producer may have multiple declared checks (language),
                # so this set is intentionally only used for duplicate
                # producer/check identity diagnostics below.
                pass
            seen_producers.add(expected["producer"])
        if seen_ids != set(expected_checks):
            errors.append(
                "evidence check membership mismatch: "
                f"missing={sorted(set(expected_checks) - seen_ids)} "
                f"extra={sorted(seen_ids - set(expected_checks))}"
            )
    authority = evidence.get("authority")
    if not isinstance(authority, dict) or authority.get("orchestration") != "mncs-actions":
        errors.append("authority must identify mncs-actions orchestration")
    promotion = evidence.get("promotion")
    if promotion != "observation-only; this document cannot update family-contracts.json":
        errors.append("promotion authority was not explicitly observation-only")
    execution = evidence.get("execution")
    if not isinstance(execution, dict):
        errors.append("execution boundary metadata is required")
    else:
        if execution.get("producer_jobs") is not True:
            errors.append("execution.producer_jobs must be true")
        if execution.get("aggregator_executes_producer_code") is not False:
            errors.append("aggregator must declare that it does not execute producer code")
        if execution.get("artifact_transport") != "content-addressed":
            errors.append("artifact transport must be content-addressed")
    pressure = evidence.get("development_pressure")
    if not isinstance(pressure, dict):
        errors.append("development_pressure binding is required")
    else:
        if not isinstance(pressure.get("path"), str) or not is_safe_relative_path(pressure["path"]):
            errors.append("development_pressure.path is unsafe")
        if not isinstance(pressure.get("digest"), str) or not HEX64_RE.fullmatch(pressure["digest"]):
            errors.append("development_pressure.digest is invalid")
        if not isinstance(pressure.get("obligation_count"), int) or pressure["obligation_count"] < 0:
            errors.append("development_pressure.obligation_count is invalid")
    obligations = evidence.get("unresolved_obligations")
    if not isinstance(obligations, list):
        errors.append("unresolved_obligations must be an array")
    else:
        for index, obligation in enumerate(obligations):
            if not isinstance(obligation, dict):
                errors.append(f"unresolved_obligations[{index}] must be an object")
                continue
            for field in (
                "obligation_key", "check_id", "owner", "producer", "producer_revision",
                "category", "claim",
            ):
                if not isinstance(obligation.get(field), str) or not obligation[field]:
                    errors.append(f"unresolved_obligations[{index}].{field} is required")
            check = checks_by_id.get(obligation.get("check_id"))
            if check is not None:
                if obligation.get("producer") != check.get("producer"):
                    errors.append(f"unresolved_obligations[{index}].producer does not match its check")
                if obligation.get("producer_revision") != check.get("producer_revision"):
                    errors.append(
                        f"unresolved_obligations[{index}].producer_revision does not match its check"
                    )
    return errors


def canonical_digest(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))
