#!/usr/bin/env python3
"""Project a current cross-repository lineage record into check-result/1.

The current ChangeSet protocol is owned by MNCDS and Commons.  The
``mncs-rp`` v0.3 lineage record is the first published transport carrying
those relationships, so this adapter validates that bounded bridge and
preserves the native record as evidence.  It does not decide promotion,
rights, or language semantics.

Structural or contradictory input emits no check-result and exits 2, letting
``run-check`` record NOT_ESTABLISHED/INVALID.  A structurally valid record
with missing coordination evidence becomes UNKNOWN; only a complete,
digest-bound bridge with no declared unresolved fields becomes PASS.

Usage:
  changeset_adapter.py --input lineage.json --output changeset-check.json
      [--check-id changeset-coordination]
      [--provider mncs-rights-provenance-lineage]
      [--contract-revision 0.3.0] [--producer-revision REV]
      [--evidence-root DIR]
      [--expected-revision REPOSITORY=40-HEX-SHA]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import (  # noqa: E402
    CHECK_RESULT_SCHEMA_VERSION,
    classify_changeset_lineage,
    is_safe_relative_path,
    validate_check_result,
)


def parse_expected(values: list[str]) -> tuple[dict[str, str], list[str]]:
    expected: dict[str, str] = {}
    errors: list[str] = []
    for value in values:
        repository, separator, revision = value.partition("=")
        if (
            not separator
            or not repository
            or not revision
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
        ):
            errors.append(
                "--expected-revision must be REPOSITORY=40-character-SHA"
            )
            continue
        if repository in expected:
            errors.append(f"duplicate expected participant repository: {repository}")
        expected[repository] = revision
    return expected, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Map a ChangeSet lineage record to check-result/1.")
    parser.add_argument("--input", required=True, help="mncs-rp v0.3 lineage record")
    parser.add_argument("--output", required=True, help="check-result/1 output path")
    parser.add_argument("--check-id", default="changeset-coordination")
    parser.add_argument("--provider", default="mncs-rights-provenance-lineage")
    parser.add_argument("--scope", default="cross-repository coordination")
    parser.add_argument("--claim", default="declared ChangeSet participants and evidence are mechanically bound")
    parser.add_argument("--contract-revision", default="0.3.0")
    parser.add_argument("--producer-revision", default="")
    parser.add_argument(
        "--evidence-root",
        default="",
        help="optional root for checking local evidence bytes referenced by path/reference",
    )
    parser.add_argument(
        "--expected-revision",
        action="append",
        default=[],
        help="optional caller assertion REPOSITORY=40-character-SHA; repeatable",
    )
    args = parser.parse_args()

    try:
        raw = Path(args.input).read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read ChangeSet lineage record: {exc}", file=sys.stderr)
        return 2

    expected, expected_errors = parse_expected(args.expected_revision)
    if expected_errors:
        for error in expected_errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    evidence_root = Path(args.evidence_root) if args.evidence_root else None
    verdict, unresolved, errors, summary = classify_changeset_lineage(
        record,
        expected_revisions=expected,
        evidence_root=evidence_root,
    )
    if errors or verdict is None:
        for error in errors or ["ChangeSet lineage did not establish a claim"]:
            print(f"error: {error}", file=sys.stderr)
        return 2

    changesets = summary.get("changesets", [])
    lineage_reference = {
        "kind": "lineage-record",
        "producer": "mncs-rights-provenance",
        "contract_revision": args.contract_revision,
        "digest": summary.get("content_digest_expected", "").removeprefix("sha256:"),
    }
    if is_safe_relative_path(args.input):
        lineage_reference["path"] = args.input
    else:
        lineage_reference["uri"] = args.input
    if args.producer_revision:
        lineage_reference["producer_revision"] = args.producer_revision

    check = {
        "schema_version": CHECK_RESULT_SCHEMA_VERSION,
        "id": args.check_id,
        "provider": args.provider,
        "verdict": verdict,
        "scope": args.scope,
        "claim": args.claim,
        "summary": (
            f"lineage {summary.get('lineage_id', '(missing)')} carries "
            f"{len(changesets)} ChangeSet(s) and {summary.get('participant_count', 0)} "
            f"repository participant(s) -> {verdict}"
        ),
        "references": [lineage_reference],
    }
    if unresolved:
        check["unresolved"] = unresolved
    validation_errors = validate_check_result(check)
    if validation_errors:
        for error in validation_errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(check, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"ChangeSet lineage {summary.get('lineage_id', '(missing)')} -> {verdict} ({args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
