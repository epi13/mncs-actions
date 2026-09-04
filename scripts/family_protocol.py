"""Shared validation for the bounded family producer protocol.

This module contains protocol mechanics only.  It deliberately has no owner
commands and no domain adapters: producer operations are an allowlisted
implementation detail of ``family_producer.py`` and the assembler consumes
their typed, content-addressed output.
"""

from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "lib"))
from mncs_actions import (  # noqa: E402
    canonical_bytes,
    is_safe_relative_path,
    sha256_hex,
)

DESCRIPTOR_SCHEMA = "mncs-actions.family-producer-descriptors/2"
PRODUCER_OUTPUT_SCHEMA = "mncs-actions.family-producer-output/2"
INTEGRATION_EVIDENCE_SCHEMA = "mncs-actions.family-integration-evidence/2"
PRESSURE_EVIDENCE_SCHEMA = "mncs-actions.development-pressure-evidence/2"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# These are protocol limits, not runner-sandbox claims.  They keep the
# artifact-only boundary finite on every transport implementation: a family
# producer may carry at most 128 files, each file is at most 8 MiB, the whole
# envelope is at most 64 MiB, and protocol JSON is at most 8 MiB / 64 levels
# deep.  The limits are deliberately well above the current family fixtures
# while preventing unbounded memory, recursion, and artifact amplification.
MAX_PROTOCOL_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_TRANSPORT_BYTES = 64 * 1024 * 1024
MAX_TRANSPORT_FILES = 128
MAX_JSON_DEPTH = 64
MAX_PATH_LENGTH = 512
MAX_BINDINGS = 128
MAX_OWNER_OPERATION_SECONDS = 240
MAX_PRODUCER_JOB_SECONDS = 900

ROLE_FIELDS = (
    "evidence_provider",
    "semantic_authority",
    "remediation_owner",
    "transport_authority",
    "originating_project",
)
ROLE_TRANSPORT_AUTHORITY = "mncs-actions"

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
    "mncds-json-v1",
    "rights-json-v1",
    "commons-family-v1",
    "language-study-v1",
    "forge-cell-v1",
}
OPERATION_ADAPTERS = {
    "mncs-standard-validate": "validator-json-v1",
    "rights-provenance-validate": "rights-json-v1",
    "mncds-development-record-validate": "mncds-json-v1",
    "commons-compatibility-validate": "commons-family-v1",
    "language-source-study": "language-study-v1",
    "forge-cell-validate": "forge-cell-v1",
}
OPERATION_INPUTS = {
    "mncs-standard-validate": {"manifest"},
    "rights-provenance-validate": {"manifest"},
    "mncds-development-record-validate": {"record"},
    "commons-compatibility-validate": {"registry", "validator"},
    "language-source-study": {"library"},
    "forge-cell-validate": {"policy", "bundle", "record"},
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


def _json_depth(raw: bytes) -> int:
    """Return structural JSON nesting depth without building the value."""
    depth = 0
    maximum = 0
    in_string = False
    escaped = False
    for byte in raw:
        char = chr(byte)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            maximum = max(maximum, depth)
        elif char in "]}":
            depth -= 1
    return maximum


def load_json_bytes(raw: bytes, *, label: str = "JSON document") -> dict[str, Any]:
    if len(raw) > MAX_PROTOCOL_DOCUMENT_BYTES:
        raise ProtocolError(
            f"{label} exceeds protocol JSON limit of {MAX_PROTOCOL_DOCUMENT_BYTES} bytes"
        )
    if _json_depth(raw) > MAX_JSON_DEPTH:
        raise ProtocolError(f"{label} exceeds protocol JSON depth limit of {MAX_JSON_DEPTH}")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON constant {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ProtocolError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"JSON document must be an object: {label}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProtocolError(f"cannot read JSON document {path}: {exc}") from exc
    return load_json_bytes(raw, label=str(path))


def ensure_clean_directory(path: Path, *, label: str = "output directory") -> None:
    """Require a new directory boundary, including no hidden/nested entries."""
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise ProtocolError(f"{label} must be a real directory: {path}")
        try:
            next(path.iterdir())
        except StopIteration:
            return
        except OSError as exc:
            raise ProtocolError(f"cannot inspect {label}: {exc}") from exc
        raise ProtocolError(f"{label} must be empty for a new run: {path}")


def _walk_transport_files(root: Path) -> dict[str, int]:
    """Enumerate a transport tree without following links or special files."""
    if root.is_symlink() or not root.is_dir():
        raise ProtocolError(f"producer transport root must be a real directory: {root}")
    found: dict[str, int] = {}
    total_bytes = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ProtocolError(f"cannot inspect producer transport {directory}: {exc}") from exc
        for entry in entries:
            if entry.is_symlink():
                raise ProtocolError(f"symlink is not permitted in producer transport: {entry.path}")
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                stack.append(Path(entry.path))
                continue
            if not stat.S_ISREG(mode):
                raise ProtocolError(f"special file is not permitted in producer transport: {entry.path}")
            relative = Path(entry.path).relative_to(root).as_posix()
            _require_path(relative, "transport path")
            size = entry.stat(follow_symlinks=False).st_size
            if size > MAX_ARTIFACT_BYTES:
                raise ProtocolError(
                    f"transport artifact exceeds {MAX_ARTIFACT_BYTES} bytes: {relative}"
                )
            found[relative] = size
            total_bytes += size
            if total_bytes > MAX_TRANSPORT_BYTES:
                raise ProtocolError(f"producer transport exceeds {MAX_TRANSPORT_BYTES} bytes")
    if len(found) > MAX_TRANSPORT_FILES:
        raise ProtocolError(f"producer transport contains more than {MAX_TRANSPORT_FILES} files")
    return found


def validate_transport_tree(root: Path, declared_paths: Iterable[str]) -> None:
    """Require exact file membership for one producer artifact."""
    actual = _walk_transport_files(root)
    declared = list(declared_paths)
    _validate_unique_paths(declared, "declared producer files")
    expected = set(declared) | {"producer-execution.json"}
    actual_paths = set(actual)
    if actual_paths != expected:
        missing = sorted(expected - actual_paths)
        extra = sorted(actual_paths - expected)
        raise ProtocolError(
            f"producer transport membership mismatch: missing={missing} extra={extra}"
        )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = (
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"cannot serialize bounded JSON document: {exc}") from exc
    if len(raw) > MAX_PROTOCOL_DOCUMENT_BYTES:
        raise ProtocolError(
            f"JSON output exceeds protocol limit of {MAX_PROTOCOL_DOCUMENT_BYTES} bytes"
        )
    if _json_depth(raw) > MAX_JSON_DEPTH:
        raise ProtocolError(f"JSON output exceeds protocol depth limit of {MAX_JSON_DEPTH}")
    path.write_bytes(raw)


def document_digest(path: Path) -> str:
    try:
        return sha256_hex(path.read_bytes())
    except OSError as exc:
        raise ProtocolError(f"cannot hash protocol document {path}: {exc}") from exc


def _require_text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_LENGTH
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise ProtocolError(f"{label} must be non-empty bounded text")
    return value


def _require_path(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_PATH_LENGTH
        or not is_safe_relative_path(value)
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ProtocolError(f"{label} must be a safe relative path")
    return value


def _path_identity(value: str) -> str:
    """Use a portable identity to reject case/Unicode aliasing."""
    return unicodedata.normalize("NFC", value).casefold()


def _validate_unique_paths(paths: Iterable[str], label: str) -> None:
    exact: set[str] = set()
    portable: dict[str, str] = {}
    for path in paths:
        if path in exact:
            raise ProtocolError(f"duplicate {label} path: {path}")
        exact.add(path)
        identity = _path_identity(path)
        prior = portable.get(identity)
        if prior is not None and prior != path:
            raise ProtocolError(f"ambiguous {label} paths: {prior} and {path}")
        portable[identity] = path


def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ProtocolError(f"{label} must be a bounded identifier")
    return value


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise ProtocolError(f"{label} must be a lowercase 40-character SHA")
    return value


def _require_hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64_RE.fullmatch(value):
        raise ProtocolError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _check_output(output: Any, label: str) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ProtocolError(f"{label} must be an object")
    check_id = _require_identifier(output.get("check_id"), f"{label}.check_id")
    provider = _require_text(output.get("provider"), f"{label}.provider")
    contract_revision = _require_text(
        output.get("contract_revision"), f"{label}.contract_revision"
    )
    roles = output.get("roles")
    if not isinstance(roles, dict):
        raise ProtocolError(f"{label}.roles must be an object")
    normalized_roles: dict[str, str] = {}
    for field in ROLE_FIELDS:
        normalized_roles[field] = _require_text(roles.get(field), f"{label}.roles.{field}")
    if normalized_roles["evidence_provider"] != provider:
        raise ProtocolError(f"{label}.roles.evidence_provider must match provider")
    if normalized_roles["transport_authority"] != ROLE_TRANSPORT_AUTHORITY:
        raise ProtocolError(
            f"{label}.roles.transport_authority must be {ROLE_TRANSPORT_AUTHORITY}"
        )
    return {
        "check_id": check_id,
        "provider": provider,
        "contract_revision": contract_revision,
        "roles": normalized_roles,
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
        producer = _require_identifier(raw.get("producer"), f"{label}.producer")
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
        _validate_unique_paths(normalized_paths, f"{label}.artifact_paths")
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
        if len(set(capabilities)) != len(capabilities):
            raise ProtocolError(f"{label}.required_capabilities contain duplicates")
        execution = raw.get("execution")
        if not isinstance(execution, dict) or execution.get("mode") != "owner-native":
            raise ProtocolError(f"{label}.execution must declare owner-native mode")
        operation = execution.get("operation")
        if operation not in OPERATIONS:
            raise ProtocolError(f"{label}.execution.operation is not allowlisted")
        if adapter_id != OPERATION_ADAPTERS[operation]:
            raise ProtocolError(f"{label}.adapter_id does not match execution operation")
        input_paths = execution.get("input_paths")
        if not isinstance(input_paths, dict):
            raise ProtocolError(f"{label}.execution.input_paths must be an object")
        if set(input_paths) != OPERATION_INPUTS[operation]:
            raise ProtocolError(
                f"{label}.execution.input_paths must contain exactly "
                f"{sorted(OPERATION_INPUTS[operation])}"
            )
        for key, path in input_paths.items():
            _require_text(key, f"{label}.execution.input_paths key")
            _require_path(path, f"{label}.execution.input_paths.{key}")
        if "cases" in execution:
            if operation != "language-source-study":
                raise ProtocolError(f"{label}.execution.cases is only valid for language-source-study")
            cases = execution["cases"]
            if not isinstance(cases, list) or not cases:
                raise ProtocolError(f"{label}.execution.cases must be a non-empty array")
            case_names: set[str] = set()
            case_check_ids: set[str] = set()
            for case_index, case in enumerate(cases):
                case_label = f"{label}.execution.cases[{case_index}]"
                if not isinstance(case, dict):
                    raise ProtocolError(f"{case_label} must be an object")
                name = _require_identifier(case.get("name"), f"{case_label}.name")
                if name in case_names:
                    raise ProtocolError(f"duplicate descriptor case: {name}")
                case_names.add(name)
                _require_path(case.get("source_path"), f"{case_label}.source_path")
                case_check_id = _require_identifier(case.get("check_id"), f"{case_label}.check_id")
                if case_check_id in case_check_ids or case_check_id in check_ids:
                    raise ProtocolError(f"duplicate declared check id: {case_check_id}")
                case_check_ids.add(case_check_id)
        elif operation == "language-source-study":
            raise ProtocolError(f"{label}.execution.cases is required for language-source-study")
        if "expected_nonce" in execution:
            if operation != "forge-cell-validate":
                raise ProtocolError(f"{label}.execution.expected_nonce is only valid for forge-cell-validate")
            nonce = execution["expected_nonce"]
            if not isinstance(nonce, str) or not nonce or len(nonce) > 128 or any(
                ord(char) < 0x20 for char in nonce
            ):
                raise ProtocolError(f"{label}.execution.expected_nonce is invalid")
        elif operation == "forge-cell-validate":
            raise ProtocolError(f"{label}.execution.expected_nonce is required for forge-cell-validate")
        provenance_context = raw.get("provenance_context")
        if provenance_context is not None:
            if not isinstance(provenance_context, dict):
                raise ProtocolError(f"{label}.provenance_context must be an object")
            for field in ("kind", "authority", "input_key", "transport_name"):
                _require_text(provenance_context.get(field), f"{label}.provenance_context.{field}")
            if provenance_context["input_key"] not in input_paths:
                raise ProtocolError(f"{label}.provenance_context.input_key is not an execution input")
            _require_path(provenance_context["transport_name"], f"{label}.provenance_context.transport_name")
            if provenance_context["transport_name"].startswith("/"):
                raise ProtocolError(f"{label}.provenance_context.transport_name must be relative")
            status = provenance_context.get("authority_status", "UNKNOWN")
            if status not in {"PASS", "FAIL", "UNKNOWN"}:
                raise ProtocolError(f"{label}.provenance_context.authority_status is invalid")
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
        if operation == "language-source-study":
            case_ids = {case["check_id"] for case in execution["cases"]}
            output_ids = {item["check_id"] for item in normalized_outputs}
            if case_ids != output_ids:
                raise ProtocolError(f"{label}.execution.cases check membership does not match outputs")
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
    expected_contract_digest: str,
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
    if document.get("contract_digest") != expected_contract_digest:
        raise ProtocolError(f"producer output contract digest mismatch for {descriptor['producer']}")
    files = document.get("files")
    if not isinstance(files, list) or not files:
        raise ProtocolError("producer output files must be a non-empty array")
    if len(files) > MAX_TRANSPORT_FILES:
        raise ProtocolError(f"producer output contains more than {MAX_TRANSPORT_FILES} files")
    seen_paths: set[str] = set()
    normalized_paths: list[str] = []
    file_kinds: dict[str, str] = {}
    total_declared_bytes = 0
    for index, item in enumerate(files):
        label = f"producer output files[{index}]"
        if not isinstance(item, dict):
            raise ProtocolError(f"{label} must be an object")
        path = _require_path(item.get("path"), f"{label}.path")
        if path == "producer-execution.json":
            raise ProtocolError("producer envelope control file cannot be declared as a transport artifact")
        digest = _require_hex(item.get("sha256"), f"{label}.sha256")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_ARTIFACT_BYTES:
            raise ProtocolError(f"{label}.size must be between 0 and {MAX_ARTIFACT_BYTES}")
        kind = _require_text(item.get("kind"), f"{label}.kind")
        if path in seen_paths:
            raise ProtocolError(f"duplicate producer output path: {path}")
        seen_paths.add(path)
        normalized_paths.append(path)
        file_kinds[path] = kind
        if kind not in {"native", "check-result"}:
            raise ProtocolError(f"unsupported producer output file kind: {kind}")
        if kind == "check-result" and not path.startswith("checks/"):
            raise ProtocolError(f"check-result files must be under checks/: {path}")
        if kind == "native" and path.startswith("checks/"):
            raise ProtocolError(f"native files cannot be under checks/: {path}")
        total_declared_bytes += size
        item["path"] = path
        item["sha256"] = digest
        item["size"] = size
    _validate_unique_paths(normalized_paths, "producer output")
    if total_declared_bytes > MAX_TRANSPORT_BYTES:
        raise ProtocolError(f"declared producer transport exceeds {MAX_TRANSPORT_BYTES} bytes")
    expected_ids = set(descriptor_outputs(descriptor))
    outputs = document.get("check_results")
    if not isinstance(outputs, list) or not outputs:
        raise ProtocolError("producer output check_results must be a non-empty array")
    if len(outputs) > MAX_TRANSPORT_FILES:
        raise ProtocolError(f"producer output contains more than {MAX_TRANSPORT_FILES} checks")
    seen_ids: set[str] = set()
    seen_check_paths: set[str] = set()
    check_path_identities: dict[str, str] = {}
    for index, output in enumerate(outputs):
        label = f"producer output check_results[{index}]"
        if not isinstance(output, dict):
            raise ProtocolError(f"{label} must be an object")
        check_id = _require_identifier(output.get("id"), f"{label}.id")
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
        if path in seen_check_paths:
            raise ProtocolError(f"multiple producer checks claim one file: {path}")
        seen_check_paths.add(path)
        path_identity = _path_identity(path)
        prior_id = check_path_identities.get(path_identity)
        if prior_id is not None and prior_id != check_id:
            raise ProtocolError(f"ambiguous producer check path identity: {path}")
        check_path_identities[path_identity] = check_id
        check_digest = _require_hex(output.get("sha256"), f"{label}.sha256")
        if check_digest != next(
            item["sha256"] for item in files if item["path"] == path
        ):
            raise ProtocolError(f"producer check digest does not match its file entry: {path}")
    if seen_ids != expected_ids:
        raise ProtocolError(
            f"producer check membership mismatch for {descriptor['producer']}: "
            f"missing={sorted(expected_ids - seen_ids)} extra={sorted(seen_ids - expected_ids)}"
        )
    declared_check_paths = {
        path for path, kind in file_kinds.items() if kind == "check-result"
    }
    if declared_check_paths != seen_check_paths:
        raise ProtocolError(
            "producer check-result file membership mismatch: "
            f"unclaimed={sorted(declared_check_paths - seen_check_paths)} "
            f"missing={sorted(seen_check_paths - declared_check_paths)}"
        )
    bindings = document.get("provenance_bindings", [])
    if not isinstance(bindings, list) or len(bindings) > MAX_BINDINGS:
        raise ProtocolError("provenance_bindings must be a bounded array")
    binding_paths: list[str] = []
    for index, binding in enumerate(bindings):
        label = f"provenance_bindings[{index}]"
        if not isinstance(binding, dict):
            raise ProtocolError(f"{label} must be an object")
        _require_text(binding.get("kind"), f"{label}.kind")
        _require_text(binding.get("authority"), f"{label}.authority")
        binding_revision = _require_sha(binding.get("revision"), f"{label}.revision")
        if binding_revision != document.get("revision"):
            raise ProtocolError(f"{label}.revision does not match producer revision")
        path = _require_path(binding.get("path"), f"{label}.path")
        digest = _require_hex(binding.get("sha256"), f"{label}.sha256")
        if binding.get("authority_status") not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ProtocolError(f"{label}.authority_status must be PASS, FAIL, or UNKNOWN")
        if path not in seen_paths or file_kinds[path] != "native":
            raise ProtocolError(f"{label}.path must identify a declared native file")
        file_digest = next(item["sha256"] for item in files if item["path"] == path)
        if digest != file_digest:
            raise ProtocolError(f"{label}.sha256 does not match its file entry")
        binding_paths.append(path)
    _validate_unique_paths(binding_paths, "provenance binding")
    provenance_context = descriptor.get("provenance_context")
    if provenance_context is not None:
        expected_binding_path = "native/" + provenance_context["transport_name"]
        matching = [
            binding for binding in bindings
            if binding.get("kind") == provenance_context["kind"]
            and binding.get("authority") == provenance_context["authority"]
            and binding.get("path") == expected_binding_path
        ]
        if len(matching) != 1:
            raise ProtocolError(
                f"producer provenance binding is required at {expected_binding_path}"
            )
        if matching[0]["authority_status"] != provenance_context.get("authority_status", "UNKNOWN"):
            raise ProtocolError("producer provenance authority status does not match descriptor")
    return [
        {"id": item["id"], "path": item["path"], "sha256": item["sha256"]}
        for item in outputs
    ]


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
            if item.get("evidence_provider") != expected["roles"]["evidence_provider"]:
                errors.append(f"{label}.evidence_provider mismatch")
            for role in ROLE_FIELDS[1:]:
                if item.get(role) != expected["roles"][role]:
                    errors.append(f"{label}.{role} mismatch")
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
    provenance_bindings = evidence.get("provenance_bindings", [])
    if not isinstance(provenance_bindings, list) or len(provenance_bindings) > MAX_BINDINGS:
        errors.append("provenance_bindings must be a bounded array")
    else:
        binding_keys: set[tuple[str, str]] = set()
        for index, binding in enumerate(provenance_bindings):
            label = f"provenance_bindings[{index}]"
            if not isinstance(binding, dict):
                errors.append(f"{label} must be an object")
                continue
            for field in ("producer", "kind", "authority", "path", "sha256", "authority_status", "revision"):
                if not isinstance(binding.get(field), str) or not binding[field]:
                    errors.append(f"{label}.{field} is required")
            if binding.get("producer") not in expected_revisions:
                errors.append(f"{label}.producer is not a family producer")
            elif binding.get("revision") != expected_revisions[binding["producer"]]["revision"]:
                errors.append(f"{label}.revision does not match its producer")
            if isinstance(binding.get("path"), str) and not is_safe_relative_path(binding["path"]):
                errors.append(f"{label}.path is unsafe")
            if isinstance(binding.get("sha256"), str) and not HEX64_RE.fullmatch(binding["sha256"]):
                errors.append(f"{label}.sha256 is invalid")
            if binding.get("authority_status") not in {"PASS", "FAIL", "UNKNOWN"}:
                errors.append(f"{label}.authority_status is invalid")
            key = (str(binding.get("producer")), str(binding.get("path")))
            if key in binding_keys:
                errors.append(f"duplicate provenance binding: {key[0]} {key[1]}")
            binding_keys.add(key)
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
        if execution.get("candidate_isolation") is not (expected_mode == "moving-head"):
            errors.append("execution.candidate_isolation must match fixed or moving-head mode")
        if execution.get("candidate_isolation") is not (expected_mode == "moving-head"):
            errors.append("execution.candidate_isolation must match fixed or moving-head mode")
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
                "category", "claim", "evidence_provider", "semantic_authority",
                "remediation_owner", "transport_authority", "originating_project",
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
                for role in ROLE_FIELDS:
                    if obligation.get(role) != check.get(role):
                        errors.append(f"unresolved_obligations[{index}].{role} does not match its check")
            if obligation.get("owner") != obligation.get("remediation_owner"):
                errors.append(f"unresolved_obligations[{index}].owner must be the remediation_owner alias")
            lifecycle = obligation.get("lifecycle")
            if not isinstance(lifecycle, dict):
                errors.append(f"unresolved_obligations[{index}].lifecycle is required")
            else:
                for field in (
                    "state", "reproduction", "resolution_status", "semantic_resolution",
                    "promotion_status", "correlation_key",
                ):
                    if not isinstance(lifecycle.get(field), str) or not lifecycle[field]:
                        errors.append(f"unresolved_obligations[{index}].lifecycle.{field} is required")
                if lifecycle.get("state") != "OPEN":
                    errors.append(f"unresolved_obligations[{index}].lifecycle.state must be OPEN")
                if lifecycle.get("resolution_status") != "NOT_ESTABLISHED":
                    errors.append(f"unresolved_obligations[{index}] cannot claim semantic resolution")
    not_reproduced = evidence.get("not_reproduced", [])
    if not isinstance(not_reproduced, list):
        errors.append("not_reproduced must be an array")
    else:
        for index, item in enumerate(not_reproduced):
            label = f"not_reproduced[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{label} must be an object")
                continue
            if item.get("current_status") != "NOT_REPRODUCED":
                errors.append(f"{label}.current_status must be NOT_REPRODUCED")
            lifecycle = item.get("lifecycle")
            if not isinstance(lifecycle, dict):
                errors.append(f"{label}.lifecycle is required")
            elif lifecycle.get("resolution_status") != "NOT_ESTABLISHED":
                errors.append(f"{label} cannot claim semantic resolution")
    observation = evidence.get("observation")
    if not isinstance(observation, dict):
        errors.append("observation lifecycle metadata is required")
    else:
        if not isinstance(observation.get("observation_id"), str) or not observation["observation_id"].startswith("sha256:"):
            errors.append("observation.observation_id is invalid")
        if observation.get("resolution_status") != "NOT_ESTABLISHED":
            errors.append("observation cannot claim semantic resolution")
    return errors


def canonical_digest(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))
