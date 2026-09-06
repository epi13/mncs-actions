#!/usr/bin/env python3
"""RFC conformance ledger check: implementation claims stay tied to evidence.

Validates a machine-readable RFC ledger of the form owned by mncs-language
(``rfcs/conformance-ledger.json``): schema version, ordered entries, design
and implementation vocabularies, per-criterion states, and — the load-bearing
rule — that every satisfied or partial criterion names evidence that exists
under the repository root, while unsatisfied criteria carry none (unless
explicitly excused with a recorded gap).

A PASS here is transport only: it proves the ledger is well-formed and its
claims resolve to files. Whether those files actually establish the claim is
decided by the owning repository's own suites (in mncs-language, the
MNCS-native status gates in ``mncs.family.rfc_status.v1``).

Usage:
  check_rfc_ledger.py --ledger LEDGER.json --root REPO_ROOT
Exit 0 on PASS, 1 on FAIL with one line per defect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "0.1"
DESIGN_STATES = {"DRAFT", "PROPOSED", "ACCEPTED", "STABLE", "SUPERSEDED"}
IMPLEMENTATION_STATES = {
    "NONE",
    "SUBSTRATE",
    "PARTIAL",
    "BOUNDED_IMPLEMENTATION",
    "IMPLEMENTED_EXPERIMENTALLY",
    "IMPLEMENTED",
}
CRITERION_STATES = {"satisfied", "partial", "unsatisfied"}
GAP_REFERENCE = re.compile(r"^[0-9]{4}-[A-Z][0-9]+$")


def check_ledger(ledger: dict, root: Path) -> list[str]:
    errors: list[str] = []
    if ledger.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or not entries:
        return errors + ["entries must be a non-empty array"]
    numbers = [entry.get("number") for entry in entries]
    if numbers != sorted(numbers):
        errors.append("entries must be in ascending numeric order")
    if len(set(numbers)) != len(numbers):
        errors.append("entry numbers must be unique")
    for entry in entries:
        number = entry.get("number", "?")
        if entry.get("design_status") not in DESIGN_STATES:
            errors.append(f"RFC {number}: design_status outside the vocabulary")
        if entry.get("implementation_status") not in IMPLEMENTATION_STATES:
            errors.append(f"RFC {number}: implementation_status outside the vocabulary")
        criteria = entry.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"RFC {number}: no acceptance criteria")
            continue
        for criterion in criteria:
            criterion_id = criterion.get("id", "?")
            state = criterion.get("state")
            if state not in CRITERION_STATES:
                errors.append(f"RFC {number} {criterion_id}: state outside the vocabulary")
                continue
            evidence = criterion.get("evidence", [])
            if not isinstance(evidence, list):
                errors.append(f"RFC {number} {criterion_id}: evidence must be an array")
                continue
            excused = criterion.get("excused") is True
            if state in ("satisfied", "partial") and not evidence:
                errors.append(
                    f"RFC {number} {criterion_id}: {state} claim carries no evidence"
                )
            if state == "unsatisfied" and not excused and evidence:
                errors.append(
                    f"RFC {number} {criterion_id}: unsatisfied claim must carry no evidence"
                )
            for item in evidence:
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"RFC {number} {criterion_id}: empty evidence entry")
                    continue
                if GAP_REFERENCE.match(item):
                    continue
                if not (root / item).exists():
                    errors.append(f"RFC {number} {criterion_id}: missing evidence {item}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    try:
        ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL: cannot read ledger: {error}")
        return 1
    errors = check_ledger(ledger, Path(args.root))
    if errors:
        print(f"FAIL: {len(errors)} defect(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("PASS: ledger claims resolve to evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
