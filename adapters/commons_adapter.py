#!/usr/bin/env python3
"""Consume Commons' own compatibility validator as check-result/1.

The compatibility semantics remain in ``MNCS-Commons``.  This adapter checks
the published family producer registry for the records Actions is consuming,
runs the owner-provided compatibility suite, and carries the exact registry
bytes as evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import CHECK_RESULT_SCHEMA_VERSION, sha256_hex, validate_check_result  # noqa: E402

REQUIRED_PRODUCERS = {
    "mncs-language": "CompilationStudyResult",
    "mncs-forge": "ConceptEvaluation",
    "mncds": "DevelopmentRecord",
}


def load_registry(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Commons producer registry: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("contracts"), list):
        raise ValueError("Commons producer registry must contain a contracts array")
    seen: set[str] = set()
    for index, contract in enumerate(value["contracts"]):
        if not isinstance(contract, dict):
            raise ValueError(f"Commons contract {index} must be an object")
        producer = contract.get("producer")
        if not isinstance(producer, str) or not producer:
            raise ValueError(f"Commons contract {index} has no producer")
        if producer in seen:
            raise ValueError(f"Commons producer registry duplicates {producer}")
        seen.add(producer)
        for field in ("recordKind", "schemaVersion", "sourceRepository", "sourcePath", "sourceFingerprint"):
            if not isinstance(contract.get(field), str) or not contract[field]:
                raise ValueError(f"Commons contract {producer} has no {field}")
    for producer, record_kind in REQUIRED_PRODUCERS.items():
        matching = [item for item in value["contracts"] if item.get("producer") == producer]
        if not matching:
            raise ValueError(f"Commons registry is missing required producer {producer}")
        if matching[0].get("recordKind") != record_kind:
            raise ValueError(f"Commons registry recordKind mismatch for {producer}")
    return value


def build_check(
    *,
    registry: dict[str, Any],
    registry_path: str,
    validator_returncode: int,
    validator_stdout: str,
    validator_stderr: str,
    producer_revision: str = "",
    contract_revision: str = "0.1",
) -> dict[str, Any]:
    registry_digest = sha256_hex(
        json.dumps(registry, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    verdict = "PASS" if validator_returncode == 0 else "FAIL"
    unresolved = []
    if validator_returncode:
        unresolved.append("Commons owner compatibility validator exited non-zero")
        if validator_stderr.strip():
            unresolved.append("Commons validator: " + validator_stderr.strip()[:1000])
    check: dict[str, Any] = {
        "schema_version": CHECK_RESULT_SCHEMA_VERSION,
        "id": "commons-family-compatibility",
        "provider": "MNCS-Commons",
        "verdict": verdict,
        "scope": "producer-registry-and-owner-compatibility-validator",
        "claim": "Commons registry and compatibility fixtures were checked by Commons-owned code",
        "contract_revision": contract_revision,
        "summary": (
            f"Commons owner compatibility validator exit={validator_returncode}; "
            f"registry producers={len(registry['contracts'])}"
        ),
        "references": [
            {
                "kind": "commons-producer-registry",
                "producer": "MNCS-Commons",
                "path": registry_path,
                "digest": registry_digest,
            }
        ],
        "unresolved": unresolved,
        "digest": registry_digest,
    }
    if validator_stdout.strip():
        check["validator_output"] = validator_stdout.strip()[:2000]
    if producer_revision:
        check["producer_revision"] = producer_revision
        check["references"][0]["producer_revision"] = producer_revision
    errors = validate_check_result(check)
    if errors:
        raise ValueError("invalid adapted Commons check: " + "; ".join(errors))
    return check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commons-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer-revision", default="")
    parser.add_argument("--contract-revision", default="0.1")
    args = parser.parse_args()
    registry_path = args.commons_root / "compat/family-record-producers.json"
    try:
        registry = load_registry(registry_path)
        process = subprocess.run(
            [sys.executable, str(args.commons_root / "scripts/validate_compat.py")],
            cwd=args.commons_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        check = build_check(
            registry=registry,
            registry_path="family/commons/compat/family-record-producers.json",
            validator_returncode=process.returncode,
            validator_stdout=process.stdout,
            validator_stderr=process.stderr,
            producer_revision=args.producer_revision,
            contract_revision=args.contract_revision,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Commons compatibility -> {check['verdict']}")
        return 0
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
