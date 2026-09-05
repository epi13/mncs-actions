#!/usr/bin/env python3
"""Owner-native project check for the mncs-actions family boundary.

Runs the repository's deterministic test suite and writes one
mncs.check-result/1 document. Exit 0 always carries the verdict file;
a FAIL verdict is data, never a crash.
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
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        timeout=600,
    )
    verdict = "PASS" if completed.returncode == 0 else "FAIL"
    tail = (completed.stdout + completed.stderr)[-800:]
    result = {
        "schema_version": RESULT_SCHEMA,
        "id": "project-tests",
        "provider": "mncs-actions-pytest",
        "verdict": verdict,
        "summary": f"pytest tests/ exit={completed.returncode}: {tail.strip().splitlines()[-1] if tail.strip() else 'no output'}",
        "subject": {"repository": "mncs-actions", "revision": args.revision},
    }
    destination = Path(args.result_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"id": "project-tests", "verdict": verdict}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
