#!/usr/bin/env python3
"""Render a human-readable promotion summary from a promotion check-result.

Owns no verdict semantics. It projects the machine-readable promotion
claim (verdict, boundary, subject, counts, blockers, per-requirement
authorities, digests) into Markdown for step summaries and review. The
badge remains a presentation of the same claim digest, never a separate
semantic source.

Usage:
  render_promotion_summary.py --input promotion-check.json [--output summary.md]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a promotion summary.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    try:
        doc = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read promotion result: {exc}", file=sys.stderr)
        return 2
    if not isinstance(doc, dict) or not isinstance(doc.get("promotion"), dict):
        print("error: input is not a promotion check-result", file=sys.stderr)
        return 2

    promotion = doc["promotion"]
    subject = promotion.get("subject", {})
    blockers = promotion.get("blockers", [])
    lines = [
        "## Promotion decision",
        f"- Verdict: {doc.get('verdict', '(missing)')}",
        f"- Boundary: {promotion.get('boundary_id', '(missing)')} "
        f"({promotion.get('boundary_revision', '(missing)')})",
        f"- Subject: {subject.get('repository', '(missing)')}@"
        f"{subject.get('commit', '(missing)')}",
        f"- Required evidence: {promotion.get('required_passed', '?')}/"
        f"{promotion.get('required_total', '?')} PASS",
        f"- Blockers ({len(blockers)}):",
    ]
    for blocker in blockers:
        lines.append(f"  - {blocker}")
    lines.append("- Evidence bound:")
    for ref in doc.get("references", []):
        if not isinstance(ref, dict):
            continue
        identity = (
            ref.get("check_id")
            or ref.get("obligation_key")
            or ref.get("boundary_id")
            or ref.get("contract_revision", "(unnamed)")
        )
        lines.append(
            f"  - {ref.get('kind')}:{identity} "
            f"[{ref.get('verdict', ref.get('status', '-'))}] "
            f"{ref.get('authority', '')} {ref.get('digest', '(no digest)')}"
        )
    lines.append(f"- Claim: {doc.get('summary', '')}")
    text = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
