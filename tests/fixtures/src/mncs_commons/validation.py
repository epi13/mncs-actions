"""Test double: owner Commons validation report protocol (see package docstring)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ValidationReport:
    valid: bool
    diagnostics: list


def validate_record(record):
    if not isinstance(record, dict):
        return ValidationReport(False, ["record must be an object"])
    if record.get("kind") != "ChangeSet":
        return ValidationReport(False, ["record kind must be ChangeSet"])
    details = record.get("details")
    if not isinstance(details, dict):
        return ValidationReport(False, ["record needs details"])
    if not details.get("changesetId"):
        return ValidationReport(False, ["record needs details.changesetId"])
    if not isinstance(details.get("baseRevisions"), list):
        return ValidationReport(False, ["record needs details.baseRevisions"])
    from .canonical import canonical_digest

    if record.get("contentDigest") is not None:
        expected = canonical_digest(
            {k: v for k, v in record.items() if k != "contentDigest"}
        )
        if record["contentDigest"] != expected:
            return ValidationReport(False, ["contentDigest does not recompute"])
    return ValidationReport(True, [])
