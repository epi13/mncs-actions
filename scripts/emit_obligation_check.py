#!/usr/bin/env python3
"""Emit an owner-signed check-result/1 for one host-harness obligation.

The obligation owner declares a host entrypoint (a command the CI
runner executes). This emitter runs that entrypoint verbatim and maps
the process outcome onto the check-result/1 vocabulary:

  exit 0            -> PASS
  exit nonzero      -> FAIL
  timeout           -> UNKNOWN (with unresolved: ["execution-timeout"])
  launch failure    -> UNKNOWN (with unresolved: ["execution-launch"])

Timeouts and launch failures are infrastructure facts, never product
verdicts; they map to UNKNOWN, never FAIL. The emitted document always
carries the obligation identity as its check id so run-check admission
can bind it exactly.

Usage:
  emit_obligation_check.py --obligation ID --provider NAME \
      --result-file PATH [--timeout SECONDS] -- ENTRYPOINT...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--obligation", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("entrypoint", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    entrypoint = list(args.entrypoint)
    if entrypoint and entrypoint[0] == "--":
        entrypoint = entrypoint[1:]
    if not entrypoint:
        print("emit_obligation_check: missing entrypoint", file=sys.stderr)
        return 2
    try:
        completed = subprocess.run(
            entrypoint, capture_output=True, text=True,
            timeout=max(1, args.timeout), check=False,
        )
    except FileNotFoundError:
        verdict, unresolved = "UNKNOWN", ["execution-launch"]
        output = ""
        exit_code: int | None = None
    except subprocess.TimeoutExpired as exc:
        verdict, unresolved = "UNKNOWN", ["execution-timeout"]
        output = (exc.stdout or "") + (exc.stderr or "")
        exit_code = None
    else:
        output = (completed.stdout or "") + (completed.stderr or "")
        exit_code = completed.returncode
        if completed.returncode == 0:
            verdict, unresolved = "PASS", []
        else:
            verdict, unresolved = "FAIL", []
    tail = output[-4000:]
    document = {
        "schema_version": "mncs.check-result/1",
        "id": args.obligation,
        "provider": args.provider,
        "verdict": verdict,
        "summary": f"host entrypoint exit={exit_code}",
        "digest": "sha256:" + hashlib.sha256(tail.encode("utf-8")).hexdigest(),
    }
    if unresolved:
        document["unresolved"] = unresolved
    result_path = Path(args.result_file)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(tail[-2000:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
