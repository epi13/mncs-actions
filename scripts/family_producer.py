#!/usr/bin/env python3
"""Execute exactly one allowlisted family producer operation.

The producer job is intentionally one-owner-at-a-time.  It writes only its
own native reports and check-results; aggregation happens later from an
artifact-only transport.  Descriptor fields select an operation but never
contain shell or Python source.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from family_contracts import ContractError, validate_against_fixed, validate_candidate, validate_fixed  # noqa: E402
from family_protocol import (  # noqa: E402
    MAX_ARTIFACT_BYTES,
    MAX_OWNER_OPERATION_SECONDS,
    PRODUCER_OUTPUT_SCHEMA,
    ProtocolError,
    _walk_transport_files,
    descriptor_map,
    descriptor_outputs,
    document_digest,
    ensure_clean_directory,
    load_json,
    load_json_bytes,
    validate_transport_tree,
    write_json,
)
from mncs_actions import sha256_hex, validate_check_result  # noqa: E402


class ProducerError(RuntimeError):
    """The bounded producer invocation did not establish output."""


def _revision(checkout: Path) -> str:
    process = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _inside(root: Path, relative: str) -> Path:
    raw_candidate = root / relative
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ProducerError(f"symlink is not permitted in bounded input path: {relative}")
    candidate = raw_candidate.resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ProducerError(f"path escapes bounded root: {relative}")
    if candidate.is_symlink():
        raise ProducerError(f"symlink is not permitted in bounded input path: {relative}")
    return candidate


def _owner_environment(**updates: str) -> dict[str, str]:
    """Pass only non-secret runner/toolchain context into owner code."""
    import os

    allowed = {
        "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
        "CARGO_HOME", "RUSTUP_HOME", "PYTHONHASHSEED",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(updates)
    return environment


def _run_json(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env or _owner_environment(),
        capture_output=True,
        text=False,
        timeout=MAX_OWNER_OPERATION_SECONDS,
    )
    if process.returncode:
        stderr = process.stderr.decode("utf-8", errors="replace").strip()
        raise ProducerError(
            f"owner operation failed ({process.returncode}): {' '.join(command)}\n"
            f"{stderr}"
        )
    try:
        return load_json_bytes(process.stdout, label="owner operation JSON")
    except ProtocolError as exc:
        raise ProducerError(str(exc)) from exc


def _adapt(command: list[str], *, cwd: Path) -> None:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=_owner_environment(),
        capture_output=True,
        text=True,
        timeout=MAX_OWNER_OPERATION_SECONDS,
    )
    if process.returncode:
        raise ProducerError(
            f"bounded adapter failed ({process.returncode}): {' '.join(command)}\n"
            f"{process.stderr.strip()}"
        )


def _output_path(output_dir: Path, check_id: str) -> Path:
    if not check_id or "/" in check_id or "\\" in check_id or check_id in {".", ".."}:
        raise ProducerError(f"check id cannot be used as a bounded output name: {check_id!r}")
    return output_dir / "checks" / f"{check_id}.json"


def _copy_native(source: Path, output_dir: Path, name: str) -> Path:
    if source.is_symlink() or not source.is_file():
        raise ProducerError(f"native input must be a regular file: {source}")
    if source.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ProducerError(f"native input exceeds {MAX_ARTIFACT_BYTES} bytes: {source}")
    destination = output_dir / "native" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _run_operation(
    descriptor: dict[str, Any],
    *,
    checkout: Path,
    family_root: Path,
    actions_root: Path,
    output_dir: Path,
    producer_revision: str,
    language_binary: Path | None,
    provenance_binding: dict[str, Any] | None,
) -> list[Path]:
    execution = descriptor["execution"]
    operation = execution["operation"]
    inputs = execution["input_paths"]
    adapters = actions_root / "adapters"
    outputs = descriptor_outputs(descriptor)
    generated: list[Path] = []

    if operation == "mncs-standard-validate":
        report_path = output_dir / "native/mncs-report.json"
        report = _run_json(
            [
                sys.executable,
                "-c",
                "from mncs_validator.cli import main; raise SystemExit(main(__import__('sys').argv[1:]))",
                "validate",
                str(_inside(checkout, inputs["manifest"])),
                "--json",
            ],
            cwd=checkout,
            env=_owner_environment(PYTHONPATH=str(checkout / "src")),
        )
        write_json(report_path, report)
        target = _output_path(output_dir, "mncs-validation")
        output = outputs["mncs-validation"]
        _adapt(
            [
                sys.executable,
                str(adapters / "validator_adapter.py"),
                "--input", str(report_path), "--output", str(target),
                "--check-id", output["check_id"], "--provider", output["provider"],
                "--contract-revision", output["contract_revision"],
                "--producer-revision", producer_revision,
            ],
            cwd=actions_root,
        )
        generated.append(target)

    elif operation == "rights-provenance-validate":
        report_path = output_dir / "native/rights-report.json"
        report = _run_json(
            [
                sys.executable, "-m", "mncs_rights_provenance.cli", "validate",
                str(_inside(checkout, inputs["manifest"])), "--findings-are-not-failures",
            ],
            cwd=checkout,
            env=_owner_environment(PYTHONPATH=str(checkout / "src")),
        )
        write_json(report_path, report)
        target = _output_path(output_dir, "rights-provenance")
        output = outputs["rights-provenance"]
        _adapt(
            [
                sys.executable, str(adapters / "rights_adapter.py"),
                "--input", str(report_path), "--output", str(target),
                "--check-id", output["check_id"], "--provider", output["provider"],
                "--contract-revision", output["contract_revision"],
                "--producer-revision", producer_revision,
            ] + (
                [
                    "--manifest-path", provenance_binding["path"],
                    "--manifest-digest", provenance_binding["sha256"],
                ]
                if provenance_binding is not None
                else []
            ),
            cwd=actions_root,
        )
        generated.append(target)

    elif operation == "mncds-development-record-validate":
        record = _inside(checkout, inputs["record"])
        report_path = output_dir / "native/mncds-report.json"
        report = _run_json(
            [
                sys.executable, "-c",
                (
                    "import json; from pathlib import Path; "
                    "from mncds_validator.mncds import validate_development_record; "
                    f"print(json.dumps(validate_development_record(Path({str(record)!r})).as_dict()))"
                ),
            ],
            cwd=checkout,
            env=_owner_environment(PYTHONPATH=str(checkout / "src")),
        )
        write_json(report_path, report)
        target = _output_path(output_dir, "mncds-development-record")
        output = outputs["mncds-development-record"]
        _adapt(
            [
                sys.executable, str(adapters / "mncds_adapter.py"),
                "--input", str(report_path), "--output", str(target),
                "--check-id", output["check_id"], "--provider", output["provider"],
                "--contract-revision", output["contract_revision"],
                "--producer-revision", producer_revision,
            ],
            cwd=actions_root,
        )
        generated.append(target)

    elif operation == "commons-compatibility-validate":
        registry = _inside(checkout, inputs["registry"])
        _copy_native(registry, output_dir, "commons-registry.json")
        target = _output_path(output_dir, "commons-family-compatibility")
        output = outputs["commons-family-compatibility"]
        _adapt(
            [
                sys.executable, str(adapters / "commons_adapter.py"),
                "--commons-root", str(checkout), "--output", str(target),
                "--producer-revision", producer_revision,
                "--contract-revision", output["contract_revision"],
            ],
            cwd=actions_root,
        )
        generated.append(target)

    elif operation == "language-source-study":
        if language_binary is None:
            raise ProducerError("language-source-study requires --language-binary")
        for case in execution["cases"]:
            source = _inside(actions_root, case["source_path"])
            report_path = output_dir / "native" / f"language-{case['name']}.json"
            process = subprocess.run(
                [str(language_binary), "source-study", str(source), "--node-id", "family-producer"],
                cwd=checkout,
                env=_owner_environment(MNCS_LIBRARY_PATH=str(checkout / inputs["library"])),
                capture_output=True,
                text=False,
                timeout=MAX_OWNER_OPERATION_SECONDS,
            )
            if process.returncode:
                raise ProducerError(
                    "mncs-language source-study failed: "
                    + process.stderr.decode("utf-8", errors="replace").strip()
                )
            try:
                study = load_json_bytes(process.stdout, label="mncs-language source-study JSON")
            except ProtocolError as exc:
                raise ProducerError(str(exc)) from exc
            write_json(report_path, study)
            output = outputs[case["check_id"]]
            target = _output_path(output_dir, case["check_id"])
            _adapt(
                [
                    sys.executable, str(adapters / "language_adapter.py"),
                    "--input", str(report_path), "--output", str(target),
                    "--source-path", case["source_path"],
                    "--check-id", output["check_id"], "--producer-revision", producer_revision,
                ],
                cwd=actions_root,
            )
            generated.append(target)

    elif operation == "forge-cell-validate":
        target = _output_path(output_dir, "forge-cell-contract")
        output = outputs["forge-cell-contract"]
        _adapt(
            [
                sys.executable, str(adapters / "forge_adapter.py"),
                "--forge-root", str(checkout), "--output", str(target),
                "--expected-nonce", execution["expected_nonce"],
                "--producer-revision", producer_revision,
                "--contract-revision", output["contract_revision"],
            ],
            cwd=actions_root,
        )
        for key, name in (("policy", "forge-policy.json"), ("bundle", "forge-test-bundle.json"), ("record", "forge-execution-record.json")):
            _copy_native(_inside(checkout, inputs[key]), output_dir, name)
        generated.append(target)

    else:  # pragma: no cover - registry validation should catch this first.
        raise ProducerError(f"operation is not allowlisted: {operation}")
    return generated


def run(args: argparse.Namespace) -> int:
    family_root = args.family_root.resolve()
    actions_root = args.actions_root.resolve()
    output_dir = args.output_dir.resolve()
    contract_document = load_json(args.contracts.resolve())
    if contract_document.get("schema_version") == "mncs-actions.family-contracts/1":
        entries = validate_fixed(contract_document)
        mode = "fixed"
    else:
        entries = validate_candidate(contract_document)
        validate_against_fixed(contract_document, load_json(args.fixed_contracts.resolve()))
        mode = "moving-head"
    by_name = {entry["name"]: entry for entry in entries}
    descriptors_document = load_json(args.descriptors.resolve())
    descriptors = descriptor_map(descriptors_document, entries)
    producer = args.producer
    if producer not in by_name or producer not in descriptors:
        raise ProducerError(f"unknown producer: {producer}")
    entry = by_name[producer]
    descriptor = descriptors[producer]
    checkout = _inside(family_root, entry["checkout_path"])
    if not checkout.is_dir():
        raise ProducerError(f"missing producer checkout: {checkout}")
    expected_revision = entry.get("revision", entry.get("candidate_revision"))
    actual_revision = _revision(checkout)
    if actual_revision != expected_revision:
        raise ProducerError(f"{producer} is {actual_revision}, expected {expected_revision}")
    for artifact in entry["artifacts"]:
        artifact_path = _inside(checkout, artifact)
        if not artifact_path.is_file():
            raise ProducerError(f"missing {producer} artifact: {artifact}")
        if artifact_path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ProducerError(f"family artifact exceeds {MAX_ARTIFACT_BYTES} bytes: {artifact}")

    try:
        ensure_clean_directory(output_dir, label="producer output directory")
    except ProtocolError as exc:
        raise ProducerError(str(exc)) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    provenance_binding: dict[str, Any] | None = None
    provenance_context = descriptor.get("provenance_context")
    if provenance_context is not None:
        input_path = _inside(checkout, descriptor["execution"]["input_paths"][provenance_context["input_key"]])
        if descriptor["execution"]["input_paths"][provenance_context["input_key"]] not in entry["artifacts"]:
            raise ProducerError("provenance context input must be pinned in the family contract")
        copied = _copy_native(input_path, output_dir, provenance_context["transport_name"])
        provenance_binding = {
            "kind": provenance_context["kind"],
            "authority": provenance_context["authority"],
            "path": copied.relative_to(output_dir).as_posix(),
            "sha256": sha256_hex(copied.read_bytes()),
            "authority_status": provenance_context.get("authority_status", "UNKNOWN"),
            "revision": actual_revision,
        }
    generated = _run_operation(
        descriptor,
        checkout=checkout,
        family_root=family_root,
        actions_root=actions_root,
        output_dir=output_dir,
        producer_revision=actual_revision,
        language_binary=args.language_binary.resolve() if args.language_binary else None,
        provenance_binding=provenance_binding,
    )
    expected_outputs = descriptor_outputs(descriptor)
    check_results = []
    files = []
    try:
        output_stats = _walk_transport_files(output_dir)
    except ProtocolError as exc:
        raise ProducerError(str(exc)) from exc
    for relative in sorted(output_stats):
        kind = "check-result" if relative.startswith("checks/") else "native"
        path = output_dir / relative
        files.append({
            "path": relative,
            "sha256": sha256_hex(path.read_bytes()),
            "size": output_stats[relative],
            "kind": kind,
        })
    for path in sorted(generated):
        raw = path.read_bytes()
        try:
            value = load_json_bytes(raw, label=f"generated check {path}")
        except ProtocolError as exc:
            raise ProducerError(str(exc)) from exc
        errors = validate_check_result(value)
        if errors:
            raise ProducerError(f"generated check is invalid: {path}: {'; '.join(errors)}")
        expected = expected_outputs.get(value.get("id"))
        if expected is None:
            raise ProducerError(f"generated check id is not declared: {value.get('id')}")
        if value.get("provider") != expected["provider"] or value.get("contract_revision") != expected["contract_revision"]:
            raise ProducerError(f"generated check metadata mismatch: {path}")
        if value.get("producer_revision") != actual_revision:
            raise ProducerError(f"generated check is not bound to producer revision: {path}")
        check_results.append({
            "id": value["id"],
            "path": path.relative_to(output_dir).as_posix(),
            "sha256": sha256_hex(raw),
        })
    if {item["id"] for item in check_results} != set(expected_outputs):
        raise ProducerError("generated check membership does not match descriptor")
    output = {
        "schema_version": PRODUCER_OUTPUT_SCHEMA,
        "mode": mode,
        "producer": producer,
        "repository": descriptor["repository"],
        "revision": actual_revision,
        "descriptor_digest": document_digest(args.descriptors.resolve()),
        "contract_digest": document_digest(args.contracts.resolve()),
        "files": files,
        "check_results": check_results,
    }
    if provenance_binding is not None:
        output["provenance_bindings"] = [provenance_binding]
    write_json(output_dir / "producer-execution.json", output)
    try:
        validate_transport_tree(output_dir, [item["path"] for item in files])
    except ProtocolError as exc:
        raise ProducerError(str(exc)) from exc
    print(json.dumps({"producer": producer, "checks": len(check_results), "output": str(output_dir)}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--family-root", type=Path, required=True)
    parser.add_argument("--actions-root", type=Path, default=ROOT)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--fixed-contracts", type=Path, default=ROOT / "family-contracts.json")
    parser.add_argument("--descriptors", type=Path, default=ROOT / "family-producer-descriptors.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language-binary", type=Path)
    args = parser.parse_args()
    try:
        return run(args)
    except (ContractError, ProtocolError, ProducerError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
