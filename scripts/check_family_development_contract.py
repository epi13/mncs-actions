#!/usr/bin/env python3
"""Validate and optionally bind the fixed MNCS development-loop contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "mncs-actions.family-development-contract/1"
RESULT_SCHEMA = "mncs-actions.family-development-contract-validation/1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SLUG_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


class ContractError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read contract: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("contract must be a JSON object")
    return value


def _safe_path(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("/", "\\"))
        or "\\" in value
        or "//" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ContractError(f"{label} must be a safe relative path")


def validate(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema_version") != SCHEMA:
        raise ContractError(f"schema_version must be {SCHEMA}")
    if document.get("mode") != "fixed":
        raise ContractError("mode must be fixed")
    carrier = document.get("carrier")
    if not isinstance(carrier, dict) or carrier.get("revision_binding") != "workflow_subject_sha":
        raise ContractError("carrier must bind the Actions workflow subject SHA")
    if carrier.get("repository") != "epi13/mncs-actions" or carrier.get("exact_at_runtime") is not True:
        raise ContractError("carrier must be the exact mncs-actions workflow subject")
    entries = document.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise ContractError("repositories must be a non-empty array")
    names: set[str] = set()
    slugs: set[str] = set()
    paths: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"repositories[{index}]"
        if not isinstance(entry, dict):
            raise ContractError(f"{label} must be an object")
        for field in ("name", "repository", "revision", "checkout_path", "artifacts"):
            if field not in entry:
                raise ContractError(f"{label}.{field} is required")
        name = entry["name"]
        slug = entry["repository"]
        path = entry["checkout_path"]
        if not isinstance(name, str) or not name or name in names:
            raise ContractError(f"{label}.name must be unique non-empty text")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug) or slug.casefold() in slugs:
            raise ContractError(f"{label}.repository must be a unique owner/repository slug")
        if not isinstance(entry["revision"], str) or not SHA_RE.fullmatch(entry["revision"]):
            raise ContractError(f"{label}.revision must be a lowercase full SHA")
        _safe_path(path, f"{label}.checkout_path")
        path_key = path.casefold()
        if path_key in paths:
            raise ContractError(f"{label}.checkout_path must be unique")
        artifacts = entry["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            raise ContractError(f"{label}.artifacts must be non-empty")
        for artifact_index, artifact in enumerate(artifacts):
            _safe_path(artifact, f"{label}.artifacts[{artifact_index}]")
        names.add(name)
        slugs.add(slug.casefold())
        paths.add(path_key)
    verification = document.get("verification")
    if not isinstance(verification, dict) or verification.get("profile") != "0.17":
        raise ContractError("verification must bind Profile 0.17")
    stages = verification.get("required_stages")
    if not isinstance(stages, list) or not {str(item) for item in stages} >= {
        "compiler_inventory", "mncs_test", "mncs_debug", "mncs_actions", "forge_diagnosis", "mncs_test_verification"
    }:
        raise ContractError("verification.required_stages is incomplete")
    return entries


def _workspace_checks(entries: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for entry in entries:
        checkout = root / entry["checkout_path"]
        actual: str | None = None
        error: str | None = None
        try:
            actual = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
        missing = [artifact for artifact in entry["artifacts"] if not (checkout / artifact).is_file() and not (checkout / artifact).is_dir()]
        passed = error is None and actual == entry["revision"] and not missing
        checks.append({"name": entry["name"], "expected": entry["revision"], "actual": actual, "missing_artifacts": missing, "error": error, "status": "PASS" if passed else "FAIL"})
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path("family-development-contract.json"))
    parser.add_argument("--workspace-root", type=Path)
    args = parser.parse_args(argv)
    try:
        document = _read(args.contract)
        entries = validate(document)
        checks = _workspace_checks(entries, args.workspace_root.resolve()) if args.workspace_root else []
        verdict = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
        if args.workspace_root is None:
            verdict = "PASS"
        result = {
            "schema_version": RESULT_SCHEMA,
            "verdict": verdict,
            "contract": str(args.contract),
            "contract_sha256": hashlib.sha256(args.contract.read_bytes()).hexdigest(),
            "repositories": len(entries),
            "workspace_root": str(args.workspace_root.resolve()) if args.workspace_root else None,
            "checks": checks,
            "carrier": document["carrier"],
        }
    except (ContractError, OSError) as exc:
        result = {"schema_version": RESULT_SCHEMA, "verdict": "INVALID", "error": str(exc)}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
