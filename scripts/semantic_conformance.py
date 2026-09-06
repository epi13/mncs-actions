#!/usr/bin/env python3
"""Semantic-conformance provider for the mncs-actions boundary.

Transport only: runs ``mncs conformance`` against a semantic-contract
program, validates the emitted ``mncs.conformance-report/1`` document, and
projects it to one ``mncs.check-result/1`` verdict through
``lib/mncs_actions.py::classify_conformance_report`` (the host mirror of
``pressure/semantic-conformance.mncs`` gate plus has_followups).

All verdict logic lives in the classifier (mirrored in MNCS). This script
only moves bytes: process execution, file IO, and exit codes. Exit 0 always
carries the verdict file; a FAIL verdict is data, never a crash.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib  # noqa: E402

RESULT_SCHEMA = "mncs.check-result/1"
CHECK_ID = "semantic-conformance"
PROVIDER = "mncs-conformance-cli"

DEFAULT_MNCS_BIN = os.environ.get(
    "MNCS_BIN", "/home/epi13/Documents/Projects/mncs-language/target/debug/mncs"
)


def run_conformance(mncs_bin: str, program: str, backends: str, seed: int, cases: int, step_budget: int, library_path: str | None) -> dict:
    with tempfile.TemporaryDirectory(prefix="mncs-conformance-") as tmp:
        report_path = Path(tmp) / "report.json"
        command = [
            mncs_bin,
            "conformance",
            program,
            "--cases",
            str(cases),
            "--seed",
            str(seed),
            "--backends",
            backends,
            "--step-budget",
            str(step_budget),
            "--output",
            str(report_path),
        ]
        env = dict(os.environ)
        if library_path:
            env["MNCS_LIBRARY_PATH"] = library_path
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=1800,
        )
        if not report_path.is_file():
            raise RuntimeError(
                f"mncs conformance produced no report (exit={completed.returncode}): "
                f"{(completed.stdout + completed.stderr)[-800:]}"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["_cli_exit"] = completed.returncode
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", required=True, help="semantic-contract program (.mncs or program JSON)")
    parser.add_argument("--backends", default="mncs-portable-wasm-mvp,mncs-research-bytecode")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cases", type=int, default=8)
    parser.add_argument("--step-budget", type=int, default=200000)
    parser.add_argument("--library-path", default=None)
    parser.add_argument("--mncs-bin", default=DEFAULT_MNCS_BIN)
    parser.add_argument("--report-file", default=None, help="keep the conformance report as evidence")
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--revision", default="working-tree")
    args = parser.parse_args()

    try:
        report = run_conformance(
            args.mncs_bin, args.program, args.backends, args.seed, args.cases, args.step_budget, args.library_path
        )
    except (OSError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        result = {
            "schema_version": RESULT_SCHEMA,
            "id": CHECK_ID,
            "provider": PROVIDER,
            "verdict": "UNKNOWN",
            "summary": f"conformance harness error (no claim): {error}",
            "subject": {"program": args.program, "revision": args.revision},
        }
        destination = Path(args.result_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"id": CHECK_ID, "verdict": "UNKNOWN"}))
        return 0

    if args.report_file:
        kept = dict(report)
        kept.pop("_cli_exit", None)
        destination = Path(args.report_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n")

    verdict, unresolved, error = lib.classify_conformance_report(report)
    if error is not None or verdict is None:
        result = {
            "schema_version": RESULT_SCHEMA,
            "id": CHECK_ID,
            "provider": PROVIDER,
            "verdict": "UNKNOWN",
            "summary": f"conformance report establishes no claim: {error}",
            "subject": {"program": args.program, "revision": args.revision},
        }
    else:
        summary = report.get("summary", {})
        result = {
            "schema_version": RESULT_SCHEMA,
            "id": CHECK_ID,
            "provider": PROVIDER,
            "verdict": verdict,
            "summary": (
                f"conformance {args.program}: "
                f"pass={summary.get('pass')} fail={summary.get('fail')} "
                f"unknown={summary.get('unknown')} unsupported={summary.get('unsupported')}"
            ),
            "subject": {
                "program": args.program,
                "module": report.get("subject_module"),
                "fingerprint": report.get("subject_fingerprint"),
                "revision": args.revision,
            },
        }
        if unresolved:
            result["unresolved"] = unresolved
    destination = Path(args.result_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"id": CHECK_ID, "verdict": result["verdict"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
