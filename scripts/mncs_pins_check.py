#!/usr/bin/env python3
"""Owner-native action-pin hygiene check for the mncs-actions boundary.

Runs the repository's immutable-ref linter and writes one
mncs.check-result/1 document. A floating or unpinned action reference is
a FAIL verdict, never a warning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RESULT_SCHEMA = "mncs.check-result/1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--revision", default="working-tree")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/check-action-pins.py"],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        timeout=120,
    )
    verdict = "PASS" if completed.returncode == 0 else "FAIL"
    detail = (completed.stdout + completed.stderr).strip().splitlines()
    result = {
        "schema_version": RESULT_SCHEMA,
        "id": "action-pins",
        "provider": "mncs-actions-pincheck",
        "verdict": verdict,
        "summary": detail[-1] if detail else "no output",
        "subject": {"repository": "mncs-actions", "revision": args.revision},
    }
    destination = Path(args.result_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"id": "action-pins", "verdict": verdict}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
