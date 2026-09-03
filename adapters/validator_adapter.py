#!/usr/bin/env python3
"""MNCS validator adapter: mncs-rs report -> mncs.check-result/1.

Owns no validation semantics.  It consumes the JSON report produced by
``mncs-rs validate --json`` / ``validate-bundle --json`` (see
mncs-validator-rs) and maps it with explicit semantics:

  valid=true  + computed_status PASS/FAIL/UNKNOWN -> same verdict
  valid=true  + unrecognized status               -> UNKNOWN (never PASS)
  valid=false (issues established)                -> FAIL

Operational failures (exit 2, no report) must NOT be fabricated into a
check-result; the caller should let run-check emit NOT_ESTABLISHED instead.

Usage:
  validator_adapter.py --input validation-report.json --output check-result.json \\
    --check-id mncs-validation --provider mncs-validator-rs \\
    [--scope specification] [--contract-revision 0.2] [--producer-revision REV]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import (  # noqa: E402
    CHECK_RESULT_SCHEMA_VERSION,
    map_validator_computed_status,
    validate_check_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Map mncs-rs report to check-result.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--check-id", default="mncs-validation")
    parser.add_argument("--provider", default="mncs-validator-rs")
    parser.add_argument("--scope", default="")
    parser.add_argument("--claim", default="")
    parser.add_argument("--contract-revision", default="")
    parser.add_argument("--producer-revision", default="")
    args = parser.parse_args()

    try:
        report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read validator report: {exc}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("error: validator report must be a JSON object", file=sys.stderr)
        return 2

    valid = report.get("valid")
    if not isinstance(valid, bool):
        print("error: validator report missing boolean 'valid'", file=sys.stderr)
        return 2
    computed = str(report.get("computed_status", report.get("computedStatus", "")))
    issues = report.get("issues", [])
    if not isinstance(issues, list):
        issues = [str(issues)]
    verdict, notes = map_validator_computed_status(
        valid=valid, computed_status=computed
    )
    summary = (
        f"validator valid={valid} status={computed or '(missing)'} -> {verdict}; "
        f"checked_files={report.get('checked_files', '?')}; "
        f"issues={len([i for i in issues if isinstance(i, str)])}"
    )
    unresolved: list[str] = list(notes)
    for issue in issues:
        unresolved.append(f"validator issue: {issue}")

    check = {
        "schema_version": CHECK_RESULT_SCHEMA_VERSION,
        "id": args.check_id,
        "provider": args.provider,
        "verdict": verdict,
        "summary": summary,
    }
    if args.scope:
        check["scope"] = args.scope
    if args.claim:
        check["claim"] = args.claim
    if args.contract_revision:
        check["contract_revision"] = args.contract_revision
    if args.producer_revision:
        check["producer_revision"] = args.producer_revision
    if unresolved:
        # For FAIL, issues are the established negative evidence; for
        # UNKNOWN they are the reason PASS could not be established.
        check["unresolved"] = [str(item) for item in unresolved]

    errors = validate_check_result(check)
    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 2
    Path(args.output).write_text(
        json.dumps(check, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"validator valid={valid} status={computed!r} -> {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
