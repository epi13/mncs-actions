#!/usr/bin/env python3
"""Rights & provenance adapter: native mncs-rp output -> mncs.check-result/1.

Owns no policy.  It invokes NOTHING; it consumes the JSON report produced by
``mncs-rp validate`` (see mncs-rights-provenance) and maps it with explicit,
documented semantics:

  pass                  -> PASS (requires identity match + structural_valid)
  blocked / invalid     -> FAIL (valid negative established)
  pass-with-findings /
  review-required /
  unknown               -> UNKNOWN (review outstanding; never PASS)

FAIL vs NOT_ESTABLISHED: a well-formed negative report is FAIL.  A missing
outcome, an unreadable report, or a self-contradictory report (pass for a
structurally-invalid manifest, invalid for a structurally-valid one)
establishes NO claim: the adapter exits 2 emitting nothing, so run-check
records NOT_ESTABLISHED (INVALID) instead of fabricating a verdict.
Identity mismatch downgrades an otherwise-passing claim to FAIL (binding
failure is a valid negative; tampering is Fail, never a pass).
Unrecognized non-empty outcomes become UNKNOWN with an explicit drift note
(never PASS).

Unrecognized outcomes map to UNKNOWN with an explicit unresolved entry so a
vocabulary drift can never masquerade as PASS.  The native outcome,
severity, findings, and manifest identity are preserved verbatim in the
check summary/unresolved/references.

Usage:
  rights_adapter.py --input rp-report.json --output check-result.json \\
    --check-id rights-provenance --provider mncs-rights-provenance \\
    [--scope release] [--contract-revision v0.3.0] [--producer-revision REV]
    [--manifest-path RIGHTS.json] [--manifest-digest HEX]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import (  # noqa: E402
    CHECK_RESULT_SCHEMA_VERSION,
    classify_rights_report,
    subject_stamp,
    validate_check_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Map mncs-rp report to check-result.")
    parser.add_argument("--input", required=True, help="mncs-rp validate JSON report")
    parser.add_argument("--output", required=True, help="check-result/1 output path")
    parser.add_argument("--check-id", default="rights-provenance")
    parser.add_argument("--provider", default="mncs-rights-provenance")
    parser.add_argument("--scope", default="")
    parser.add_argument("--claim", default="")
    parser.add_argument("--contract-revision", default="")
    parser.add_argument("--producer-revision", default="")
    parser.add_argument("--manifest-path", default="")
    parser.add_argument("--manifest-digest", default="")
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
        print(f"error: cannot read rights report: {exc}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("error: rights report must be a JSON object", file=sys.stderr)
        return 2

    outcome = str(report.get("outcome", ""))
    # Report-level classification enforces FAIL vs NOT_ESTABLISHED: a
    # missing/contradictory report establishes no claim (exit 2, caller
    # records INVALID); vocabulary drift becomes UNKNOWN, never PASS.
    verdict, classification_notes, classification_error = classify_rights_report(report)
    if classification_error is not None or verdict is None:
        print(f"error: {classification_error}", file=sys.stderr)
        return 2
    severity = str(report.get("severity", ""))
    findings = report.get("findings", [])
    if not isinstance(findings, list):
        findings = [str(findings)]
    issues = report.get("issues", [])
    if not isinstance(issues, list):
        issues = [str(issues)]
    identity = str(report.get("manifest_identity_expected", ""))

    summary_parts = [
        f"rights outcome {outcome or '(missing)'} -> {verdict}",
        f"severity {severity or '(missing)'}",
    ]
    if args.manifest_path:
        summary_parts.append(f"manifest {args.manifest_path}")
    summary = "; ".join(summary_parts) + ". Native result preserved; legal_conclusion=NOT_MADE."

    unresolved: list[str] = []
    unresolved.extend(classification_notes)
    for finding in findings:
        unresolved.append(f"rights finding: {finding}")
    for issue in issues:
        unresolved.append(f"rights issue: {issue}")
    if verdict == "UNKNOWN" and not unresolved:
        unresolved.append(f"rights outcome {outcome!r} requires review under adapter semantics")

    references: list[dict] = []
    if args.manifest_path or args.manifest_digest or identity:
        ref: dict = {
            "kind": "rights-manifest",
            "producer": "mncs-rights-provenance",
            "authority": "mncs-rights-provenance",
        }
        if args.contract_revision:
            ref["contract_revision"] = args.contract_revision
        if args.producer_revision:
            ref["producer_revision"] = args.producer_revision
        if args.manifest_path:
            ref["path"] = args.manifest_path
        digest = args.manifest_digest or identity
        if digest:
            ref["digest"] = digest
        ref["authority_status"] = (
            report.get("authority_status")
            if report.get("authority_status") in {"PASS", "FAIL", "UNKNOWN"}
            else "UNKNOWN"
        )
        if identity:
            ref["authority_record_identity"] = identity
        references.append(ref)

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
        check["unresolved"] = unresolved
    if references:
        check["references"] = references
    if stamp:
        check["subject"] = stamp

    errors = validate_check_result(check)
    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 2
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(check, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"rights {outcome!r} -> {verdict} ({args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
