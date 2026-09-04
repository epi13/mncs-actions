#!/usr/bin/env python3
"""Project MNCDS obligation records into an mncds-obligations check-result.

Owns no obligation semantics.  The mapping is verbatim from
``docs/mncds-check-catalog.md`` and ``docs/obligation-projection.md`` in
machine-native-complexity-development-specification
(``mncds-obligation-record/0.2``):

  all resolved, or none required            -> PASS
  a required obligation still open          -> UNKNOWN (keys named)
  a rejected obligation with evidence       -> FAIL (negative finding)

Malformed records, duplicate obligation keys, or records bound to a
different subject establish NO claim: this script exits 2 emitting
nothing, so run-check records NOT_ESTABLISHED (INVALID).  Tolerance
policy belongs to the MNCS promotion boundary, not to this projection:
tolerated keys are still reported here and tolerated there.

Usage:
  project_obligations.py --obligations obligation-*.json
      --subject-repository epi13/example --subject-commit <40-hex>
      --output .mncs/mncds-obligations-check.json
      [--check-id mncds-obligations] [--provider mncds]
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

OBLIGATION_SCHEMA_VERSION = "mncds-obligation-record/0.2"
OBLIGATION_CONTRACT_REVISION = "mncds-obligation-record/0.2"


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Project obligations to check-result.")
    parser.add_argument("--obligations", nargs="*", default=[])
    parser.add_argument("--subject-repository", required=True)
    parser.add_argument("--subject-commit", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--check-id", default="mncds-obligations")
    parser.add_argument("--provider", default="mncds")
    parser.add_argument("--scope", default="development obligations")
    parser.add_argument("--claim", default="development obligations are resolved enough for evaluation")
    args = parser.parse_args()

    stamp, stamp_error = subject_stamp(args.subject_repository, args.subject_commit)
    if stamp_error:
        return _fail(stamp_error)
    assert stamp is not None

    records: list[dict] = []
    seen: set[str] = set()
    documents: list[tuple[str, dict]] = []
    for path in args.obligations:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _fail(f"cannot read obligation {path}: {exc}")
        # Accept one obligation record per file or one obligation set
        # (JSON array, as emitted by pressure_to_obligations.py).
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if not isinstance(item, dict):
                return _fail(f"obligation {path} must hold JSON objects")
            documents.append((path, item))
    for path, doc in documents:
        if doc.get("schema_version") != OBLIGATION_SCHEMA_VERSION:
            return _fail(
                f"obligation {path} schema_version must be {OBLIGATION_SCHEMA_VERSION}"
            )
        key = doc.get("obligation_key")
        if not isinstance(key, str) or not key:
            return _fail(f"obligation {path} needs a non-empty obligation_key")
        if key in seen:
            return _fail(f"duplicate obligation key: {key}")
        seen.add(key)
        if doc.get("status") not in ("open", "resolved", "rejected"):
            return _fail(f"obligation {key} has unknown status")
        if not isinstance(doc.get("required"), bool):
            return _fail(f"obligation {key} needs a boolean required flag")
        subject = doc.get("subject")
        if (
            not isinstance(subject, dict)
            or subject.get("repository") != args.subject_repository
            or subject.get("commit") != args.subject_commit
        ):
            return _fail(f"obligation {key} is bound to another subject")
        if doc["status"] in ("resolved", "rejected") and not isinstance(
            doc.get("resolution"), dict
        ):
            return _fail(f"obligation {key} resolved/rejected without a resolution block")
        records.append(doc)

    rejected = [item["obligation_key"] for item in records if item["status"] == "rejected"]
    blocking = [
        item["obligation_key"]
        for item in records
        if item["status"] == "open" and item["required"]
    ]
    advisory = [
        item["obligation_key"]
        for item in records
        if item["status"] == "open" and not item["required"]
    ]
    resolved = sum(1 for item in records if item["status"] == "resolved")

    if rejected:
        verdict = "FAIL"
    elif blocking:
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"

    summary = (
        f"obligations resolved={resolved} open-required={len(blocking)} "
        f"open-optional={len(advisory)} rejected={len(rejected)} -> {verdict}"
    )
    unresolved: list[str] = []
    for key in rejected:
        unresolved.append(f"obligation {key} rejected with authoritative evidence")
    for key in blocking:
        unresolved.append(f"obligation {key} open (required)")
    for key in advisory:
        unresolved.append(f"obligation {key} open (optional): visible, not deciding")

    check: dict = {
        "schema_version": CHECK_RESULT_SCHEMA_VERSION,
        "id": args.check_id,
        "provider": args.provider,
        "verdict": verdict,
        "scope": args.scope,
        "claim": args.claim,
        "summary": summary,
        "contract_revision": OBLIGATION_CONTRACT_REVISION,
        "subject": stamp,
        "references": [
            {
                "kind": "mncds-obligation-record",
                "producer": "mncds",
                "authority": "machine-native-complexity-development-specification",
                "contract_revision": OBLIGATION_CONTRACT_REVISION,
            }
        ],
    }
    if unresolved:
        check["unresolved"] = unresolved

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
    print(f"obligations ({len(records)}) -> {verdict} ({args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
