#!/usr/bin/env python3
"""MNCDS development-record adapter: native report -> mncs.check-result/1.

Owns no development semantics.  It consumes the JSON report produced by
``mncds validate --json`` (see
machine-native-complexity-development-specification, docs/mncds-check-catalog.md)
and applies that catalog's result mapping verbatim:

  valid=true  + computed_status PASS/FAIL/UNKNOWN -> same verdict
  valid=true  + unrecognized status               -> UNKNOWN (never PASS)
  valid=true  + supported=false                   -> UNKNOWN (unevaluable)
  valid=false (issues established)                -> FAIL

Operational failures (unreadable input, malformed report) establish NO
claim: the adapter exits 2 emitting nothing, so run-check records
NOT_ESTABLISHED (INVALID) instead of fabricating a verdict.

Optional subject stamping (``--subject-repository`` plus
``--subject-commit``) binds the claim to the exact candidate revision so
an MNCS promotion boundary can verify revision coherence.  A malformed
commit (not 40-hex) is rejected, never stamped.

Usage:
  mncds_adapter.py --input mncds-report.json --output check-result.json \\
    --check-id mncds-development-record --provider mncds \\
    [--scope development] [--contract-revision 0.2-alpha.1]
    [--producer-revision REV]
    [--subject-repository epi13/example --subject-commit <40-hex>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import (  # noqa: E402
    CHECK_RESULT_SCHEMA_VERSION,
    subject_stamp,
    validate_check_result,
)

# Normative mapping; mirrors docs/mncds-check-catalog.md in the MNCDS repo.
STATUS_VERDICTS = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNKNOWN"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Map mncds report to check-result.")
    parser.add_argument("--input", required=True, help="mncds validate --json report")
    parser.add_argument("--output", required=True, help="check-result/1 output path")
    parser.add_argument("--check-id", default="mncds-development-record")
    parser.add_argument("--provider", default="mncds")
    parser.add_argument("--scope", default="")
    parser.add_argument("--claim", default="")
    parser.add_argument("--contract-revision", default="")
    parser.add_argument("--producer-revision", default="")
    parser.add_argument("--subject-repository", default="")
    parser.add_argument("--subject-commit", default="")
    args = parser.parse_args()

    stamp, stamp_error = subject_stamp(args.subject_repository, args.subject_commit)
    if stamp_error:
        print(f"error: {stamp_error}", file=sys.stderr)
        return 2

    try:
        report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read MNCDS report: {exc}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("error: MNCDS report must be a JSON object", file=sys.stderr)
        return 2

    valid = report.get("valid")
    if not isinstance(valid, bool):
        print("error: MNCDS report missing boolean 'valid'", file=sys.stderr)
        return 2
    supported = report.get("supported", True)
    if not isinstance(supported, bool):
        print("error: MNCDS report 'supported' must be a boolean", file=sys.stderr)
        return 2
    computed = str(report.get("computed_status", ""))
    issues = report.get("issues", [])
    if not isinstance(issues, list):
        issues = [issues]
    warnings = report.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = [warnings]

    record_id = report.get("record_id") or "(missing)"
    profile = report.get("profile") or "(missing)"
    if not valid:
        verdict = "FAIL"
    elif not supported:
        verdict = "UNKNOWN"
    else:
        verdict = STATUS_VERDICTS.get(computed, "UNKNOWN")

    summary = (
        f"mncds valid={valid} supported={supported} status={computed or '(missing)'} "
        f"-> {verdict}; record={record_id}; profile={profile}"
    )
    unresolved: list[str] = []
    for issue in issues:
        unresolved.append(f"mncds issue: {issue}")
    for warning in warnings:
        unresolved.append(f"mncds note: {warning}")
    if verdict == "UNKNOWN" and not unresolved:
        unresolved.append(
            "mncds development evidence is valid but incomplete for evaluation"
        )

    check: dict = {
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
        check["unresolved"] = unresolved
    if stamp:
        check["subject"] = stamp
    references = [
        {
            "kind": "mncds-development-record",
            "producer": "mncds",
            "authority": "machine-native-complexity-development-specification",
        }
    ]
    if record_id != "(missing)":
        references[0]["authority_record_identity"] = str(record_id)
    check["references"] = references

    errors = validate_check_result(check)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(check, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"mncds record {record_id} -> {verdict} ({args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
