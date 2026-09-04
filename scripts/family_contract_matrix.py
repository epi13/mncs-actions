#!/usr/bin/env python3
"""Emit a GitHub Actions matrix from a fixed or candidate family document."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from family_contracts import ContractError, _read, validate_against_fixed, validate_candidate, validate_fixed  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--fixed", type=Path)
    args = parser.parse_args()
    try:
        document = _read(args.contracts)
        if document.get("schema_version") == "mncs-actions.family-contracts/1":
            entries = validate_fixed(document)
        else:
            entries = validate_candidate(document)
            if args.fixed:
                validate_against_fixed(document, _read(args.fixed))
        matrix = {
            "include": [
                {
                    "producer": entry["name"],
                    "repository": entry["repository"],
                    "revision": entry.get("revision", entry.get("candidate_revision")),
                    "checkout_path": entry["checkout_path"],
                }
                for entry in entries
            ]
        }
        encoded = json.dumps(matrix, sort_keys=True, separators=(",", ":"))
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as handle:
                handle.write(f"matrix={encoded}\n")
        print(encoded)
        return 0
    except (ContractError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
