#!/usr/bin/env python3
"""Project development-pressure evidence into MNCDS obligation records.

Owns no obligation semantics.  The field mapping is verbatim from
``docs/obligation-projection.md`` in
machine-native-complexity-development-specification:

  pressure obligation_key -> obligation_key (verbatim)
  pressure category       -> origin.kind = development-pressure
  pressure authority      -> origin.authority (verbatim)
  pressure_id             -> origin.pressure_id (verbatim)
  CLI subject             -> subject (exact repository + 40-hex commit)
  lifecycle NEW/REPRODUCED (+ status UNKNOWN) -> status open
  required                -> true (pressure exists because evidence was
                             insufficient; only an MNCS promotion boundary
                             may tolerate listed keys explicitly)

Projection never closes an obligation and never marks one optional: only
development work with evidence resolves, and only boundary policy
tolerates.  ``not_reproduced`` pressure projects to nothing.  Malformed
pressure, duplicate keys, or a non-exact subject establish NO claim:
exit 2 emits nothing.

Usage:
  pressure_to_obligations.py --pressure development-pressure-evidence.json
      --subject-repository epi13/example --subject-commit <40-hex>
      --output obligations.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import subject_stamp  # noqa: E402

OBLIGATION_SCHEMA_VERSION = "mncds-obligation-record/0.1"


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Project pressure to obligations.")
    parser.add_argument("--pressure", required=True)
    parser.add_argument("--subject-repository", required=True)
    parser.add_argument("--subject-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    stamp, stamp_error = subject_stamp(args.subject_repository, args.subject_commit)
    if stamp_error:
        return _fail(stamp_error)
    assert stamp is not None

    try:
        document = json.loads(Path(args.pressure).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _fail(f"cannot read pressure evidence: {exc}")
    if not isinstance(document, dict):
        return _fail("pressure evidence must be a JSON object")
    obligations = document.get("obligations", [])
    if not isinstance(obligations, list):
        return _fail("pressure obligations must be an array")

    records: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(obligations):
        label = f"obligations[{index}]"
        if not isinstance(item, dict):
            return _fail(f"{label} must be an object")
        key = item.get("obligation_key")
        if not isinstance(key, str) or not key:
            return _fail(f"{label} needs a non-empty obligation_key")
        if key in seen:
            return _fail(f"duplicate obligation key: {key}")
        seen.add(key)
        authority = item.get("semantic_authority") or item.get("producer") or ""
        if not isinstance(authority, str) or not authority:
            return _fail(f"{label} names no semantic authority")
        evidence: list[str] = [f"pressure_id:{item.get('pressure_id', '(missing)')}"]
        for entry in item.get("unresolved", []) or []:
            if isinstance(entry, str) and entry:
                evidence.append(entry)
        record = {
            "schema_version": OBLIGATION_SCHEMA_VERSION,
            "obligation_key": key,
            "status": "open",
            "required": True,
            "subject": stamp,
            "origin": {
                "kind": "development-pressure",
                "authority": authority,
            },
            "evidence": evidence,
            "supersedes": None,
            "extensions": {},
        }
        pressure_id = item.get("pressure_id")
        if isinstance(pressure_id, str) and pressure_id:
            record["origin"]["pressure_id"] = pressure_id
        records.append(record)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"pressure {len(records)} obligation(s) -> open ({args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
