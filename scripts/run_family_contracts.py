#!/usr/bin/env python3
"""Run the bounded family protocol locally.

GitHub workflows use one producer job per owner and transport the resulting
envelopes as artifacts. This local entry point mirrors that topology by
invoking ``family_producer.py`` once per owner in separate output directories,
then delegating all composition to ``assemble_family_evidence.py``. It is a
developer convenience, not a second semantic implementation.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from family_contracts import ContractError, _read, validate_against_fixed, validate_candidate, validate_fixed  # noqa: E402
from family_protocol import (  # noqa: E402
    MAX_PRODUCER_JOB_SECONDS,
    ProtocolError,
    descriptor_map,
    ensure_clean_directory,
    load_json,
)


class IntegrationError(RuntimeError):
    """A bounded family protocol stage failed."""


def run(args: argparse.Namespace) -> int:
    family_root = args.family_root.resolve()
    actions_root = args.actions_root.resolve()
    contracts_path = args.contracts.resolve()
    fixed_path = args.fixed_contracts.resolve()
    output_dir = args.output_dir.resolve()
    if actions_root != output_dir and actions_root not in output_dir.parents:
        raise IntegrationError("local family output directory must be under actions root")
    contract = _read(contracts_path)
    if contract.get("schema_version") == "mncs-actions.family-contracts/1":
        entries = validate_fixed(contract)
    else:
        entries = validate_candidate(contract)
        validate_against_fixed(contract, _read(fixed_path))
    descriptors = descriptor_map(load_json(args.descriptors.resolve()), entries)

    # Never reuse producer output from an earlier run. This prevents a stale
    # envelope from silently filling a newly resolved candidate set.
    try:
        ensure_clean_directory(output_dir, label="local family output directory")
    except ProtocolError as exc:
        raise IntegrationError(str(exc)) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    producer_root = output_dir.with_name(output_dir.name + "-producer-artifacts")
    try:
        ensure_clean_directory(producer_root, label="local producer transport directory")
    except ProtocolError as exc:
        raise IntegrationError(str(exc)) from exc
    producer_root.mkdir(parents=True, exist_ok=True)
    for producer in sorted(descriptors):
        producer_output = producer_root / producer
        command = [
            sys.executable,
            str(ROOT / "scripts/family_producer.py"),
            "--producer", producer,
            "--family-root", str(family_root),
            "--actions-root", str(actions_root),
            "--contracts", str(contracts_path),
            "--fixed-contracts", str(fixed_path),
            "--descriptors", str(args.descriptors.resolve()),
            "--output-dir", str(producer_output),
        ]
        if args.language_binary:
            command.extend(["--language-binary", str(args.language_binary.resolve())])
        process = subprocess.run(
            command,
            cwd=actions_root,
            text=True,
            capture_output=True,
            timeout=MAX_PRODUCER_JOB_SECONDS,
        )
        if process.returncode:
            raise IntegrationError(
                f"producer {producer} failed ({process.returncode}): {process.stderr.strip()}"
            )
    assemble = [
        sys.executable,
        str(ROOT / "scripts/assemble_family_evidence.py"),
        "--actions-root", str(actions_root),
        "--contracts", str(contracts_path),
        "--fixed-contracts", str(fixed_path),
        "--descriptors", str(args.descriptors.resolve()),
        "--producer-root", str(producer_root),
        "--output-dir", str(output_dir),
    ]
    if args.previous_pressure:
        assemble.extend(["--previous-pressure", str(args.previous_pressure.resolve())])
    process = subprocess.run(
        assemble,
        cwd=actions_root,
        text=True,
        capture_output=True,
        timeout=MAX_PRODUCER_JOB_SECONDS,
    )
    if process.returncode:
        raise IntegrationError(f"family assembly failed ({process.returncode}): {process.stderr.strip()}")
    print(process.stdout, end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family-root", type=Path, required=True)
    parser.add_argument("--actions-root", type=Path, default=ROOT)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--fixed-contracts", type=Path, default=ROOT / "family-contracts.json")
    parser.add_argument("--descriptors", type=Path, default=ROOT / "family-producer-descriptors.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language-binary", type=Path, required=True)
    parser.add_argument("--previous-pressure", type=Path)
    args = parser.parse_args()
    try:
        return run(args)
    except (ContractError, ProtocolError, IntegrationError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
