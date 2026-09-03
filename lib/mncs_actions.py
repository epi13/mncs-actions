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

BADGE_SCHEMA_VERSION = "mncs.badge/1"
BADGE_LABEL_DEFAULT = "MNCS verification"
BADGE_LABEL_MAX_LEN = 64
# Presentation states.  PASS/FAIL/UNKNOWN mirror established claim verdicts;
# INVALID is a projection-only state meaning no valid claim was established
# (it is never a claim verdict; see the module docstring).
BADGE_STATES = ("PASS", "FAIL", "UNKNOWN", "INVALID")
BADGE_INVALID = "INVALID"
# Conventional Shields-compatible message colors (hex without "#", as
# published by shields.io for these semantics).  Color is decorative only:
# the verdict word always carries the meaning.
BADGE_COLORS = {
    "PASS": "4c1",
    "FAIL": "e05d44",
    "UNKNOWN": "dfb317",
    "INVALID": "9f9f9f",
}

# Revision-binding record keys carried in receipt ``inputs`` and manifest
# ``boundary`` blocks.  These are transport annotations identifying which
# mncs-actions implementation composed the evidence and (when asserted by
# the caller) which workflow carrier revision invoked it.  They grant no
# policy authority and redefine no domain semantics.
IMPLEMENTATION_REVISION_KEY = "implementation_revision"
CARRIER_REVISION_KEY = "carrier_revision"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Opaque revision labels (subject commits, caller-asserted carriers) are
# carried verbatim: non-empty, no whitespace or control characters.
REVISION_TOKEN_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,128}$")

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_DIGEST = re.compile(r"^(sha256:)?[a-f0-9]{64}$")
_NATIVE_HEX64 = re.compile(r"^[A-Fa-f0-9]{64}$")
_REPOSITORY_ID = re.compile(
    r"^(?:https://)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$"
)
_DERIVATION_RELATIONS = {
    "derived-from",
    "transformed-by",
    "validated-by",
    "executed-by",
    "attested-by",
    "referenced",
    "member-of",
    "supersedes",
    "superseded-by",
    "resolves-gap",
    "gap-derived-from",
    "evaluated-by",
    "approved-by",
}


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
    ):
        if obj.get(field) is not None and not isinstance(obj[field], str):
            errors.append(f"{field} must be a string when present")
    # Optional content binding: when a provider binds its claim to exact
    # bytes, the digest must be a SHA-256 hex (bare or sha256:-prefixed).
    # A present-but-malformed digest (including explicit null) is rejected,
    # never silently accepted.
    if "digest" in obj:
        digest = obj["digest"]
        if not isinstance(digest, str) or not _DIGEST.match(digest):
            errors.append("digest must be hex64 or sha256:hex64 when present")
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
    """Validate an aggregate-result/1 document.

    Component bindings (``checks[].digest`` / ``checks[].path``) are
    optional but strict: ``digest`` must be SHA-256 hex (bare or
    ``sha256:``-prefixed) identifying the exact consumed check bytes, and
    ``path`` must be a safe relative path (no absolute paths, no ``..``
    traversal, no backslashes) locating those bytes under the declared
    working directory. Malformed bindings are rejected, never silently
    accepted. Unknown additive fields on checks[] and top level are
    permitted and ignored (forward compatible).
    """
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
        for field in ("provider", "scope"):
            if check.get(field) is not None and not isinstance(
                check[field], str
            ):
                errors.append(f"{prefix}.{field} must be a string when present")
        if "digest" in check:
            digest = check["digest"]
            if not isinstance(digest, str) or not _DIGEST.match(digest):
                errors.append(
                    f"{prefix}.digest must be hex64 or sha256:hex64 when present"
                )
        if "path" in check:
            path = check["path"]
            if not isinstance(path, str) or not is_safe_relative_path(path):
                errors.append(
                    f"{prefix}.path must be a safe relative path when present"
                )
    unresolved = obj.get("unresolved")
    if unresolved is not None and (
        not isinstance(unresolved, list)
        or not all(isinstance(item, str) and item for item in unresolved)
    ):
        errors.append("unresolved must be an array of non-empty strings")
    return errors


def validate_aggregate_declarations(
    required: List[str], optional: Optional[List[str]] = None
) -> List[str]:
    """Validate the caller-owned required/optional composition boundary.

    The declaration is deliberately not part of ``aggregate-result/1``'s
    required fields, so old result documents remain readable.  The action
    validates it before producing a new aggregate: duplicate ids and
    required/optional overlap are ambiguous policy, not UNKNOWN evidence.
    """
    optional = optional or []
    errors: List[str] = []
    for name, values in (("required", required), ("optional", optional)):
        seen: set[str] = set()
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{name}[{index}] must be a non-empty check id")
                continue
            if value in seen:
                errors.append(f"duplicate {name} check id: {value}")
            seen.add(value)
    overlap = sorted(set(required).intersection(optional))
    for check_id in overlap:
        errors.append(f"check id cannot be both required and optional: {check_id}")
    return errors


def _validate_native_digest(value: Any, label: str, errors: List[str]) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:") or not _NATIVE_HEX64.match(value[7:]):
        errors.append(f"{label} must be 'sha256:<64 hex>'")


def _validate_native_evidence_reference(
    value: Any, label: str, errors: List[str]
) -> None:
    """Validate the currently published mncs-rp lineage evidence reference.

    This is intentionally a small transport check, not a copy of the
    rights/provenance schema.  The owning repository remains authoritative;
    this only rejects malformed references before they enter an aggregate.
    """
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    for field in ("kind", "reference"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"{label}.{field} must be a non-empty string")
    if "sha256" in value and (
        not isinstance(value["sha256"], str) or not _NATIVE_HEX64.match(value["sha256"])
    ):
        errors.append(f"{label}.sha256 must be 64 hexadecimal characters")
    producer = value.get("producer_reference")
    if producer is not None:
        if not isinstance(producer, dict):
            errors.append(f"{label}.producer_reference must be an object")
        else:
            for field in ("producer", "recordKind", "schemaVersion", "stableId"):
                if not isinstance(producer.get(field), str) or not producer[field]:
                    errors.append(
                        f"{label}.producer_reference.{field} must be a non-empty string"
                    )
            if "contentDigest" in producer:
                _validate_native_digest(
                    producer["contentDigest"],
                    f"{label}.producer_reference.contentDigest",
                    errors,
                )
    path = value.get("path")
    if path is not None and (not isinstance(path, str) or not is_safe_relative_path(path)):
        errors.append(f"{label}.path must be a safe relative path")


def _iter_native_evidence_references(record: Dict[str, Any]):
    subject = record.get("subject")
    if isinstance(subject, dict) and "evidence_refs" in subject:
        yield "subject.evidence_refs", subject.get("evidence_refs")
    for field in ("derivations", "contributions", "evaluations", "approvals", "authority_claims"):
        entries = record.get(field)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            for evidence_field in ("evidence", "basis_evidence"):
                if evidence_field in entry:
                    yield f"{field}[{index}].{evidence_field}", entry[evidence_field]
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, dict) and "evidence" in lifecycle:
        yield "lifecycle.evidence", lifecycle["evidence"]


def classify_changeset_lineage(
    record: Any,
    *,
    expected_revisions: Optional[Dict[str, str]] = None,
    evidence_root: Optional[Path] = None,
) -> Tuple[Optional[str], List[str], List[str], Dict[str, Any]]:
    """Mechanically classify the published mncs-rp lineage/ChangeSet bridge.

    The v0.3 rights lineage record references ChangeSets owned by MNCDS and
    coordination records owned by Commons.  This function validates only
    identity, digest, revision, path, and relationship structure.  It never
    evaluates promotion, rights, or language semantics.

    Returns ``(verdict, unresolved, errors, summary)``.  ``verdict`` is
    ``None`` when no claim can be established (the adapter must emit no
    check-result); valid but incomplete records become ``UNKNOWN``.
    """
    errors: List[str] = []
    unresolved: List[str] = []
    summary: Dict[str, Any] = {}
    if not isinstance(record, dict):
        return None, [], ["lineage record must be a JSON object"], summary
    if record.get("schema_version") != "0.3.0":
        errors.append("unsupported lineage schema_version; expected 0.3.0")
    lineage_id = record.get("lineage_id")
    if not isinstance(lineage_id, str) or not lineage_id:
        errors.append("lineage_id must be a non-empty string")
    summary["lineage_id"] = lineage_id if isinstance(lineage_id, str) else ""

    subject = record.get("subject")
    if not isinstance(subject, dict):
        errors.append("subject must be an object")
    else:
        artifacts = subject.get("artifact_refs")
        if not isinstance(artifacts, list) or not artifacts:
            errors.append("subject.artifact_refs must be a non-empty array")
        else:
            for index, artifact in enumerate(artifacts):
                if not isinstance(artifact, dict) or not isinstance(artifact.get("id"), str) or not artifact["id"]:
                    errors.append(f"subject.artifact_refs[{index}].id must be non-empty")
                if isinstance(artifact, dict) and "path" in artifact:
                    path = artifact["path"]
                    if not isinstance(path, str) or not is_safe_relative_path(path):
                        errors.append(f"subject.artifact_refs[{index}].path must be safe")

    declared_digest = record.get("content_digest")
    _validate_native_digest(declared_digest, "content_digest", errors)
    if isinstance(declared_digest, str) and _NATIVE_HEX64.match(declared_digest.removeprefix("sha256:")):
        reduced = {key: value for key, value in record.items() if key != "content_digest"}
        expected_digest = "sha256:" + sha256_hex(canonical_bytes(reduced))
        summary["content_digest_expected"] = expected_digest
        summary["content_digest_matches"] = declared_digest == expected_digest
        if declared_digest != expected_digest:
            errors.append("content_digest does not match canonical content")

    changesets = record.get("changesets")
    if changesets is None:
        unresolved.append("changesets: membership is not declared")
        changesets = []
    elif not isinstance(changesets, list):
        errors.append("changesets must be an array")
        changesets = []
    changeset_ids: set[str] = set()
    seen_repositories: set[str] = set()
    participant_count = 0
    for index, changeset in enumerate(changesets):
        label = f"changesets[{index}]"
        if not isinstance(changeset, dict):
            errors.append(f"{label} must be an object")
            continue
        changeset_id = changeset.get("changeset_id")
        if not isinstance(changeset_id, str) or not changeset_id:
            errors.append(f"{label}.changeset_id must be non-empty")
        elif changeset_id in changeset_ids:
            errors.append(f"duplicate ChangeSet identity: {changeset_id}")
        else:
            changeset_ids.add(changeset_id)
        if "content_digest" in changeset:
            _validate_native_digest(changeset["content_digest"], f"{label}.content_digest", errors)
        bases = changeset.get("base_revisions")
        if bases is None:
            unresolved.append(f"{label}.base_revisions: exact participants are not declared")
            continue
        if not isinstance(bases, list):
            errors.append(f"{label}.base_revisions must be an array")
            continue
        if not bases:
            unresolved.append(f"{label}.base_revisions: no participants declared")
        changeset_repositories: set[str] = set()
        for participant_index, participant in enumerate(bases):
            participant_label = f"{label}.base_revisions[{participant_index}]"
            participant_count += 1
            if not isinstance(participant, dict):
                errors.append(f"{participant_label} must be an object")
                continue
            repository = participant.get("repository")
            if not isinstance(repository, str) or not repository or not _REPOSITORY_ID.match(repository):
                errors.append(f"{participant_label}.repository must be a GitHub repository identity")
                continue
            if repository in changeset_repositories:
                errors.append(f"duplicate ChangeSet participant repository: {repository}")
            changeset_repositories.add(repository)
            seen_repositories.add(repository)
            commit = participant.get("commit")
            if commit is None:
                unresolved.append(f"{participant_label}.commit: exact revision is not declared")
            elif not isinstance(commit, str) or not FULL_SHA_RE.match(commit):
                errors.append(f"{participant_label}.commit must be a full 40-character SHA")
            if "tree_digest" in participant:
                _validate_native_digest(participant["tree_digest"], f"{participant_label}.tree_digest", errors)
            if isinstance(expected_revisions, dict) and repository in expected_revisions:
                expected = expected_revisions[repository]
                if commit != expected:
                    errors.append(
                        f"participant revision mismatch for {repository}: expected {expected}, got {commit}"
                    )
    summary["changesets"] = sorted(changeset_ids)
    summary["participant_count"] = participant_count
    if not changeset_ids:
        unresolved.append("changesets: no ChangeSet identity is available")
    if isinstance(expected_revisions, dict):
        missing_expected = sorted(set(expected_revisions) - seen_repositories)
        for repository in missing_expected:
            errors.append(f"expected ChangeSet participant is missing: {repository}")

    contributions = record.get("contributions")
    if contributions is not None:
        if not isinstance(contributions, list):
            errors.append("contributions must be an array")
        else:
            contribution_ids: set[str] = set()
            for index, contribution in enumerate(contributions):
                label = f"contributions[{index}]"
                if not isinstance(contribution, dict):
                    errors.append(f"{label} must be an object")
                    continue
                contribution_id = contribution.get("contribution_id")
                if contribution_id:
                    if not isinstance(contribution_id, str):
                        errors.append(f"{label}.contribution_id must be a string")
                    elif contribution_id in contribution_ids:
                        errors.append(f"duplicate contribution identity: {contribution_id}")
                    else:
                        contribution_ids.add(contribution_id)
                linked = contribution.get("changeset_id")
                if linked is not None and linked not in changeset_ids:
                    errors.append(f"{label}.changeset_id references an unknown ChangeSet")

    derivations = record.get("derivations")
    if derivations is not None:
        if not isinstance(derivations, list):
            errors.append("derivations must be an array")
        else:
            for index, edge in enumerate(derivations):
                label = f"derivations[{index}]"
                if not isinstance(edge, dict):
                    errors.append(f"{label} must be an object")
                    continue
                for field in ("from", "to"):
                    if not isinstance(edge.get(field), str) or not edge[field]:
                        errors.append(f"{label}.{field} must be non-empty")
                if edge.get("relation") not in _DERIVATION_RELATIONS:
                    errors.append(f"{label}.relation uses unsupported vocabulary")

    for label, references in _iter_native_evidence_references(record):
        if not isinstance(references, list):
            errors.append(f"{label} must be an array")
            continue
        for index, reference in enumerate(references):
            ref_label = f"{label}[{index}]"
            _validate_native_evidence_reference(reference, ref_label, errors)
            if not isinstance(reference, dict):
                continue
            ref_path = reference.get("path") or reference.get("reference")
            ref_digest = reference.get("sha256")
            if evidence_root is not None and isinstance(ref_path, str) and is_safe_relative_path(ref_path) and ref_digest:
                candidate = (evidence_root / ref_path).resolve()
                root = evidence_root.resolve()
                if root != candidate and root not in candidate.parents:
                    errors.append(f"{ref_label} escapes evidence root")
                elif not candidate.is_file():
                    unresolved.append(f"{ref_label}: referenced bytes are unavailable")
                elif sha256_hex(candidate.read_bytes()) != ref_digest.lower():
                    errors.append(f"{ref_label}: referenced bytes do not match sha256")

    top_unresolved = record.get("unresolved")
    if top_unresolved is not None:
        if not isinstance(top_unresolved, list) or any(not isinstance(item, str) or not item for item in top_unresolved):
            errors.append("unresolved must be an array of non-empty strings")
        else:
            unresolved.extend(top_unresolved)
    return (None if errors else ("UNKNOWN" if unresolved else "PASS"), unresolved, errors, summary)


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
#
# FAIL vs NOT_ESTABLISHED: a well-formed domain report establishing a
# negative (blocked, structurally-invalid artifact, identity mismatch on
# an otherwise-passing claim) is FAIL -- a valid negative claim.  A
# missing/unreadable report, a report without an outcome, or a
# self-contradictory report (pass + structurally invalid, invalid +
# structurally valid) establishes NO claim: the adapter emits nothing and
# run-check records NOT_ESTABLISHED (INVALID), never a fabricated verdict.
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


def classify_rights_report(report: Any) -> Tuple[Optional[str], List[str], Optional[str]]:
    """Classify a native ``mncs-rp validate`` report for check projection.

    Returns (verdict, unresolved, error).  ``error`` non-None means the
    report establishes no claim: the caller must emit no check-result so
    the execution layer records NOT_ESTABLISHED (INVALID) instead of
    fabricating PASS/FAIL/UNKNOWN.

    Rules (see docs/adapters.md):
    - outcome missing/non-string/empty -> error (malformed report).
    - pass + structural_valid False -> error (self-contradictory).
    - invalid + structural_valid True -> error (self-contradictory).
    - pass + identity mismatch (False) -> FAIL (binding failure is a
      valid negative; tampering is Fail, never a pass).
    - unrecognized non-empty outcome -> UNKNOWN with a drift note (never
      PASS), so vocabulary growth stays visible without breaking.
    """
    unresolved: List[str] = []
    if not isinstance(report, dict):
        return None, [], "rights report must be a JSON object"
    outcome = report.get("outcome")
    if not isinstance(outcome, str) or not outcome.strip():
        return None, [], "rights report has no outcome (malformed report)"
    normalized = outcome.strip().lower()
    structural_valid = report.get("structural_valid")
    identity_matches = report.get("manifest_identity_matches")
    if normalized == "pass" and structural_valid is False:
        return None, [], "rights report claims pass for a structurally invalid manifest"
    if normalized == "invalid" and structural_valid is True:
        return None, [], "rights report claims invalid for a structurally valid manifest"
    if normalized in RIGHTS_PASS:
        verdict = "PASS"
    elif normalized in RIGHTS_FAIL:
        verdict = "FAIL"
    else:
        verdict = "UNKNOWN"
        if normalized not in RIGHTS_UNKNOWN:
            unresolved.append(
                f"rights outcome {outcome!r} unrecognized; treated as UNKNOWN (vocabulary drift)"
            )
    if identity_matches is False:
        if verdict == "PASS":
            verdict = "FAIL"
            unresolved.append(
                "manifest identity mismatch: binding failure downgrades pass to FAIL"
            )
        else:
            unresolved.append("manifest identity mismatch")
    return verdict, unresolved, None


def check_revision_token(field: str, value: Any) -> Optional[str]:
    """Validate an opaque revision label carried verbatim (or None if ok).

    Empty/absent values mean "not asserted" and are the caller's cue to
    omit the field, not an error; callers check presence themselves.
    """
    if not isinstance(value, str) or not REVISION_TOKEN_RE.match(value):
        return (
            f"{field} must be a non-empty token without whitespace or "
            "control characters when asserted"
        )
    return None


def read_binding_file(root: Path) -> Tuple[Optional[str], List[str]]:
    """Read the implementation revision bound by a mncs-actions checkout.

    Returns (implementation_revision_or_None, warnings).  ``root`` is the
    checkout root (the scripts pass their own action directory's parent).
    A missing file means "unbound checkout" (e.g. a partial vendor) and
    yields no warning; a present-but-unusable file yields a warning and is
    otherwise ignored so a degraded provenance annotation can never break
    verdict computation.  Unknown extra fields are ignored (forward
    compatible).
    """
    warnings: List[str] = []
    candidate = root / "revision-binding.json"
    if not candidate.is_file():
        return None, []
    try:
        raw = candidate.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"revision binding file unreadable, ignoring: {exc}"]
    if not isinstance(doc, dict):
        return None, ["revision binding file is not an object, ignoring"]
    revision = doc.get("implementation_revision")
    if not isinstance(revision, str) or not FULL_SHA_RE.match(revision):
        return None, ["revision binding file has no valid implementation_revision, ignoring"]
    return revision, warnings


def resolve_implementation_revision(
    explicit: str = "", search_root: Optional[Path] = None
) -> Tuple[Optional[str], List[str], Optional[str]]:
    """Resolve which implementation revision to record in evidence.

    Returns (revision_or_None, warnings, error_or_None).

    - An explicit non-empty ``explicit`` value is a caller identity claim
      and is strict: it must be a full SHA or an error is returned.
    - Otherwise the checkout's own ``revision-binding.json`` is read
      best-effort (see :func:`read_binding_file`).
    - When both sources are present and disagree, an error is returned:
      two sources disagreeing about implementation identity must never be
      silently resolved in either direction.
    """
    warnings: List[str] = []
    claimed: Optional[str] = None
    if explicit:
        if not FULL_SHA_RE.match(explicit):
            return None, [], (
                "implementation_revision must be a full 40-char SHA when asserted"
            )
        claimed = explicit
    bound: Optional[str] = None
    if search_root is not None:
        bound, file_warnings = read_binding_file(search_root)
        warnings.extend(file_warnings)
    if claimed is not None and bound is not None and claimed != bound:
        return None, warnings, (
            "implementation_revision disagrees with the executing checkout's "
            f"revision-binding.json ({claimed!r} != {bound!r})"
        )
    return claimed if claimed is not None else bound, warnings, None


def validate_badge_label(label: Any) -> List[str]:
    """Validate a badge label (display text, never a trust boundary)."""
    errors: List[str] = []
    if not isinstance(label, str) or not label:
        return ["badge label must be a non-empty string"]
    if len(label) > BADGE_LABEL_MAX_LEN:
        errors.append(
            f"badge label must be at most {BADGE_LABEL_MAX_LEN} characters"
        )
    for ch in label:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            errors.append("badge label must not contain control characters")
            break
    return errors


def project_badge_state(word: Any) -> Optional[str]:
    """Project a verdict word to its badge presentation state (or None).

    PASS/FAIL/UNKNOWN mirror established claim verdicts; INVALID is the
    projection of "no valid claim established" (missing, malformed, or
    binding-mismatched evidence).  Anything else establishes nothing and
    yields None so callers fail the input contract instead of guessing.
    The pure mapping mirrors ``pressure/badge-projection.mncs``.
    """
    if isinstance(word, str) and word in BADGE_STATES:
        return word
    return None


def badge_message(state: str) -> str:
    """Visible badge message text for a presentation state."""
    return state


def badge_color(state: str) -> str:
    """Conventional message color (hex, no "#") for a presentation state."""
    return BADGE_COLORS[state]


def _svg_text_width(text: str) -> int:
    """Deterministic integer advance: fixed 6px per character plus padding."""
    return 6 * len(text) + 10


def render_badge_svg(label: str, state: str) -> str:
    """Render a deterministic flat-style SVG badge (pure function).

    ``label`` and ``state`` must already be validated.  All text is
    XML-escaped; geometry is a pure integer function of the inputs so the
    same inputs always yield byte-identical output.  Widths are a fixed
    advance approximation, not font metrics.
    """
    from xml.sax.saxutils import escape as _escape

    def text_escaped(value: str) -> str:
        return _escape(value, {'"': "&quot;"})

    message = badge_message(state)
    color = badge_color(state)
    label_width = _svg_text_width(label)
    message_width = _svg_text_width(message)
    total_width = label_width + message_width
    aria = f"{label}: {message}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20"'
        f' role="img" aria-label="{text_escaped(aria)}">'
        f"<title>{text_escaped(aria)}</title>"
        '<linearGradient id="s" x2="0" y2="100%">'
        '<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/>'
        "</linearGradient>"
        '<clipPath id="r">'
        f'<rect width="{total_width}" height="20" rx="3" fill="#fff"/>'
        "</clipPath>"
        '<g clip-path="url(#r)">'
        f'<rect width="{label_width}" height="20" fill="#555"/>'
        f'<rect x="{label_width}" width="{message_width}" height="20" fill="#{color}"/>'
        f'<rect width="{total_width}" height="20" fill="url(#s)"/>'
        "</g>"
        '<g fill="#fff" text-anchor="middle"'
        ' font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{label_width // 2}" y="15" fill="#010101" fill-opacity=".3">'
        f"{text_escaped(label)}</text>"
        f'<text x="{label_width // 2}" y="14">{text_escaped(label)}</text>'
        f'<text x="{label_width + message_width // 2}" y="15" fill="#010101" fill-opacity=".3">'
        f"{text_escaped(message)}</text>"
        f'<text x="{label_width + message_width // 2}" y="14">{text_escaped(message)}</text>'
        "</g></svg>\n"
    )


def build_badge_doc(
    *,
    label: str,
    verdict: str,
    repository: str = "",
    subject_commit: str = "",
    boundary: str = "",
    aggregate_digest: str = "",
    manifest_digest: str = "",
    carrier_revision: str = "",
    implementation_revision: str = "",
) -> Dict[str, Any]:
    """Build a deterministic ``mncs.badge/1`` sidecar document.

    The document is a pure function of its inputs: no timestamps, no
    environment capture.  Provenance of the run lives in the evidence
    manifest and receipts; the badge only binds to them by digest and
    revision identifiers.  Empty optional fields are omitted.
    """
    doc: Dict[str, Any] = {
        "schema_version": BADGE_SCHEMA_VERSION,
        "label": label,
        "verdict": verdict,
    }
    subject: Dict[str, str] = {}
    if repository:
        subject["repository"] = repository
    if subject_commit:
        subject["commit"] = subject_commit
    if subject:
        doc["subject"] = subject
    evidence: Dict[str, str] = {}
    if boundary:
        evidence["boundary"] = boundary
    if aggregate_digest:
        evidence["aggregate_digest"] = aggregate_digest
    if manifest_digest:
        evidence["manifest_digest"] = manifest_digest
    if evidence:
        doc["evidence"] = evidence
    revisions: Dict[str, str] = {}
    if carrier_revision:
        revisions[CARRIER_REVISION_KEY] = carrier_revision
    if implementation_revision:
        revisions[IMPLEMENTATION_REVISION_KEY] = implementation_revision
    if revisions:
        doc["revisions"] = revisions
    return doc


def validate_badge(obj: Any) -> List[str]:
    """Validate an ``mncs.badge/1`` sidecar document.

    Extra/unknown fields are permitted and ignored (forward compatible);
    the verdict vocabulary is closed: only PASS/FAIL/UNKNOWN/INVALID.
    """
    errors: List[str] = []
    if not isinstance(obj, dict):
        return ["badge must be a JSON object"]
    if obj.get("schema_version") != BADGE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {BADGE_SCHEMA_VERSION}")
    errors.extend(validate_badge_label(obj.get("label")))
    if obj.get("verdict") not in BADGE_STATES:
        errors.append("verdict must be PASS, FAIL, UNKNOWN, or INVALID")
    subject = obj.get("subject")
    if subject is not None:
        if not isinstance(subject, dict):
            errors.append("subject must be an object when present")
        else:
            for field in ("repository", "commit"):
                if subject.get(field) is not None and not isinstance(subject[field], str):
                    errors.append(f"subject.{field} must be a string when present")
    evidence = obj.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, dict):
            errors.append("evidence must be an object when present")
        else:
            if evidence.get("boundary") is not None and not isinstance(
                evidence["boundary"], str
            ):
                errors.append("evidence.boundary must be a string when present")
            for field in ("aggregate_digest", "manifest_digest"):
                digest = evidence.get(field)
                if digest is not None and (
                    not isinstance(digest, str) or not _DIGEST.match(digest)
                ):
                    errors.append(
                        f"evidence.{field} must be hex64 or sha256:hex64 when present"
                    )
    revisions = obj.get("revisions")
    if revisions is not None:
        if not isinstance(revisions, dict):
            errors.append("revisions must be an object when present")
        else:
            for field in (CARRIER_REVISION_KEY, IMPLEMENTATION_REVISION_KEY):
                if revisions.get(field) is not None and not isinstance(
                    revisions[field], str
                ):
                    errors.append(f"revisions.{field} must be a string when present")
    return errors


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
