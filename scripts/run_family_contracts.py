#!/usr/bin/env python3
"""Run the current family-owned contract surfaces and emit check results.

This is an integration harness for the fixed and moving-head canaries.  It
invokes validators from their owning repositories, adapts their reports to
``mncs.check-result/1``, and records the exact family revisions used.  It does
not promote a candidate or edit the fixed contract.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from family_contracts import (  # noqa: E402
    ContractError,
    _read,
    validate_against_fixed,
    validate_candidate,
    validate_fixed,
)
from mncs_actions import sha256_hex, validate_check_result  # noqa: E402


class IntegrationError(RuntimeError):
    """A required owner-owned integration could not establish a result."""


REQUIRED_FAMILY_REPOSITORIES = {
    "mncs-standard",
    "rights-provenance",
    "mncds",
    "commons",
    "mncs-language",
    "forge",
}


def _run_json(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if process.returncode != 0:
        raise IntegrationError(
            f"owner command failed ({process.returncode}): {' '.join(command)}\n"
            f"{process.stderr.strip()}"
        )
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            f"owner command did not emit JSON: {' '.join(command)}: {exc}\n{process.stdout[:1000]}"
        ) from exc
    if not isinstance(value, dict):
        raise IntegrationError(f"owner command emitted a non-object: {' '.join(command)}")
    return value


def _revision(checkout: Path) -> str:
    process = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _adapt(command: list[str], *, cwd: Path) -> None:
    process = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=240)
    if process.returncode:
        raise IntegrationError(
            f"adapter failed ({process.returncode}): {' '.join(command)}\n{process.stderr.strip()}"
        )


def _check_paths(output_dir: Path) -> list[str]:
    return sorted(
        str(path.relative_to(output_dir.parent)).replace("\\", "/")
        for path in output_dir.glob("*.json")
        if path.name != "family-contract-evidence.json"
    )


def run(args: argparse.Namespace) -> int:
    family_root = args.family_root.resolve()
    actions_root = args.actions_root.resolve()
    output_dir = args.output_dir.resolve()
    contracts_path = args.contracts.resolve()
    document = _read(contracts_path)
    if document.get("schema_version") == "mncs-actions.family-contracts/1":
        entries = validate_fixed(document)
        mode = "fixed"
    else:
        entries = validate_candidate(document)
        mode = "moving-head"
        validate_against_fixed(document, _read(args.fixed_contracts.resolve()))
    by_name = {entry["name"]: entry for entry in entries}
    missing = REQUIRED_FAMILY_REPOSITORIES - by_name.keys()
    if missing:
        raise IntegrationError(
            "family contract is missing required repositories: " + ", ".join(sorted(missing))
        )
    for entry in entries:
        checkout = family_root / entry["checkout_path"]
        if not checkout.is_dir():
            raise IntegrationError(f"missing family checkout: {checkout}")
        expected = entry.get("revision", entry.get("candidate_revision"))
        actual = _revision(checkout)
        if actual != expected:
            raise IntegrationError(f"{entry['name']} is {actual}, expected {expected}")
        for artifact in entry["artifacts"]:
            if not (checkout / artifact).is_file():
                raise IntegrationError(f"missing {entry['name']} artifact: {artifact}")

    output_dir.mkdir(parents=True, exist_ok=True)
    native_dir = output_dir / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    adapters_dir = actions_root / "adapters"
    producer_revisions = {
        name: _revision(family_root / entry["checkout_path"])
        for name, entry in by_name.items()
    }

    # MNCS standard validator -> Actions generic check-result adapter.
    standard = family_root / by_name["mncs-standard"]["checkout_path"]
    standard_report = native_dir / "mncs-report.json"
    report = _run_json(
        [
            sys.executable,
            "-c",
            "from mncs_validator.cli import main; raise SystemExit(main(__import__('sys').argv[1:]))",
            "validate",
            str(standard / "examples/minimal/manifest.json"),
            "--json",
        ],
        cwd=standard,
        env={**os.environ, "PYTHONPATH": str(standard / "src")},
    )
    _write(standard_report, report)
    standard_check = output_dir / "mncs-validation.json"
    _adapt(
        [
            sys.executable,
            str(adapters_dir / "validator_adapter.py"),
            "--input",
            str(standard_report),
            "--output",
            str(standard_check),
            "--check-id",
            "mncs-validation",
            "--provider",
            "mncs-validator-rs",
            "--contract-revision",
            "0.2",
            "--producer-revision",
            producer_revisions["mncs-standard"],
        ],
        cwd=actions_root,
    )

    # Rights/provenance native validation -> rights adapter.
    rights = family_root / by_name["rights-provenance"]["checkout_path"]
    rights_report = native_dir / "rights-report.json"
    report = _run_json(
        [
            sys.executable,
            "-m",
            "mncs_rights_provenance.cli",
            "validate",
            str(rights / "dogfood/human-specification.json"),
            "--findings-are-not-failures",
        ],
        cwd=rights,
        env={**os.environ, "PYTHONPATH": str(rights / "src")},
    )
    _write(rights_report, report)
    rights_check = output_dir / "rights-provenance.json"
    _adapt(
        [
            sys.executable,
            str(adapters_dir / "rights_adapter.py"),
            "--input",
            str(rights_report),
            "--output",
            str(rights_check),
            "--check-id",
            "rights-provenance",
            "--contract-revision",
            "0.3.0",
            "--producer-revision",
            producer_revisions["rights-provenance"],
        ],
        cwd=actions_root,
    )

    # MNCDS owns development-record validation; its report is compatible with
    # the generic validator adapter's valid/computed_status projection.
    mncds = family_root / by_name["mncds"]["checkout_path"]
    mncds_report = native_dir / "mncds-report.json"
    report = _run_json(
        [
            sys.executable,
            "-c",
            (
                "import json; from pathlib import Path; "
                "from mncds_validator.mncds import validate_development_record; "
                f"print(json.dumps(validate_development_record(Path({str(mncds / 'examples/mncds-0.2-alpha/language-span-fix.development-record.json')!r})).as_dict()))"
            ),
        ],
        cwd=mncds,
        env={**os.environ, "PYTHONPATH": str(mncds / "src")},
    )
    _write(mncds_report, report)
    mncds_check = output_dir / "mncds-development-record.json"
    _adapt(
        [
            sys.executable,
            str(adapters_dir / "validator_adapter.py"),
            "--input",
            str(mncds_report),
            "--output",
            str(mncds_check),
            "--check-id",
            "mncds-development-record",
            "--provider",
            "mncds",
            "--contract-revision",
            "0.2-alpha.1",
            "--producer-revision",
            producer_revisions["mncds"],
        ],
        cwd=actions_root,
    )

    # Commons runs its own compatibility fixture/adapter validator and owns
    # the family producer registry semantics.
    commons = family_root / by_name["commons"]["checkout_path"]
    commons_check = output_dir / "commons-family-compatibility.json"
    _adapt(
        [
            sys.executable,
            str(adapters_dir / "commons_adapter.py"),
            "--commons-root",
            str(commons),
            "--output",
            str(commons_check),
            "--producer-revision",
            producer_revisions["commons"],
        ],
        cwd=actions_root,
    )

    # Three real Actions pressure programs are studied by the language
    # compiler.  Unresolved compiler obligations remain UNKNOWN in the adapter.
    language = family_root / by_name["mncs-language"]["checkout_path"]
    language_binary = args.language_binary.resolve()
    pressure_names = ("family-boundary", "rights-projection", "changeset-boundary")
    for pressure_name in pressure_names:
        source = actions_root / "pressure" / f"{pressure_name}.mncs"
        report_path = native_dir / f"language-{pressure_name}.json"
        process = subprocess.run(
            [str(language_binary), "source-study", str(source), "--node-id", f"family-{mode}"],
            cwd=language,
            env={**os.environ, "MNCS_LIBRARY_PATH": str(language / "library")},
            capture_output=True,
            text=True,
            timeout=240,
        )
        if process.returncode:
            raise IntegrationError(f"mncs-language source-study failed for {pressure_name}: {process.stderr}")
        try:
            study = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise IntegrationError(f"mncs-language source-study was not JSON for {pressure_name}: {exc}") from exc
        _write(report_path, study)
        _adapt(
            [
                sys.executable,
                str(adapters_dir / "language_adapter.py"),
                "--input",
                str(report_path),
                "--output",
                str(output_dir / f"language-{pressure_name}.json"),
                "--source-path",
                f"pressure/{pressure_name}.mncs",
                "--check-id",
                f"mncs-language-{pressure_name}",
                "--producer-revision",
                producer_revisions["mncs-language"],
            ],
            cwd=actions_root,
        )

    # Forge owns Forge Cell schema and assurance semantics.  The reference
    # record intentionally projects UNKNOWN because isolation is unmet.
    forge = family_root / by_name["forge"]["checkout_path"]
    forge_check = output_dir / "forge-cell-contract.json"
    _adapt(
        [
            sys.executable,
            str(adapters_dir / "forge_adapter.py"),
            "--forge-root",
            str(forge),
            "--output",
            str(forge_check),
            "--expected-nonce",
            "reference-nonce-0000000000000001",
            "--producer-revision",
            producer_revisions["forge"],
        ],
        cwd=actions_root,
    )

    checks = []
    for path in sorted(output_dir.glob("*.json")):
        if path.name == "family-contract-evidence.json":
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_check_result(value)
        if errors:
            raise IntegrationError(f"invalid generated check {path}: {'; '.join(errors)}")
        checks.append(
            {
                "id": value["id"],
                "verdict": value["verdict"],
                "path": str(path.relative_to(actions_root)).replace("\\", "/"),
                "digest": sha256_hex(path.read_bytes()),
            }
        )
    evidence = {
        "schema_version": "mncs-actions.family-integration-evidence/1",
        "mode": mode,
        "contract_document": str(contracts_path.relative_to(actions_root)).replace("\\", "/")
        if contracts_path.is_relative_to(actions_root)
        else str(contracts_path),
        "contract_digest": sha256_hex(contracts_path.read_bytes()),
        "family_revisions": {
            entry["name"]: {
                "repository": entry["repository"],
                "branch": entry.get("branch", "fixed-contract"),
                "revision": entry.get("revision", entry.get("candidate_revision")),
            }
            for entry in entries
        },
        "checks": checks,
        "authority": {
            "mncs": "machine-native-complexity-standard",
            "mncds": "machine-native-complexity-development-specification",
            "commons": "MNCS-Commons",
            "rights": "mncs-rights-provenance",
            "language": "mncs-language",
            "forge": "mncs-forge-mcp",
            "orchestration": "mncs-actions",
        },
        "promotion": "observation only; this document cannot update family-contracts.json",
    }
    _write(output_dir / "family-contract-evidence.json", evidence)
    print(json.dumps({"mode": mode, "checks": len(checks), "output": str(output_dir)}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family-root", type=Path, required=True)
    parser.add_argument("--actions-root", type=Path, default=ROOT)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--fixed-contracts", type=Path, default=ROOT / "family-contracts.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language-binary", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args)
    except (ContractError, IntegrationError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
