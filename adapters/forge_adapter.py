#!/usr/bin/env python3
"""Consume Forge's native Forge Cell validator as check-result/1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import CHECK_RESULT_SCHEMA_VERSION, sha256_hex, validate_check_result  # noqa: E402


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Forge document is not an object: {path}")
    return value


def build_check(
    *,
    forge_root: Path,
    policy_path: Path,
    bundle_path: Path,
    record_path: Path,
    expected_nonce: str,
    producer_revision: str = "",
) -> dict[str, Any]:
    try:
        from mncs_forge.forge_cell import (  # type: ignore[import-not-found]
            assess_execution_assurance,
            validate_forge_cell_document,
        )
    except ImportError as exc:
        raise ValueError(f"cannot import Forge validator: {exc}") from exc
    policy = _load(policy_path)
    bundle = _load(bundle_path)
    record = _load(record_path)
    # These calls are intentionally delegated to Forge; Actions does not
    # reproduce Forge's schema or assurance rules.
    validate_forge_cell_document("policy", policy)
    validate_forge_cell_document("test-bundle", bundle)
    validate_forge_cell_document("execution-record", record)
    assessment = assess_execution_assurance(policy, record, expected_nonce=expected_nonce)
    references = []
    for kind, path in (("forge-policy", policy_path), ("forge-test-bundle", bundle_path), ("forge-execution-record", record_path)):
        references.append(
            {
                "kind": kind,
                "producer": "mncs-forge-mcp",
                "path": "family/forge/" + str(path.relative_to(forge_root)).replace("\\", "/"),
                "digest": sha256_hex(path.read_bytes()),
            }
        )
    check: dict[str, Any] = {
        "schema_version": CHECK_RESULT_SCHEMA_VERSION,
        "id": "forge-cell-contract",
        "provider": "mncs-forge-mcp",
        "verdict": assessment.status,
        "scope": "forge-cell-schema-and-assurance-boundary",
        "claim": "Forge-owned Forge Cell validation and assurance projection were executed",
        "summary": (
            f"Forge execution result={record.get('result')}; "
            f"assurance={assessment.status}; enforced={list(assessment.enforced)}; "
            f"unmet={list(assessment.unmet)}"
        ),
        "references": references,
        "unresolved": list(assessment.reasons),
        "digest": sha256_hex(record_path.read_bytes()),
    }
    if producer_revision:
        check["producer_revision"] = producer_revision
        for reference in references:
            reference["producer_revision"] = producer_revision
    errors = validate_check_result(check)
    if errors:
        raise ValueError("invalid adapted Forge check: " + "; ".join(errors))
    return check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forge-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-nonce", required=True)
    parser.add_argument("--producer-revision", default="")
    args = parser.parse_args()
    try:
        # The Forge package is loaded from the exact checked-out family head.
        sys.path.insert(0, str(args.forge_root / "src"))
        check = build_check(
            forge_root=args.forge_root,
            policy_path=args.forge_root / "examples/forge-cell/policy.json",
            bundle_path=args.forge_root / "examples/forge-cell/test-bundle.json",
            record_path=args.forge_root / "examples/forge-cell/execution-record.json",
            expected_nonce=args.expected_nonce,
            producer_revision=args.producer_revision,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Forge Cell contract -> {check['verdict']}")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
