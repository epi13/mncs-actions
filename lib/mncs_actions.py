"""MNCS Actions canonical contract library.

This is the single canonical validation implementation for the
machine-readable contracts published under ``schemas/``.

The JSON Schema files are the published contract; this module is the
executable enforcement.  ``tests/test_schema_agreement.py`` mechanically
checks that the two agree on versions, verdicts, required fields, and
naming so the runtime never quietly accepts what the schema rejects (or
vice versa).

Only the Python standard library is used so GitHub-hosted runners can
execute the actions without extra dependencies.

Verdict semantics (never collapse uncertainty into success):

- ``PASS``: required checks established the claimed condition.
- ``FAIL``: a required check established a negative result.
- ``UNKNOWN``: evidence was insufficient to establish PASS or FAIL under
  a *valid* contract.
- ``INVALID`` (execution level only, never a claim verdict): no valid
  verification claim was established because the result was missing,
  malformed, or structurally invalid.  A valid execution receipt is still
  produced; a verification claim is NOT ESTABLISHED.  ``INVALID`` must
  never be reinterpreted as ``UNKNOWN``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERDICTS = ("PASS", "FAIL", "UNKNOWN")
CLAIM_ESTABLISHED = "ESTABLISHED"
CLAIM_NOT_ESTABLISHED = "NOT_ESTABLISHED"

VERIFICATION_RESULT_SCHEMA_VERSION = "mncs.verification-result/1"
EVIDENCE_MANIFEST_SCHEMA_VERSION = "mncs.evidence-manifest/1"
EXECUTION_RECEIPT_SCHEMA_VERSION = "mncs.execution-receipt/1"
CHECK_RESULT_SCHEMA_VERSION = "mncs.check-result/1"
AGGREGATE_RESULT_SCHEMA_VERSION = "mncs.aggregate-result/1"

# Correct digest output name.  ``provenance-digest`` is retained as a
# deprecated compatibility alias because it historically hashed the entire
# canonical manifest, not only the provenance block.
MANIFEST_DIGEST_OUTPUT = "manifest-digest"
PROVENANCE_DIGEST_OUTPUT_COMPAT = "provenance-digest"

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_DIGEST = re.compile(r"^(sha256:)?[a-f0-9]{64}$")


def canonical_bytes(value: Any) -> bytes:
    """RFC8785-style canonical JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def github_provenance(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    src = env if env is not None else os.environ
    return {
        "repository": src.get("GITHUB_REPOSITORY", ""),
        "ref": src.get("GITHUB_REF", ""),
        "commit": src.get("GITHUB_SHA", ""),
        "workflow": src.get("GITHUB_WORKFLOW", ""),
        "run_id": src.get("GITHUB_RUN_ID", ""),
        "actor": src.get("GITHUB_ACTOR", ""),
        "event": src.get("GITHUB_EVENT_NAME", ""),
        "runner": src.get("RUNNER_NAME", ""),
    }


def is_safe_relative_path(value: str) -> bool:
    """Reject absolute paths, traversal, backslashes, empty, NUL/control."""
    if not isinstance(value, str) or not value:
        return False
    if "\x00" in value:
        return False
    if value.startswith("/") or value.startswith("\\"):
        return False
    # Windows drive, e.g. C: / C:\
    if re.match(r"^[A-Za-z]:", value):
        return False
    if "\\" in value:
        return False
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            # Allow trailing slash? No: evidence paths are files.
            # Empty segment means // or leading/trailing slash.
            return False
    # Control characters
    for ch in value:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return False
    return True


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def validate_verification_result(obj: Any) -> List[str]:
    """Validate a verification-result/1 document.

    Extra/unknown fields are permitted and ignored (forward compatible),
    matching ``schemas/verification-result.schema.json`` which sets
    ``additionalProperties: true``.  Unknown fields never change the
    verdict.
    """
    errors: List[str] = []
    if not isinstance(obj, dict):
        return ["verification result must be a JSON object"]
    if obj.get("schema_version") != VERIFICATION_RESULT_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {VERIFICATION_RESULT_SCHEMA_VERSION}"
        )
    verdict = obj.get("verdict")
    if verdict not in VERDICTS:
        errors.append("verdict must be PASS, FAIL, or UNKNOWN")
    summary = obj.get("summary")
    if summary is not None and not isinstance(summary, str):
        errors.append("summary must be a string when present")
    metadata = obj.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        errors.append("metadata must be an object when present")
    checks = obj.get("checks")
    if not isinstance(checks, list):
        errors.append("checks must be an array")
        return errors
    for index, check in enumerate(checks):
        prefix = f"checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{prefix} must be an object")
            continue
        check_id = check.get("id")
        if not isinstance(check_id, str) or not check_id:
            errors.append(f"{prefix}.id must be a non-empty string")
        if check.get("verdict") not in VERDICTS:
            errors.append(
                f"{prefix}.verdict must be PASS, FAIL, or UNKNOWN"
            )
        check_summary = check.get("summary")
        if check_summary is not None and not isinstance(check_summary, str):
            errors.append(f"{prefix}.summary must be a string when present")
        evidence = check.get("evidence")
        if evidence is not None:
            if not isinstance(evidence, list) or not all(
                isinstance(item, str) for item in evidence
            ):
                errors.append(f"{prefix}.evidence must be an array of strings")
    # Semantic contradiction rule: documented in schema description and
    # enforced here (JSON Schema cannot express cross-field dominance).
    if obj.get("verdict") == "PASS" and isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict) and check.get("verdict") == "FAIL":
                errors.append(
                    "top-level PASS cannot contain a failing check"
                )
                break
    return errors


def validate_evidence_reference(obj: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(obj, dict):
        return ["reference must be an object"]
    kind = obj.get("kind")
    if not isinstance(kind, str) or not kind:
        errors.append("reference.kind must be a non-empty string")
    for field in (
        "producer",
        "contract_revision",
        "schema_revision",
        "producer_revision",
        "uri",
        "digest",
    ):
        if obj.get(field) is not None and not isinstance(obj[field], str):
            errors.append(f"reference.{field} must be a string when present")
    path = obj.get("path")
    if path is not None:
        if not isinstance(path, str) or not is_safe_relative_path(path):
            errors.append(
                "reference.path must be a safe relative path when present"
            )
    digest = obj.get("digest")
    if digest is not None and not _DIGEST.match(digest):
        errors.append(
            "reference.digest must be hex64 or sha256:hex64 when present"
        )
    return errors


def validate_check_result(obj: Any) -> List[str]:
    """Validate a check-result/1 document (composable provider contract)."""
    errors: List[str] = []
    if not isinstance(obj, dict):
        return ["check result must be a JSON object"]
    if obj.get("schema_version") != CHECK_RESULT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {CHECK_RESULT_SCHEMA_VERSION}")
    check_id = obj.get("id")
    if not isinstance(check_id, str) or not check_id:
        errors.append("id must be a non-empty string")
    provider = obj.get("provider")
    if not isinstance(provider, str) or not provider:
        errors.append("provider must be a non-empty string")
    if obj.get("verdict") not in VERDICTS:
        errors.append("verdict must be PASS, FAIL, or UNKNOWN")
    for field in (
        "scope",
        "claim",
        "summary",
        "contract_revision",
        "producer_revision",
        "digest",
    ):
        if obj.get(field) is not None and not isinstance(obj[field], str):
            errors.append(f"{field} must be a string when present")
    unresolved = obj.get("unresolved")
    if unresolved is not None:
        if not isinstance(unresolved, list) or not all(
            isinstance(item, str) and item for item in unresolved
        ):
            errors.append("unresolved must be an array of non-empty strings")
    references = obj.get("references")
    if references is not None:
        if not isinstance(references, list):
            errors.append("references must be an array when present")
        else:
            for index, ref in enumerate(references):
                for err in validate_evidence_reference(ref):
                    errors.append(f"references[{index}].{err}")
    # Backwards-compatible alias: evidence_refs was an early draft name.
    evidence_refs = obj.get("evidence_refs")
    if evidence_refs is not None:
        if not isinstance(evidence_refs, list):
            errors.append("evidence_refs must be an array when present")
        else:
            for index, ref in enumerate(evidence_refs):
                if isinstance(ref, str):
                    continue
                for err in validate_evidence_reference(ref):
                    errors.append(f"evidence_refs[{index}].{err}")
    return errors


def validate_execution_receipt(obj: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(obj, dict):
        return ["execution receipt must be a JSON object"]
    if obj.get("schema_version") != EXECUTION_RECEIPT_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {EXECUTION_RECEIPT_SCHEMA_VERSION}"
        )
    if obj.get("kind") != "execution-receipt":
        errors.append('kind must be "execution-receipt"')
    exit_code = obj.get("command_exit_code")
    if not isinstance(exit_code, int) or exit_code < 0:
        errors.append("command_exit_code must be a non-negative integer")
    if obj.get("claim_status") not in (
        CLAIM_ESTABLISHED,
        CLAIM_NOT_ESTABLISHED,
    ):
        errors.append("claim_status must be ESTABLISHED or NOT_ESTABLISHED")
    provenance = obj.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    else:
        for field in (
            "repository",
            "ref",
            "commit",
            "workflow",
            "run_id",
            "actor",
            "event",
            "runner",
        ):
            if field in provenance and not isinstance(provenance[field], str):
                errors.append(f"provenance.{field} must be a string")
    generated = obj.get("generated_at")
    if not isinstance(generated, str) or not generated:
        errors.append("generated_at must be a non-empty string")
    produced = obj.get("produced_files")
    if produced is not None:
        if not isinstance(produced, list):
            errors.append("produced_files must be an array when present")
        else:
            for index, entry in enumerate(produced):
                if not isinstance(entry, dict):
                    errors.append(f"produced_files[{index}] must be an object")
                    continue
                if not isinstance(entry.get("path"), str):
                    errors.append(
                        f"produced_files[{index}].path must be a string"
                    )
                if "sha256" in entry and (
                    not isinstance(entry["sha256"], str)
                    or not _HEX64.match(entry["sha256"])
                ):
                    errors.append(
                        f"produced_files[{index}].sha256 must be hex64"
                    )
    return errors


def validate_aggregate_result(obj: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(obj, dict):
        return ["aggregate result must be a JSON object"]
    if obj.get("schema_version") != AGGREGATE_RESULT_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {AGGREGATE_RESULT_SCHEMA_VERSION}"
        )
    if obj.get("verdict") not in VERDICTS:
        errors.append("verdict must be PASS, FAIL, or UNKNOWN")
    required = obj.get("required")
    if not isinstance(required, list) or not all(
        isinstance(item, str) and item for item in required
    ):
        errors.append("required must be an array of non-empty strings")
    optional = obj.get("optional")
    if optional is not None and (
        not isinstance(optional, list)
        or not all(isinstance(item, str) and item for item in optional)
    ):
        errors.append("optional must be an array of non-empty strings")
    checks = obj.get("checks")
    if not isinstance(checks, list):
        errors.append("checks must be an array")
        return errors
    for index, check in enumerate(checks):
        prefix = f"checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(check.get("id"), str) or not check["id"]:
            errors.append(f"{prefix}.id must be a non-empty string")
        if check.get("verdict") not in VERDICTS:
            errors.append(f"{prefix}.verdict must be PASS, FAIL, or UNKNOWN")
        if "required" in check and not isinstance(check["required"], bool):
            errors.append(f"{prefix}.required must be a boolean")
    unresolved = obj.get("unresolved")
    if unresolved is not None and (
        not isinstance(unresolved, list)
        or not all(isinstance(item, str) and item for item in unresolved)
    ):
        errors.append("unresolved must be an array of non-empty strings")
    return errors


def load_result_file(path: Path) -> Tuple[Any, Optional[str], List[str]]:
    """Load a result file, distinguishing missing/malformed/parsed.

    Returns (parsed_or_None, raw_sha256_or_None, errors).  ``errors`` is
    non-empty when no valid JSON object could be established.
    """
    if not path.is_file():
        return None, None, [f"verification result does not exist: {path}"]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, None, [f"verification result is not readable: {exc}"]
    digest = sha256_hex(raw)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, digest, [
            f"verification result is not valid UTF-8 JSON: {exc}"
        ]
    return parsed, digest, []


def aggregate_verdict(
    check_verdicts: Dict[str, str],
    required: List[str],
    optional: Optional[List[str]] = None,
) -> Tuple[str, List[str]]:
    """Compose an aggregate verdict with dominance FAIL > UNKNOWN > PASS.

    - Any required FAIL -> aggregate FAIL.
    - Else any required UNKNOWN (or missing required) -> aggregate UNKNOWN.
    - Else aggregate PASS.
    - Optional UNKNOWN never changes the aggregate verdict but is always
      reported in ``unresolved`` so component uncertainty stays visible.

    Returns (verdict, unresolved).
    """
    optional = optional or []
    unresolved: List[str] = []
    # Missing required coverage is UNKNOWN (insufficient evidence), never PASS.
    for check_id in required:
        verdict = check_verdicts.get(check_id)
        if verdict is None:
            unresolved.append(f"{check_id}: required check missing")
        elif verdict == "FAIL":
            pass  # handled by dominance below
        elif verdict == "UNKNOWN":
            unresolved.append(f"{check_id}: required check UNKNOWN")
    for check_id in optional:
        if check_verdicts.get(check_id) == "UNKNOWN":
            unresolved.append(f"{check_id}: optional check UNKNOWN")
    required_verdicts = [
        check_verdicts.get(check_id) for check_id in required
    ]
    if any(v == "FAIL" for v in required_verdicts):
        verdict = "FAIL"
    elif any(v == "UNKNOWN" or v is None for v in required_verdicts):
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"
    # Surface optional FAILs as unresolved visibility without changing a
    # PASS boundary decided by required checks?  No: optional FAIL must not
    # be hidden either.  Record it, but the declared boundary verdict stands.
    for check_id in optional:
        if check_verdicts.get(check_id) == "FAIL":
            unresolved.append(f"{check_id}: optional check FAIL")
    return verdict, unresolved


def build_execution_receipt(
    *,
    command: str,
    command_exit_code: int,
    claim_status: str,
    result_path: str = "",
    result_present: bool = False,
    result_valid: bool = False,
    result_errors: Optional[List[str]] = None,
    claim_verdict: str = "",
    produced_files: Optional[List[Dict[str, str]]] = None,
    inputs: Optional[Dict[str, str]] = None,
    error_code: str = "",
    provenance: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    receipt: Dict[str, Any] = {
        "schema_version": EXECUTION_RECEIPT_SCHEMA_VERSION,
        "kind": "execution-receipt",
        "command": command,
        "command_exit_code": command_exit_code,
        "claim_status": claim_status,
        "result": {
            "present": result_present,
            "path": result_path,
            "valid": result_valid,
        },
        "provenance": provenance or github_provenance(),
        "produced_files": produced_files or [],
        "generated_at": utc_now_z(),
    }
    if result_errors:
        result_block = receipt["result"]
        assert isinstance(result_block, dict)
        result_block["errors"] = list(result_errors)
    if claim_status == CLAIM_ESTABLISHED:
        receipt["claim"] = {"verdict": claim_verdict}
    else:
        claim_block: Dict[str, Any] = {
            "verdict": "NONE",
            "error_code": error_code or "RESULT_NOT_ESTABLISHED",
        }
        if result_errors:
            claim_block["error_details"] = list(result_errors)
        receipt["claim"] = claim_block
    if inputs:
        receipt["inputs"] = dict(inputs)
    return receipt


def build_evidence_manifest(
    *,
    verdict: str,
    result_sha256: str,
    result_filename: str = "verification-result.json",
    command_exit_code: int = 0,
    kind: str = "verification",
    receipt_ref: Optional[Dict[str, str]] = None,
    references: Optional[List[Dict[str, Any]]] = None,
    provenance: Optional[Dict[str, str]] = None,
    unresolved: Optional[List[str]] = None,
    boundary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "schema_version": EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "kind": kind,
        "verdict": verdict,
        "result": {"path": result_filename, "sha256": result_sha256},
        "provenance": provenance or github_provenance(),
        "command_exit_code": command_exit_code,
        "generated_at": utc_now_z(),
        "claim_status": CLAIM_ESTABLISHED,
    }
    if receipt_ref:
        manifest["receipt"] = dict(receipt_ref)
    if references:
        manifest["references"] = list(references)
    if unresolved:
        manifest["unresolved"] = list(unresolved)
    if boundary:
        manifest["boundary"] = dict(boundary)
    return manifest


# ---- Family adapter mappings (documented, no silent reinterpretation) ----
#
# Rights & provenance (mncs-rights-provenance) native outcomes:
#   pass, pass-with-findings, review-required, blocked, invalid (+ unknown
#   in lineage rights_summary).  Adapter semantics:
#   - pass -> PASS (requirements satisfied under the profile)
#   - blocked / invalid -> FAIL (negative established)
#   - pass-with-findings / review-required / unknown -> UNKNOWN
#     (evidence insufficient for PASS; human/policy review outstanding).
#   The native outcome, severity, and findings are preserved verbatim in
#   the check summary/unresolved so nothing is hidden.
RIGHTS_PASS = {"pass"}
RIGHTS_FAIL = {"blocked", "invalid"}
RIGHTS_UNKNOWN = {"pass-with-findings", "review-required", "unknown"}


def map_rights_outcome(outcome: str) -> str:
    normalized = (outcome or "").strip().lower()
    if normalized in RIGHTS_PASS:
        return "PASS"
    if normalized in RIGHTS_FAIL:
        return "FAIL"
    if normalized in RIGHTS_UNKNOWN:
        return "UNKNOWN"
    # Unrecognized vocabulary is not PASS.  Preserve the gap explicitly.
    return "UNKNOWN"


def map_validator_computed_status(
    *, valid: bool, computed_status: str = ""
) -> Tuple[str, List[str]]:
    """Map mncs-validator-rs ValidationReport to a check verdict.

    - valid=true + computed_status PASS/FAIL/UNKNOWN -> same verdict.
    - valid=false (issues established) -> FAIL (negative established).
    - Unknown computed_status with valid=true -> UNKNOWN (never PASS).
    """
    normalized = (computed_status or "").strip().upper()
    if valid:
        if normalized in VERDICTS:
            return normalized, []
        return "UNKNOWN", [
            f"validator reported valid with unrecognized status: {computed_status!r}"
        ]
    return "FAIL", []
