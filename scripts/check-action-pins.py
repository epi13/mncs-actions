#!/usr/bin/env python3
"""Reject mutable remote GitHub Action refs in executable YAML."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
REMOTE_USE = re.compile(r"^\s*uses:\s*([^\s#]+)")
SHA = re.compile(r"^[0-9a-f]{40}$")


def action_files() -> list[Path]:
    return sorted(
        [
            *REPOSITORY.glob(".github/workflows/**/*.yml"),
            *REPOSITORY.glob(".github/workflows/**/*.yaml"),
            *REPOSITORY.glob("actions/**/*.yml"),
            *REPOSITORY.glob("actions/**/*.yaml"),
        ]
    )


def main() -> int:
    errors: list[str] = []
    for path in action_files():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = REMOTE_USE.match(line)
            if not match:
                continue
            reference = match.group(1)
            if reference.startswith("./") or reference.startswith("../"):
                continue
            if reference.startswith("docker://"):
                errors.append(f"{path}:{line_number}: Docker refs need an explicit policy: {reference}")
                continue
            if "@" not in reference:
                errors.append(f"{path}:{line_number}: action has no revision: {reference}")
                continue
            action, revision = reference.rsplit("@", 1)
            if "/" not in action or not SHA.fullmatch(revision):
                errors.append(
                    f"{path}:{line_number}: remote action must use a lowercase 40-character SHA: {reference}"
                )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"checked immutable action refs in {len(action_files())} YAML files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
