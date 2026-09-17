#!/usr/bin/env python3
"""Execute only the consumer proof surfaces selected by a verification plan.

The Commons graph supplies topology and edge identities.  Repository-owned
``family-verification-checks-v1.json`` files supply a typed check identity;
runner identities select trusted adapters and graph data never supplies a
command. Actions owns process transport, receipts, and composition,
while Commons remains the authority for the graph and plan contracts.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent / ".." / "lib"))
from mncs_actions import (  # noqa: E402
    CLAIM_ESTABLISHED,
    build_evidence_manifest,
    build_execution_receipt,
    canonical_bytes,
    sha256_hex,
    validate_execution_receipt,
    validate_check_result,
)
from mncs_family_contract import (  # noqa: E402
    bind_declaration_evidence,
    declaration_identity,
    load_family_graph,
    validate_plan,
    validate_verification_manifest,
)


PROOF_SCHEMA = "mncs-actions.selective-family-proof/1"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class SelectiveFamilyError(ValueError):
    """A fail-closed selective family routing error."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SelectiveFamilyError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise SelectiveFamilyError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _file_digest(path: Path) -> str:
    return sha256_hex(path.read_bytes())


def _directory_digest(path: Path) -> str:
    """Hash a bounded directory tree without following directory symlinks."""

    entries: list[dict[str, str]] = []
    total_bytes = 0
    for root, directories, files in os.walk(path, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in files:
            candidate = Path(root) / name
            relative = candidate.relative_to(path).as_posix()
            if candidate.is_symlink():
                entries.append({"path": relative, "kind": "symlink", "target": os.readlink(candidate)})
                continue
            if not candidate.is_file():
                raise SelectiveFamilyError(f"runtime library entry is not a regular file: {candidate}")
            size = candidate.stat().st_size
            total_bytes += size
            if len(entries) >= 4096 or total_bytes > 256 * 1024 * 1024:
                raise SelectiveFamilyError(f"runtime library directory is too large to bind safely: {path}")
            entries.append({"path": relative, "kind": "file", "sha256": _file_digest(candidate)})
    return sha256_hex(canonical_bytes(entries))


def _path_identity(value: str, *, kind: str) -> str:
    """Bind an executable/library path without making paths semantic."""

    path = Path(value)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if path.is_dir() and not path.is_symlink():
        return f"{kind}:directory:{_directory_digest(path)}"
    if path.is_file():
        return f"{kind}:file:{_file_digest(path)}"
    resolved = shutil.which(value)
    if resolved:
        candidate = Path(resolved)
        if candidate.is_file():
            return f"{kind}:file:{_file_digest(candidate)}"
    return f"{kind}:command:{value}"


def _canonical_selector(value: Mapping[str, Any]) -> dict[str, Any]:
    selector = dict(value)
    identities = selector.get("test_identities")
    if isinstance(identities, list):
        selector["test_identities"] = sorted(identities)
    return selector


def _runtime_bindings(
    *,
    mncs_test_runner: str,
    mncs_binary: str,
    mncs_test_libraries: list[str],
) -> dict[str, Any]:
    runner_identity = _path_identity(mncs_test_runner, kind="mncs-test-runner")
    binary_identity = _path_identity(mncs_binary, kind="mncs-binary")
    libraries = sorted(
        _path_identity(library, kind="mncs-library")
        for library in mncs_test_libraries
    )
    runtime_identity = sha256_hex(
        canonical_bytes(
            {
                "runner_identity": runner_identity,
                "mncs_binary_identity": binary_identity,
                "library_identities": libraries,
            }
        )
    )
    return {
        "runner_identity": runner_identity,
        "mncs_binary_identity": binary_identity,
        "library_identities": libraries,
        "runtime_identity": runtime_identity,
    }


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _repository_revision(checkout: Path, manifest_identity: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None:
        revision = result.stdout.strip()
        if REVISION_RE.fullmatch(revision):
            return revision
    # Synthetic canaries and source archives have no VCS identity.  Their
    # declaration identity remains an exact, conservative revision binding.
    return f"manifest:{manifest_identity}"


def _repository_checkout(workspace_root: Path, repository_id: str) -> Path | None:
    """Resolve one family checkout without making aliases part of proof identity."""

    exact = workspace_root / repository_id
    if exact.is_dir():
        return exact
    try:
        aliases = [
            candidate
            for candidate in workspace_root.iterdir()
            if candidate.is_dir() and candidate.name.casefold() == repository_id.casefold()
        ]
    except OSError as error:
        raise SelectiveFamilyError(
            f"cannot inspect family workspace for {repository_id}: {error}"
        ) from error
    if len(aliases) > 1:
        raise SelectiveFamilyError(
            f"family workspace has multiple aliases for {repository_id}"
        )
    return aliases[0] if aliases else None


def _check_manifest(checkout: Path, repository_id: str) -> tuple[Path, dict[str, Any]]:
    path = checkout / "family-verification-checks-v1.json"
    value = _read_json(path, f"{repository_id} verification manifest")
    return path, validate_verification_manifest(value, repository_id=repository_id)


def _native_source(checkout: Path, repository_id: str, selector: Mapping[str, Any]) -> str:
    """Resolve the bounded source mapping for a native mncs-test consumer.

    The family selector retains its historical manifest identity for graph
    compatibility.  Native execution consumes the source mapping declared by
    the repository's machine-readable userland status; it never reparses a
    TOML manifest or falls back to the Python runner.
    """

    status_path = checkout / "native-userland-status.json"
    if not status_path.is_file():
        raise SelectiveFamilyError(
            f"{repository_id} has no native-userland-status.json for native mncs-test"
        )
    status = _read_json(status_path, f"{repository_id} native userland status")
    sources = status.get("native_sources")
    manifest = selector.get("manifest")
    if not isinstance(sources, Mapping) or not isinstance(manifest, str):
        raise SelectiveFamilyError(
            f"{repository_id} native userland status has no source for selector manifest"
        )
    source = sources.get(manifest)
    if not isinstance(source, str) or not _safe_relative_path(source):
        raise SelectiveFamilyError(
            f"{repository_id} native source mapping is not a safe relative path"
        )
    source_path = checkout / source
    if not source_path.is_file():
        raise SelectiveFamilyError(
            f"{repository_id} native source is unavailable: {source}"
        )
    return source


def _run_native_mncs_call(
    *,
    mncs_binary: str,
    source_path: Path,
    function: str,
    arguments: str,
    cwd: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Invoke the generic MNCS application boundary.

    This adapter is transport only: the called MNCS module owns the semantic
    decision and receives an explicit structured-value identity grant. The
    historical ``mncs process`` wrapper is intentionally not part of the
    canonical path; it used to make Python the intermediary that launched a
    second Rust CLI and parsed its stdout.
    """

    library_arguments: list[str] = []
    configured = os.environ.get("MNCS_LIBRARY_PATH", "")
    library_roots = [Path(item) for item in configured.split(os.pathsep) if item]
    # Native family records live in Commons and the generic application
    # contract lives in the language repository. These are dependency roots,
    # not semantic projections; callers may still override them through the
    # environment when repositories are mounted elsewhere.
    for candidate in (
        cwd / "mncs-language" / "library",
        cwd.parent / "mncs-language" / "library",
        cwd / "MNCS-Commons" / "src" / "mncs_commons" / "mesh",
        cwd.parent / "MNCS-Commons" / "src" / "mncs_commons" / "mesh",
        # Actions imports the native test provider as a typed module. Keep
        # both the provider root and the repository root visible so its
        # `tests.self_suite` dependency resolves without a host projection.
        cwd / "mncs-test" / "native",
        cwd.parent / "mncs-test" / "native",
        cwd / "mncs-test",
        cwd.parent / "mncs-test",
    ):
        if candidate.is_dir() and candidate not in library_roots:
            library_roots.append(candidate)
    for library in library_roots:
        library_arguments.extend(("--library", str(library)))

    try:
        completed = subprocess.run(
            [
                mncs_binary,
                "call",
                str(source_path),
                "--module",
                "mncs.actions.family.v1",
                "--function",
                function,
                "--args-json",
                arguments,
                "--grant-structured",
                "actions_digest",
            ] + library_arguments,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=305,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SelectiveFamilyError(f"bounded native process launcher could not be started: {error}") from error
    if completed.returncode != 0:
        raise SelectiveFamilyError(
            "generic native application call failed: "
            + (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")
        )
    try:
        document = json.loads(completed.stdout)
        if not isinstance(document, Mapping):
            raise SelectiveFamilyError("generic native application call returned no document")
        return dict(document), {
            "schema_version": "mncs.application-call/1",
            "subprocess_count": 1,
            "transport": "generic-mncs-call",
        }
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SelectiveFamilyError(
            f"generic native application call returned invalid structured output: {error}"
        ) from error


def _byte_sequence(value: str) -> dict[str, Any]:
    """Encode one semantic identity as the typed 32-byte MNCS value."""

    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        value = sha256_hex(canonical_bytes(value))
    return {
        "sequence": {
            "values": [
                {"byte": {"value": byte}}
                for byte in bytes.fromhex(value)
            ]
        }
    }


def _finite_verdict(value: str) -> dict[str, Any]:
    return {"finite": {"type": "FamilyVerdict", "variant": value}}


def _typed_record(type_name: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    return {"record": {"type": type_name, "fields": dict(fields)}}


def _identity_value(value: Any) -> str:
    if isinstance(value, str) and SHA256_RE.fullmatch(value):
        return value
    return sha256_hex(canonical_bytes(value))


def _family_evidence(
    *,
    plan: Mapping[str, Any],
    graph_identity: str,
    edge: Mapping[str, Any],
    behavioral_result: Mapping[str, Any],
    behavioral_check: Mapping[str, Any],
    producer_repository: str,
    producer_repository_revision: str,
    prior_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project actual external documents into the native typed ABI.

    This is deliberately a structural codec. It hashes named identity
    documents only where the external contract uses a string identity; it
    never computes a validity or sufficiency boolean for the native module.

    The Commons-owned ``plan_id`` is the semantic plan identity. The
    whole-file ``plan_digest`` used by the surrounding compatibility flow is
    intentionally not projected here: it binds the external transport file,
    while native receipt material binds the canonical plan contract. This
    keeps incidental envelope metadata from changing semantic reuse and
    avoids treating a Python file hash as a family identity.
    """

    verification = edge.get("verification", {})
    selector = verification.get("selector", {}) if isinstance(verification, Mapping) else {}
    test_identity = _identity_value(
        _canonical_selector(selector).get("test_identities", [])
    )
    source_identity = _identity_value(plan.get("source", {}).get("sha256", ""))
    plan_value = _identity_value(plan.get("plan_id", ""))
    graph_value = _identity_value(graph_identity)
    edge_identity = _identity_value(edge.get("fingerprint", ""))
    contract_identity = _identity_value(edge.get("contract_identity", ""))
    consumer_identity = _identity_value(edge.get("consumer_manifest_identity", ""))
    family_identity = _identity_value(
        {"family": "mncs", "graph_identity": graph_identity}
    )
    check_identity = _identity_value(verification.get("check_identity", ""))
    producer_identity = _identity_value(producer_repository)
    producer_revision_identity = _identity_value(producer_repository_revision)
    test_result_identity = _identity_value(behavioral_result)
    check_result_identity = _identity_value(behavioral_check)
    # This is the owning Commons contract identity, not a digest of this
    # Python projection. Carrying it on every native result record prevents a
    # self-consistent projection from moving evidence between contracts.
    contract_value = contract_identity
    execution = behavioral_result.get("execution", {})
    execution_identity = _identity_value(
        execution.get("run_identity", execution) if isinstance(execution, Mapping) else execution
    )
    evidence_identity = _identity_value(
        {"test_result": test_result_identity, "check_result": check_result_identity}
    )
    material = {
        "family_identity": family_identity,
        "plan_identity": plan_value,
        "graph_identity": graph_value,
        "edge_identity": edge_identity,
        "source_identity": source_identity,
        "contract_identity": contract_value,
        "consumer_identity": consumer_identity,
        "test_result_identity": test_result_identity,
        "check_result_identity": check_result_identity,
        "receipt_identity": _identity_value(
            (prior_receipt or {}).get("receipt_identity", "")
        ),
        "execution_identity": execution_identity,
        "evidence_identity": evidence_identity,
        "verdict": str(behavioral_result.get("verdict", "UNKNOWN")),
    }
    prior = dict(prior_receipt) if isinstance(prior_receipt, Mapping) else {
        "receipt_identity": "0" * 64,
        "family_identity": "0" * 64,
        "plan_identity": "0" * 64,
        "graph_identity": "0" * 64,
        "edge_identity": "0" * 64,
        "source_identity": "0" * 64,
        "contract_identity": "0" * 64,
        "consumer_identity": "0" * 64,
        "test_result_identity": "0" * 64,
        "check_result_identity": "0" * 64,
        "execution_identity": "0" * 64,
        "evidence_identity": "0" * 64,
        "producer_identity": "0" * 64,
        "producer_revision_identity": "0" * 64,
        "verdict": "UNKNOWN",
    }
    # Receipts created before the Commons contract gained an explicit
    # contract binding remain readable only as non-reusable evidence. A zero
    # value makes the native decoder accept the transport shape while
    # receipt_matches rejects reuse fail-closed.
    prior.setdefault("contract_identity", "0" * 64)
    return {
        "plan": {
            "family_identity": family_identity,
            "plan_identity": plan_value,
            "graph_identity": graph_value,
            "edge_identity": edge_identity,
            "source_identity": source_identity,
            "contract_identity": contract_identity,
            "consumer_identity": consumer_identity,
            "test_identity": test_identity,
            "check_identity": check_identity,
        },
        "graph": {
            "family_identity": family_identity,
            "graph_identity": graph_value,
            "edge_identity": edge_identity,
            "source_identity": source_identity,
            "contract_identity": contract_identity,
            "consumer_identity": consumer_identity,
        },
        "edge": {
            "edge_identity": edge_identity,
            "source_identity": source_identity,
            "contract_identity": contract_identity,
            "consumer_identity": consumer_identity,
            "producer_identity": producer_identity,
            "producer_revision_identity": producer_revision_identity,
        },
        "test_result": {
            "result_identity": test_result_identity,
            "plan_identity": plan_value,
            "graph_identity": graph_value,
            "edge_identity": edge_identity,
            "source_identity": source_identity,
            "contract_identity": contract_value,
            "consumer_identity": consumer_identity,
            "test_identity": test_identity,
            "execution_identity": execution_identity,
            "evidence_identity": evidence_identity,
            "verdict": str(behavioral_result.get("verdict", "UNKNOWN")),
        },
        "check_result": {
            "result_identity": check_result_identity,
            "check_identity": check_identity,
            "plan_identity": plan_value,
            "graph_identity": graph_value,
            "edge_identity": edge_identity,
            "source_identity": source_identity,
            "contract_identity": contract_value,
            "consumer_identity": consumer_identity,
            "test_result_identity": test_result_identity,
            "evidence_identity": evidence_identity,
            "verdict": str(behavioral_check.get("verdict", "UNKNOWN")),
        },
        "prior_receipt": prior,
        "material": material,
    }


def _family_arguments(evidence: Mapping[str, Any]) -> str:
    def identity_fields(values: Mapping[str, Any], names: list[str]) -> dict[str, Any]:
        return {name: _byte_sequence(str(values[name])) for name in names}

    plan = evidence["plan"]
    graph = evidence["graph"]
    edge = evidence["edge"]
    test_result = evidence["test_result"]
    check_result = evidence["check_result"]
    prior = evidence["prior_receipt"]
    plan_names = [
        "family_identity", "plan_identity", "graph_identity", "edge_identity",
        "source_identity", "contract_identity", "consumer_identity",
        "test_identity", "check_identity",
    ]
    graph_names = [
        "family_identity", "graph_identity", "edge_identity", "source_identity",
        "contract_identity", "consumer_identity",
    ]
    edge_names = [
        "edge_identity", "source_identity", "contract_identity", "consumer_identity",
        "producer_identity", "producer_revision_identity",
    ]
    test_names = [
        "result_identity", "plan_identity", "graph_identity", "edge_identity",
        "source_identity", "contract_identity", "consumer_identity", "test_identity",
        "execution_identity", "evidence_identity",
    ]
    check_names = [
        "result_identity", "check_identity", "plan_identity", "graph_identity",
        "edge_identity", "source_identity", "contract_identity", "consumer_identity",
        "test_result_identity", "evidence_identity",
    ]
    receipt_names = [
        "receipt_identity", "family_identity", "plan_identity", "graph_identity",
        "edge_identity", "source_identity", "contract_identity", "consumer_identity",
        "test_result_identity", "check_result_identity", "execution_identity",
        "evidence_identity", "producer_identity", "producer_revision_identity",
    ]
    fields = {
        "plan": _typed_record("PlanEvidence", identity_fields(plan, plan_names)),
        "graph": _typed_record("GraphEvidence", identity_fields(graph, graph_names)),
        "edge": _typed_record("EdgeEvidence", identity_fields(edge, edge_names)),
        "test_result": _typed_record(
            "TestResultEvidence",
            {
                **identity_fields(test_result, test_names),
                "verdict": _finite_verdict(str(test_result["verdict"])),
            },
        ),
        "check_result": _typed_record(
            "CheckResultEvidence",
            {
                **identity_fields(check_result, check_names),
                "verdict": _finite_verdict(str(check_result["verdict"])),
            },
        ),
        "prior_receipt": _typed_record(
            "ReceiptEvidence",
            {
                **identity_fields(prior, receipt_names),
                "verdict": _finite_verdict(str(prior.get("verdict", "UNKNOWN"))),
            },
        ),
    }
    return json.dumps(
        [{"record": {"type": "FamilyCheckInput", "fields": fields}}],
        separators=(",", ":"),
    )


def _decode_typed_digest(value: Mapping[str, Any]) -> str:
    values = value.get("sequence", {}).get("values", [])
    try:
        return bytes(int(item["byte"]["value"]) for item in values).hex()
    except (KeyError, TypeError, ValueError):
        raise SelectiveFamilyError("native Actions returned a non-byte identity")


def _decode_typed_record(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = dict(value.get("record", {}).get("fields", []))
    decoded: dict[str, Any] = {}
    for name, item in fields.items():
        if "sequence" in item:
            decoded[name] = _decode_typed_digest(item)
        elif "boolean" in item:
            decoded[name] = bool(item["boolean"]["value"])
        elif "integer" in item:
            decoded[name] = int(item["integer"]["value"])
        elif "finite" in item:
            decoded[name] = str(item["finite"].get("variant_identity", "")).rsplit("::", 1)[-1]
        elif "record" in item:
            decoded[name] = _decode_typed_record(item)
        else:
            raise SelectiveFamilyError(f"native Actions returned an unsupported field {name}")
    return decoded


def _run_native_actions_family_check(
    *,
    mncs_binary: str,
    source_path: Path,
    cwd: Path,
    evidence: Mapping[str, Any] | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    """Run native Actions over actual typed family records.

    ``verdict`` is retained only as a compatibility-test convenience: when
    no evidence is supplied, a fully populated synthetic record set is built
    so the test still exercises the typed ABI. The campaign path always
    supplies evidence projected from real plan/graph/TestResult/CheckResult
    documents.
    """

    compatibility_fixture = evidence is None
    if evidence is None:
        marker = sha256_hex(canonical_bytes({"test": verdict or "PASS", "fixture": True}))
        synthetic = {
            "plan": {key: marker for key in (
                "family_identity", "plan_identity", "graph_identity", "edge_identity",
                "source_identity", "contract_identity", "consumer_identity", "test_identity",
                "check_identity",
            )},
            "graph": {key: marker for key in (
                "family_identity", "graph_identity", "edge_identity", "source_identity",
                "contract_identity", "consumer_identity",
            )},
            "edge": {key: marker for key in (
                "edge_identity", "source_identity", "contract_identity", "consumer_identity",
                "producer_identity", "producer_revision_identity",
            )},
            "test_result": {key: marker for key in (
                "result_identity", "plan_identity", "graph_identity", "edge_identity",
                "source_identity", "contract_identity", "consumer_identity", "test_identity", "execution_identity",
                "evidence_identity",
            )} | {"verdict": verdict or "PASS"},
            "check_result": {key: marker for key in (
                "result_identity", "check_identity", "plan_identity", "graph_identity",
                "edge_identity", "source_identity", "contract_identity", "consumer_identity", "test_result_identity",
                "evidence_identity",
            )} | {"verdict": verdict or "PASS"},
            "prior_receipt": {key: "0" * 64 for key in (
                "receipt_identity", "family_identity", "plan_identity", "graph_identity",
                "edge_identity", "source_identity", "contract_identity", "consumer_identity", "test_result_identity",
                "check_result_identity", "execution_identity", "evidence_identity",
                "producer_identity", "producer_revision_identity",
            )} | {"verdict": "UNKNOWN"},
        }
        evidence = synthetic
    arguments = _family_arguments(evidence)
    document, process_document = _run_native_mncs_call(
        mncs_binary=mncs_binary,
        source_path=source_path,
        function="family_check",
        arguments=arguments,
        cwd=cwd,
    )
    try:
        call = document["call"]
        returned = call["returned"]
        record = returned[0]["record"]
        decoded = _decode_typed_record({"record": record})
        native_verdict = str(decoded["verdict"])
        native_result = {
            "proof_sufficient": bool(decoded["proof_sufficient"]),
            "reusable": bool(decoded["reusable"]),
            "reason_code": int(decoded["reason_code"]),
            "receipt": decoded["receipt"],
            "proof_identity": decoded["proof_identity"],
            "family_result": decoded,
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SelectiveFamilyError(
            f"native Actions semantic core returned an invalid typed result: {error}"
        ) from error
    if compatibility_fixture and verdict is not None and native_verdict != verdict:
        raise SelectiveFamilyError(
            f"native Actions semantic core verdict {native_verdict} disagrees with mncs-test {verdict}"
        )
    return {
        "schema_version": "mncs-actions.native-family-check/1",
        "authority": "mncs.actions.family.v1",
        "module": "mncs.actions.family.v1",
        "function": "family_check",
        "verdict": native_verdict,
        **native_result,
        "artifact_identity": call.get("artifact_identity"),
        "artifact_sha256": call.get("artifact_sha256"),
        "steps": call.get("steps"),
        "process": {
            "schema_version": process_document.get("schema_version"),
            "subprocess_count": process_document.get("subprocess_count", 1),
            "transport": process_document.get("transport", "generic-mncs-call"),
        },
    }


def _run_native_actions_selected_proof(
    *,
    mncs_binary: str,
    source_path: Path,
    records: list[Mapping[str, Any]],
    cwd: Path,
    strict_native: bool = False,
) -> dict[str, Any]:
    """Ask the native reducer to aggregate actual consumer proof records.

    The source ABI is a bounded dynamic sequence (`up_to 64`). A set larger
    than that is sent as an explicit overflow refusal; it is never truncated
    or padded with synthetic PASS observations.
    """

    observations: list[dict[str, Any]] = []
    for record in records if len(records) <= 64 else []:
        family_result = record.get("native_family_result")
        if not isinstance(family_result, Mapping):
            if strict_native:
                raise SelectiveFamilyError(
                    "canonical Actions proof input is missing a native family result"
                )
            native = _run_native_actions_family_check(
                mncs_binary=mncs_binary,
                source_path=source_path,
                verdict=str(record.get("verdict", "UNKNOWN")),
                cwd=cwd,
            )
            family_result = native["family_result"]
        receipt = family_result.get("receipt")
        if not isinstance(receipt, Mapping):
            raise SelectiveFamilyError("native family result omitted its receipt record")
        material = {
            "family_identity": receipt["family_identity"],
            "plan_identity": family_result["plan_identity"],
            "graph_identity": family_result["graph_identity"],
            "edge_identity": family_result["edge_identity"],
            "source_identity": family_result["source_identity"],
            "contract_identity": family_result["contract_identity"],
            "consumer_identity": family_result["consumer_identity"],
            "test_result_identity": family_result["test_result_identity"],
            "check_result_identity": family_result["check_result_identity"],
            "receipt_identity": receipt["receipt_identity"],
            "execution_identity": family_result["execution_identity"],
            "evidence_identity": family_result["evidence_identity"],
            "verdict": family_result["verdict"],
        }
        observations.append(
            _typed_record(
                "ConsumerObservation",
                {
                    "material": _typed_record(
                        "ProofMaterial",
                        {
                            **{
                                key: _byte_sequence(str(value))
                                for key, value in material.items()
                                if key != "verdict"
                            },
                            "verdict": _finite_verdict(str(material["verdict"])),
                        },
                    ),
                    "proof_identity": _byte_sequence(str(family_result["proof_identity"])),
                },
            )
        )
    arguments = json.dumps(
        [
            {
                "record": {
                    "type": "SelectedProofInput",
                    "fields": {
                        "observations": {"sequence": {"values": observations}},
                        "count": {"integer": {"value": len(records)}},
                        "overflow": {"boolean": {"value": len(records) > 64}},
                    },
                }
            }
        ],
        separators=(",", ":"),
    )
    document, process_document = _run_native_mncs_call(
        mncs_binary=mncs_binary,
        source_path=source_path,
        function="selected_proof",
        arguments=arguments,
        cwd=cwd,
    )
    try:
        call = document["call"]
        fields = dict(call["returned"][0]["record"]["fields"])
        native_verdict = str(
            fields["verdict"]["finite"]["variant_identity"]
        ).rsplit("::", 1)[-1]
        native_result = {
            "total": fields["total"]["integer"]["value"],
            "passed": fields["passed"]["integer"]["value"],
            "failed": fields["failed"]["integer"]["value"],
            "unknown": fields["unknown"]["integer"]["value"],
            "invalid": fields["invalid"]["integer"]["value"],
            "reusable": fields["reusable"]["boolean"]["value"],
            "reason_code": fields["reason_code"]["integer"]["value"],
            "proof_identity": _decode_typed_digest(fields["proof_identity"]),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SelectiveFamilyError(
            f"native selected-proof reducer returned an invalid typed result: {error}"
        ) from error
    return {
        "schema_version": "mncs-actions.native-selective-proof/1",
        "authority": "mncs.actions.family.v1",
        "module": "mncs.actions.family.v1",
        "function": "selected_proof",
        "verdict": native_verdict,
        **native_result,
        "artifact_identity": call.get("artifact_identity"),
        "artifact_sha256": call.get("artifact_sha256"),
        "steps": call.get("steps"),
        "process": {
            "schema_version": process_document.get("schema_version"),
            "subprocess_count": process_document.get("subprocess_count", 1),
            "transport": process_document.get("transport", "generic-mncs-call"),
        },
    }


def _matching_edge(
    plan: Mapping[str, Any], graph: Mapping[str, Any], edge: Mapping[str, Any]
) -> dict[str, Any]:
    fingerprint = edge.get("fingerprint")
    if not isinstance(fingerprint, str):
        raise SelectiveFamilyError("plan edge has no fingerprint")
    candidates = [
        candidate
        for candidate in graph.get("edges", [])
        if isinstance(candidate, Mapping) and candidate.get("fingerprint") == fingerprint
    ]
    if len(candidates) != 1 or dict(candidates[0]) != dict(edge):
        raise SelectiveFamilyError(
            f"plan edge {fingerprint} is not the exact edge in the canonical graph"
        )
    return dict(candidates[0])


def _producer_binding(
    graph: Mapping[str, Any], edges: list[Mapping[str, Any]], workspace_root: Path
) -> dict[str, Any]:
    producer_ids = {edge.get("producer_repository") for edge in edges}
    if len(producer_ids) != 1 or not all(isinstance(item, str) and item for item in producer_ids):
        raise SelectiveFamilyError("selected edges do not have one producer repository")
    producer_id = next(iter(producer_ids))
    repositories = [
        repository
        for repository in graph.get("repositories", [])
        if isinstance(repository, Mapping) and repository.get("id") == producer_id
    ]
    if len(repositories) != 1:
        raise SelectiveFamilyError(f"canonical graph has no unique producer row for {producer_id}")
    declaration_revision = repositories[0].get("revision")
    if not isinstance(declaration_revision, str) or not declaration_revision:
        raise SelectiveFamilyError(f"producer {producer_id} has no repository revision")
    manifest_identities = {edge.get("producer_manifest_identity") for edge in edges}
    evidence_digests = {
        edge.get("provider_evidence_sha256")
        for edge in edges
        if isinstance(edge.get("provider_evidence_sha256"), str)
    }
    if len(manifest_identities) != 1 or not all(
        isinstance(item, str) and item for item in manifest_identities
    ):
        raise SelectiveFamilyError("selected edges do not share one producer declaration identity")
    if not evidence_digests:
        raise SelectiveFamilyError("selected edges carry no producer evidence digest")
    manifest_identity = next(iter(manifest_identities))
    checkout = _repository_checkout(workspace_root, producer_id)
    repository_revision = (
        _repository_revision(checkout, manifest_identity)
        if checkout
        else f"manifest:{manifest_identity}"
    )
    revision_kind = "git" if REVISION_RE.fullmatch(repository_revision) else "manifest_identity"
    return {
        "repository": producer_id,
        "declaration_revision": declaration_revision,
        "repository_revision": repository_revision,
        "repository_revision_kind": revision_kind,
        "manifest_identity": manifest_identity,
        "evidence_sha256": sorted(evidence_digests),
    }


def _consumer_check(
    *,
    checkout: Path,
    repository_id: str,
    edge: Mapping[str, Any],
    plan: Mapping[str, Any],
    plan_digest: str,
    graph_identity: str,
    graph_digest: str,
    producer_repository_revision: str,
    mncs_test_runner: str,
    mncs_binary: str,
    mncs_test_libraries: list[str],
    runtime_bindings: Mapping[str, Any],
    native_actions_shadow_source: Path | None,
) -> tuple[dict[str, Any], str]:
    manifest_path, manifest = _check_manifest(checkout, repository_id)
    verification = edge.get("verification")
    if not isinstance(verification, Mapping):
        raise SelectiveFamilyError("selected edge has no verification identity")
    check_identity = verification.get("check_identity")
    if not isinstance(check_identity, str) or not check_identity:
        raise SelectiveFamilyError("selected edge verification identity is invalid")
    checks = [check for check in manifest["checks"] if check["identity"] == check_identity]
    if len(checks) != 1:
        raise SelectiveFamilyError(
            f"{repository_id} does not declare exactly one {check_identity} check"
        )
    check = checks[0]
    if check["contract_identity"] != edge["contract_identity"]:
        raise SelectiveFamilyError(
            f"{check_identity} does not verify {edge['contract_identity']}"
        )
    if verification.get("surface") != check["surface"]:
        raise SelectiveFamilyError(f"{check_identity} surface disagrees with the graph edge")
    if verification.get("runner") != check["runner"]:
        raise SelectiveFamilyError(f"{check_identity} runner disagrees with the graph edge")
    if check["runner"] == "mncs-test":
        edge_selector = verification.get("selector")
        check_selector = check.get("selector")
        if not isinstance(edge_selector, Mapping) or not isinstance(check_selector, Mapping):
            raise SelectiveFamilyError(f"{check_identity} has no complete mncs-test selector")
        if _canonical_selector(edge_selector) != _canonical_selector(check_selector):
            raise SelectiveFamilyError(f"{check_identity} selector disagrees with the canonical graph edge")
    if verification.get("evidence") != manifest_path.name:
        raise SelectiveFamilyError(
            f"{check_identity} verification evidence is not the repository manifest"
        )

    declaration_path = checkout / "family-semantic-contracts-v1.json"
    declaration = _read_json(declaration_path, f"{repository_id} declaration")
    bound = bind_declaration_evidence(checkout, declaration)
    if declaration_identity(bound) != edge.get("consumer_manifest_identity"):
        raise SelectiveFamilyError(f"{repository_id} declaration identity is stale")
    consumes = [
        entry
        for entry in bound["consumes"]
        if entry["contract_identity"] == edge["contract_identity"]
        and entry["consumer_identity"] == edge["consuming_identity"]
    ]
    if len(consumes) != 1:
        raise SelectiveFamilyError(
            f"{repository_id} does not declare the selected consumer identity"
        )
    consumer_entry = consumes[0]
    consume_digest = bound["_evidence_digests"]["consumes"][consumer_entry["evidence"]]
    if consume_digest != edge.get("consumer_evidence_sha256"):
        raise SelectiveFamilyError(f"{repository_id} consumer evidence is stale")
    verification_digest = bound["_evidence_digests"]["verification"][manifest_path.name]
    if verification_digest != edge.get("verification_evidence_sha256"):
        raise SelectiveFamilyError(f"{repository_id} verification surface is stale")

    repository_revision = _repository_revision(
        checkout, str(edge["consumer_manifest_identity"])
    )
    if check["runner"] == "mncs-test":
        selector = check.get("selector")
        if not isinstance(selector, Mapping):
            raise SelectiveFamilyError(f"{check_identity} has no mncs-test selector")
        request = {
            "schema_version": "mncs.family-check-request/1",
            "check_identity": check_identity,
            "contract_identity": edge["contract_identity"],
            "contract_revision": edge["contract_revision"],
            "verification_plan_id": plan["plan_id"],
            "family_graph_identity": graph_identity,
            "edge_fingerprint": edge["fingerprint"],
            "source_change_sha256": plan["source"]["sha256"],
        }
        with tempfile.TemporaryDirectory(prefix="mncs-actions-family-check-") as directory:
            request_path = Path(directory) / "family-check-request.json"
            _write_json(request_path, request)
            runner_path = Path(mncs_test_runner)
            native_runner = runner_path.suffix != ".py"
            if native_runner:
                source = _native_source(checkout, repository_id, selector)
                native_result_path = Path(directory) / "native-test-result.json"
                native_check_path = Path(directory) / "native-check-result.json"
                native_artifacts = Path(directory) / "native-artifacts"
                command = [
                    mncs_test_runner,
                    source,
                    "--result",
                    str(native_result_path),
                    "--check-result",
                    str(native_check_path),
                    "--artifacts",
                    str(native_artifacts),
                ]
                for identity in selector["test_identities"]:
                    command.extend(["--test-identity", identity])
            else:
                # Python remains an explicit compatibility/oracle path.  The
                # default family runner is the native launcher above; callers
                # must opt into this branch by naming a .py runner directly.
                command = [sys.executable, str(runner_path)]
                command.extend(
                    [
                        "run-check",
                        "--request",
                        str(request_path),
                        "--checks",
                        str(manifest_path),
                        "--repository-id",
                        repository_id,
                        "--mncs",
                        mncs_binary,
                    ]
                )
            for library in mncs_test_libraries:
                command.extend(["--library", library])
            environment = os.environ.copy()
            if native_runner:
                environment["MNCS"] = mncs_binary
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(checkout),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=300,
                    env=environment,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise SelectiveFamilyError(
                    f"{repository_id} mncs-test runner could not be started: {error}"
                ) from error
            if native_runner:
                if not native_result_path.is_file() or not native_check_path.is_file():
                    raise SelectiveFamilyError(
                        f"{repository_id} native mncs-test produced no structured result: "
                        + (completed.stderr.strip() or f"exit {completed.returncode}")
                    )
                behavioral_result = _read_json(
                    native_result_path, f"{repository_id} native TestResult"
                )
                native_check = _read_json(
                    native_check_path, f"{repository_id} native CheckResult"
                )
                verdict = behavioral_result.get("verdict")
                if verdict not in {"PASS", "FAIL", "UNKNOWN"}:
                    raise SelectiveFamilyError(
                        f"{repository_id} native mncs-test returned an invalid verdict"
                    )
                execution = behavioral_result.get("execution")
                if not isinstance(execution, Mapping):
                    raise SelectiveFamilyError(
                        f"{repository_id} native mncs-test omitted execution identity"
                    )
                execution = dict(execution)
                execution["check_definition_identity"] = sha256_hex(
                    canonical_bytes(check)
                )
                behavioral_result = dict(behavioral_result)
                behavioral_result["execution"] = execution
                native_result_digest = sha256_hex(canonical_bytes(behavioral_result))
                native_check_digest = sha256_hex(canonical_bytes(native_check))
                response = {
                    "schema_version": "mncs.family-check-response/1",
                    "check_identity": check_identity,
                    "contract_identity": edge["contract_identity"],
                    "contract_revision": edge["contract_revision"],
                    "runner": "mncs-test",
                    "verdict": verdict,
                    "family_binding": {
                        "verification_plan_id": plan["plan_id"],
                        "family_graph_identity": graph_identity,
                        "edge_fingerprint": edge["fingerprint"],
                        "source_change_sha256": plan["source"]["sha256"],
                    },
                    "check_result": {
                        "schema_version": "mncs.check-result/1",
                        "id": check_identity,
                        "provider": repository_id,
                        "verdict": verdict,
                        "scope": check["surface"],
                        "claim": "selected consumer behavioral proof established by native mncs-test",
                        "summary": native_check.get(
                            "summary", "native mncs-test behavioral check"
                        ),
                        "contract_revision": edge["contract_revision"],
                        "producer_revision": plan["source"]["sha256"],
                        "references": [
                            {
                                "kind": "mncs-test-check-result",
                                "uri": f"urn:mncs-test-check:{check_identity}",
                                "digest": "sha256:" + native_check_digest,
                            },
                            {
                                "kind": "mncs-test-result",
                                "uri": f"urn:mncs-test:{behavioral_result.get('run_id', native_result_digest)}",
                                "digest": "sha256:" + native_result_digest,
                            },
                        ],
                    },
                    "test_result": behavioral_result,
                    "execution": execution,
                }
            else:
                try:
                    response = json.loads(completed.stdout)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise SelectiveFamilyError(
                        f"{repository_id} mncs-test runner returned no structured family-check response: "
                        + (completed.stderr.strip() or str(error))
                    ) from error
        if not isinstance(response, Mapping) or response.get("schema_version") != "mncs.family-check-response/1":
            raise SelectiveFamilyError(f"{repository_id} mncs-test runner response has an unsupported schema")
        if response.get("check_identity") != check_identity:
            raise SelectiveFamilyError(f"{repository_id} mncs-test runner response identity disagrees")
        if response.get("contract_identity") != edge["contract_identity"]:
            raise SelectiveFamilyError(f"{repository_id} mncs-test runner contract identity disagrees")
        if response.get("contract_revision") != edge["contract_revision"]:
            raise SelectiveFamilyError(f"{repository_id} mncs-test runner contract revision disagrees")
        if response.get("runner") != "mncs-test" or response.get("verdict") not in {"PASS", "FAIL", "UNKNOWN"}:
            raise SelectiveFamilyError(f"{repository_id} mncs-test runner response verdict is invalid")
        binding = response.get("family_binding")
        if not isinstance(binding, Mapping):
            raise SelectiveFamilyError(f"{repository_id} mncs-test runner omitted family bindings")
        for key, expected in (
            ("verification_plan_id", plan["plan_id"]),
            ("family_graph_identity", graph_identity),
            ("edge_fingerprint", edge["fingerprint"]),
            ("source_change_sha256", plan["source"]["sha256"]),
        ):
            if binding.get(key) != expected:
                raise SelectiveFamilyError(f"{repository_id} mncs-test runner {key} disagrees")
        behavioral_check = response.get("check_result")
        behavioral_result = response.get("test_result")
        if not isinstance(behavioral_check, Mapping) or not isinstance(behavioral_result, Mapping):
            verdict = response.get("verdict") if response.get("verdict") in {"FAIL", "UNKNOWN"} else "UNKNOWN"
            result = {
                "schema_version": "mncs.check-result/1",
                "id": check_identity,
                "provider": repository_id,
                "verdict": verdict,
                "scope": check["surface"],
                "claim": "selected consumer behavioral proof",
                "summary": str(response.get("failure", {}).get("message", "mncs-test returned no result")),
                "contract_revision": edge["contract_revision"],
                "producer_revision": plan["source"]["sha256"],
                "producer_repository_revision": producer_repository_revision,
                "references": [],
                "behavioral": {
                    "runner": "mncs-test",
                    "selector": dict(selector),
                    **dict(runtime_bindings),
                },
            }
            return result, repository_revision
        errors = validate_check_result(dict(behavioral_check))
        if errors:
            raise SelectiveFamilyError(
                f"{repository_id} mncs-test CheckResult is invalid: {'; '.join(errors)}"
            )
        if behavioral_check.get("id") != check_identity:
            raise SelectiveFamilyError(f"{repository_id} mncs-test CheckResult identity disagrees")
        if behavioral_check.get("verdict") != response.get("verdict"):
            raise SelectiveFamilyError(f"{repository_id} mncs-test CheckResult verdict disagrees")
        if behavioral_result.get("schema_version") != "mncs.test-result/1":
            raise SelectiveFamilyError(f"{repository_id} mncs-test TestResult schema is invalid")
        if behavioral_result.get("verdict") != response.get("verdict"):
            raise SelectiveFamilyError(f"{repository_id} mncs-test TestResult verdict disagrees")
        execution = response.get("execution")
        if not isinstance(execution, Mapping):
            raise SelectiveFamilyError(f"{repository_id} mncs-test omitted execution identity")
        selected_tests = execution.get("test_case_identities", [])
        if sorted(selected_tests) != sorted(selector["test_identities"]):
            raise SelectiveFamilyError(f"{repository_id} mncs-test did not execute the exact declared tests")
        if not isinstance(execution.get("runner_version"), str) or not execution.get("runner_version"):
            raise SelectiveFamilyError(f"{repository_id} mncs-test runner version is missing")
        if not isinstance(execution.get("run_identity"), str) or not execution.get("run_identity"):
            raise SelectiveFamilyError(f"{repository_id} mncs-test run identity is missing")
        if execution.get("inventory_identity") != selector.get("inventory_identity"):
            raise SelectiveFamilyError(f"{repository_id} mncs-test inventory identity disagrees")
        expected_check_definition = sha256_hex(canonical_bytes(check))
        if execution.get("check_definition_identity") != expected_check_definition:
            raise SelectiveFamilyError(f"{repository_id} mncs-test check definition identity disagrees")
        selection_projection = behavioral_result.get("selection")
        selected_result_tests = (
            selection_projection.get("selected_test_identities", [])
            if isinstance(selection_projection, Mapping)
            else []
        )
        if sorted(selected_result_tests) != sorted(selector["test_identities"]):
            raise SelectiveFamilyError(f"{repository_id} mncs-test TestResult selection disagrees")
        behavioral_result_digest = sha256_hex(canonical_bytes(behavioral_result))
        test_result_ref = {
            "kind": "mncs-test-result",
            "uri": f"urn:mncs-test:{behavioral_result.get('run_id', '')}",
            "digest": "sha256:" + behavioral_result_digest,
        }
        check_ref = behavioral_check.get("result_ref")
        references = [test_result_ref]
        if isinstance(check_ref, Mapping) and isinstance(check_ref.get("uri"), str) and isinstance(check_ref.get("digest"), str):
            references.insert(
                0,
                {
                    "kind": "mncs-test-check-result",
                    "uri": check_ref["uri"],
                    "digest": check_ref["digest"],
                },
            )
        native_actions = None
        native_evidence = None
        if native_actions_shadow_source is not None:
            native_evidence = _family_evidence(
                plan=plan,
                graph_identity=graph_identity,
                edge=edge,
                behavioral_result=behavioral_result,
                behavioral_check=behavioral_check,
                producer_repository=str(edge.get("producer_repository", "")),
                producer_repository_revision=producer_repository_revision,
            )
            native_actions = _run_native_actions_family_check(
                mncs_binary=mncs_binary,
                source_path=native_actions_shadow_source,
                evidence=native_evidence,
                cwd=checkout,
            )
            # The native family result is authoritative for the selected
            # family boundary. The host keeps the provider documents as
            # evidence, but does not reinterpret their verdict.
            response = dict(response)
            response["verdict"] = native_actions["verdict"]
            behavioral_check = dict(behavioral_check)
            behavioral_check["verdict"] = native_actions["verdict"]
            response["check_result"] = behavioral_check
            behavioral_result = dict(behavioral_result)
            behavioral_result["verdict"] = native_actions["verdict"]
            response["test_result"] = behavioral_result
        result = {
            "schema_version": "mncs.check-result/1",
            "id": check_identity,
            "provider": repository_id,
            "verdict": response.get("verdict", "UNKNOWN"),
            "scope": check["surface"],
            "claim": "selected consumer behavioral proof established by mncs-test",
            "summary": behavioral_check.get("summary", "mncs-test behavioral check"),
            "contract_revision": edge["contract_revision"],
            "producer_revision": plan["source"]["sha256"],
            "producer_repository_revision": producer_repository_revision,
            "references": references,
            "behavioral": {
                "runner": "mncs-test",
                "runner_version": execution.get("runner_version") if isinstance(execution, Mapping) else None,
                "selector": dict(selector),
                "test_case_identities": selected_tests,
                "run_identity": execution.get("run_identity") if isinstance(execution, Mapping) else None,
                "inventory_identity": execution.get("inventory_identity") if isinstance(execution, Mapping) else None,
                **dict(runtime_bindings),
                "test_result": dict(behavioral_result),
                "actions_native": native_actions,
            },
        }
        if native_actions is not None:
            result["native_receipt"] = native_actions["receipt"]
            result["native_family_result"] = native_actions["family_result"]
            result["native_evidence"] = native_evidence
        return result, repository_revision
    result = {
        "schema_version": "mncs.check-result/1",
        "id": check_identity,
        "provider": repository_id,
        "verdict": "PASS",
        "scope": check["surface"],
        "claim": "selected consumer declaration and verification surface match the canonical edge",
        "summary": "Contract-scoped consumer proof established without a repository-wide suite.",
        "contract_revision": edge["contract_revision"],
        "producer_revision": plan["source"]["sha256"],
        "producer_repository_revision": producer_repository_revision,
        "references": [
            {
                "kind": "verification-plan",
                "uri": f"mncs:verification-plan:{plan['plan_id']}",
                "digest": "sha256:" + plan_digest,
                "contract_revision": plan["schema_version"],
            },
            {
                "kind": "family-edge",
                "uri": f"mncs:family-edge:{edge['fingerprint']}",
                "digest": "sha256:" + edge["fingerprint"],
                "contract_revision": edge["contract_revision"],
            },
        ],
    }
    return result, repository_revision


def _write_consumer_evidence(
    *,
    output_dir: Path,
    repository_id: str,
    result: Mapping[str, Any],
    plan: Mapping[str, Any],
    plan_digest: str,
    graph_digest: str,
    edge: Mapping[str, Any],
    repository_revision: str,
    producer_repository_revision: str,
    runtime_bindings: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_dir = output_dir / "evidence" / repository_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    check_path = evidence_dir / "check-result.json"
    _write_json(check_path, result)
    check_digest = _file_digest(check_path)
    inputs = {
        "verification_plan_id": str(plan["plan_id"]),
        "verification_plan_sha256": plan_digest,
        "family_graph_identity": str(plan["impact"]["cross_repository"]["graph_identity"]),
        "family_graph_sha256": graph_digest,
        "edge_fingerprint": str(edge["fingerprint"]),
        "consumer_manifest_identity": str(edge["consumer_manifest_identity"]),
        "consumer_repository_revision": repository_revision,
        "producer_repository_revision": producer_repository_revision,
        "runner": str(result.get("behavioral", {}).get("runner", "declaration")),
        "check_definition_identity": str(edge["verification_evidence_sha256"]),
        "selected_test_identities": json.dumps(
            list(result.get("behavioral", {}).get("test_case_identities", [])),
            separators=(",", ":"),
        ),
        "test_run_identity": str(result.get("behavioral", {}).get("run_identity", "")),
        "test_inventory_identity": str(result.get("behavioral", {}).get("inventory_identity", "")),
    }
    for key in (
        "runner_identity",
        "mncs_binary_identity",
        "runtime_identity",
    ):
        inputs[key] = str(result.get("behavioral", {}).get(key, runtime_bindings.get(key, "")))
    inputs["library_identities"] = json.dumps(
        list(result.get("behavioral", {}).get("library_identities", runtime_bindings.get("library_identities", []))),
        separators=(",", ":"),
    )
    runner = str(result.get("behavioral", {}).get("runner", "declaration"))
    receipt = build_execution_receipt(
        command=f"{runner} family-check {result['id']}",
        command_exit_code=0 if result["verdict"] == "PASS" else 1,
        claim_status=CLAIM_ESTABLISHED,
        result_path=str(check_path),
        result_present=True,
        result_valid=True,
        claim_verdict=str(result["verdict"]),
        produced_files=[{"path": check_path.name, "sha256": check_digest}],
        inputs=inputs,
    )
    native_receipt = result.get("native_receipt")
    native_family_result = result.get("native_family_result")
    if isinstance(native_receipt, Mapping) and isinstance(native_family_result, Mapping):
        # The generic execution-receipt schema remains the external adapter
        # format. Its semantic identity and dependency set come from the
        # native Actions receipt; Python contributes only transport metadata
        # needed by GitHub/artifact consumers.
        receipt["native_authority"] = "mncs.actions.family.v1"
        receipt["native_receipt"] = dict(native_receipt)
        receipt["native_receipt_identity"] = native_receipt.get("receipt_identity")
        receipt["native_proof_identity"] = native_family_result.get("proof_identity")
        receipt["native_verdict"] = native_family_result.get("verdict")
    receipt_path = evidence_dir / "execution-receipt.json"
    _write_json(receipt_path, receipt)
    receipt_digest = _file_digest(receipt_path)
    manifest = build_evidence_manifest(
        verdict=str(result["verdict"]),
        result_sha256=check_digest,
        result_filename=check_path.name,
        command_exit_code=0 if result["verdict"] == "PASS" else 1,
        kind="selective-consumer-check",
        receipt_ref={"path": receipt_path.name, "sha256": receipt_digest},
        references=list(result.get("references", [])),
        boundary={
            "check_id": result["id"],
            "provider": repository_id,
            "routing_scope": "selected_repositories",
            "consumer_repository": repository_id,
            "edge_fingerprint": edge["fingerprint"],
            "verification_plan_id": plan["plan_id"],
        },
    )
    manifest_path = evidence_dir / "evidence-manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "repository": repository_id,
        "check_identity": result["id"],
        "surface": result["scope"],
        "verdict": result["verdict"],
        "status": "generated",
        "repository_revision": repository_revision,
        "producer_repository_revision": producer_repository_revision,
        "consumer_manifest_identity": edge["consumer_manifest_identity"],
        "consumer_evidence_sha256": edge["consumer_evidence_sha256"],
        "verification_evidence_sha256": edge["verification_evidence_sha256"],
        "edge_fingerprint": edge["fingerprint"],
        "contract_revision": edge["contract_revision"],
        "check_digest": check_digest,
        "evidence_directory": f"evidence/{repository_id}",
        "runner": runner,
        "check_definition_identity": edge["verification_evidence_sha256"],
        "selected_test_identities": list(
            result.get("behavioral", {}).get("test_case_identities", [])
        ),
        "test_run_identity": result.get("behavioral", {}).get("run_identity"),
        "test_inventory_identity": result.get("behavioral", {}).get("inventory_identity"),
        "runner_version": result.get("behavioral", {}).get("runner_version"),
        "runner_identity": result.get("behavioral", {}).get("runner_identity"),
        "mncs_binary_identity": result.get("behavioral", {}).get("mncs_binary_identity"),
        "library_identities": list(result.get("behavioral", {}).get("library_identities", [])),
        "runtime_identity": result.get("behavioral", {}).get("runtime_identity"),
        "native_receipt": result.get("native_receipt"),
        "native_family_result": result.get("native_family_result"),
        "native_evidence": result.get("native_evidence"),
    }


def _prior_document(path: Path | None) -> tuple[Path, dict[str, Any]] | None:
    if path is None:
        return None
    proof_path = path / "composite-proof.json" if path.is_dir() else path
    if not proof_path.is_file():
        return None
    try:
        value = _read_json(proof_path, "prior composite proof")
        identity = value.get("proof_identity")
        core = copy.deepcopy(value)
        core.pop("proof_identity", None)
        if not isinstance(identity, str) or identity != sha256_hex(canonical_bytes(core)):
            return None
        return proof_path.parent, value
    except (SelectiveFamilyError, OSError, ValueError):
        return None


def _reuse_consumer(
    *,
    prior: tuple[Path, dict[str, Any]] | None,
    output_dir: Path,
    repository_id: str,
    edge: Mapping[str, Any],
    plan: Mapping[str, Any],
    repository_revision: str,
    producer_repository_revision: str,
    runtime_bindings: Mapping[str, Any],
    plan_digest: str,
    graph_identity: str,
    mncs_binary: str,
    native_actions_shadow_source: Path | None,
    workspace_checkout: Path,
) -> dict[str, Any] | None:
    if prior is None:
        return None
    prior_root, document = prior
    if document.get("status") != "PASS":
        return None
    if document.get("plan_id") != plan["plan_id"] or document.get("graph_identity") != plan["impact"]["cross_repository"]["graph_identity"]:
        return None
    records = document.get("consumers")
    if not isinstance(records, list):
        return None
    match = next(
        (
            record
            for record in records
            if isinstance(record, Mapping) and record.get("repository") == repository_id
        ),
        None,
    )
    if not isinstance(match, Mapping) or match.get("status") not in ("generated", "reused"):
        return None
    for field, expected in (
        ("edge_fingerprint", edge["fingerprint"]),
        ("consumer_manifest_identity", edge["consumer_manifest_identity"]),
        ("consumer_evidence_sha256", edge["consumer_evidence_sha256"]),
        ("verification_evidence_sha256", edge["verification_evidence_sha256"]),
        ("contract_revision", edge["contract_revision"]),
        ("repository_revision", repository_revision),
        ("producer_repository_revision", producer_repository_revision),
        ("runner", edge.get("verification", {}).get("runner", "declaration")),
        ("check_definition_identity", edge.get("verification_evidence_sha256")),
    ):
        if match.get(field) != expected:
            return None
    runner = edge.get("verification", {}).get("runner", "declaration")
    if runner == "mncs-test":
        for key in (
            "runner_identity",
            "mncs_binary_identity",
            "runtime_identity",
        ):
            if match.get(key) != runtime_bindings.get(key):
                return None
        if match.get("library_identities") != runtime_bindings.get("library_identities"):
            return None
        selector = edge.get("verification", {}).get("selector")
        if not isinstance(selector, Mapping):
            return None
        if match.get("selected_test_identities") != _canonical_selector(selector).get("test_identities"):
            return None
        expected_inventory = selector.get("inventory_identity")
        if expected_inventory is not None and match.get("test_inventory_identity") != expected_inventory:
            return None
    relative = match.get("evidence_directory")
    if not isinstance(relative, str) or not _safe_relative_path(relative):
        return None
    prior_dir = prior_root / relative
    required = {
        "check-result.json": match.get("check_digest"),
        "execution-receipt.json": None,
        "evidence-manifest.json": None,
    }
    for name, expected_digest in required.items():
        source = prior_dir / name
        if not source.is_file():
            return None
        if expected_digest is not None and _file_digest(source) != expected_digest:
            return None
    receipt = _read_json(prior_dir / "execution-receipt.json", "prior consumer execution receipt")
    if validate_execution_receipt(receipt):
        return None
    if receipt.get("claim_status") != CLAIM_ESTABLISHED:
        return None
    claim = receipt.get("claim")
    if not isinstance(claim, Mapping) or claim.get("verdict") != "PASS":
        return None
    receipt_inputs = receipt.get("inputs")
    if not isinstance(receipt_inputs, Mapping):
        return None
    expected_inputs = {
        "verification_plan_id": str(plan["plan_id"]),
        "family_graph_identity": str(plan["impact"]["cross_repository"]["graph_identity"]),
        "edge_fingerprint": str(edge["fingerprint"]),
        "consumer_manifest_identity": str(edge["consumer_manifest_identity"]),
        "consumer_repository_revision": str(repository_revision),
        "producer_repository_revision": str(producer_repository_revision),
        "runner": str(runner),
        "check_definition_identity": str(edge["verification_evidence_sha256"]),
    }
    if runner == "mncs-test":
        expected_inputs.update(
            {
                "test_run_identity": str(match.get("test_run_identity") or ""),
                "test_inventory_identity": str(match.get("test_inventory_identity") or ""),
                "runner_identity": str(runtime_bindings["runner_identity"]),
                "mncs_binary_identity": str(runtime_bindings["mncs_binary_identity"]),
                "runtime_identity": str(runtime_bindings["runtime_identity"]),
                "library_identities": json.dumps(
                    list(runtime_bindings["library_identities"]), separators=(",", ":")
                ),
                "selected_test_identities": json.dumps(
                    list(_canonical_selector(edge["verification"]["selector"])["test_identities"]),
                    separators=(",", ":"),
                ),
            }
        )
    if any(receipt_inputs.get(key) != expected for key, expected in expected_inputs.items()):
        return None
    check = _read_json(prior_dir / "check-result.json", "prior consumer check")
    if validate_check_result(check) or check.get("verdict") != "PASS":
        return None
    if native_actions_shadow_source is not None:
        prior_evidence = match.get("native_evidence")
        prior_receipt = match.get("native_receipt")
        if not isinstance(prior_evidence, Mapping) or not isinstance(prior_receipt, Mapping):
            return None
        # Retain the actual provider TestResult/CheckResult identity records
        # from the prior proof, but refresh the plan/graph/edge ingress with
        # the current invocation. Native Actions then decides whether the
        # receipt dependency set is identical; Python only handles safe file
        # discovery/copying after that decision.
        native_evidence = copy.deepcopy(dict(prior_evidence))
        current_family = _identity_value({"family": "mncs", "graph_identity": graph_identity})
        current_graph = _identity_value(graph_identity)
        current_edge = _identity_value(edge.get("fingerprint", ""))
        current_source = _identity_value(plan.get("source", {}).get("sha256", ""))
        current_plan = _identity_value(plan.get("plan_id", ""))
        current_contract = _identity_value(edge.get("contract_identity", ""))
        current_consumer = _identity_value(edge.get("consumer_manifest_identity", ""))
        current_check = _identity_value(edge.get("verification", {}).get("check_identity", ""))
        native_evidence["plan"] = {
            **dict(native_evidence["plan"]),
            "family_identity": current_family,
            "plan_identity": current_plan,
            "graph_identity": current_graph,
            "edge_identity": current_edge,
            "source_identity": current_source,
            "contract_identity": current_contract,
            "consumer_identity": current_consumer,
            "check_identity": current_check,
        }
        native_evidence["graph"] = {
            **dict(native_evidence["graph"]),
            "family_identity": current_family,
            "graph_identity": current_graph,
            "edge_identity": current_edge,
            "source_identity": current_source,
            "contract_identity": current_contract,
            "consumer_identity": current_consumer,
        }
        native_evidence["edge"] = {
            **dict(native_evidence["edge"]),
            "edge_identity": current_edge,
            "source_identity": current_source,
            "contract_identity": current_contract,
            "consumer_identity": current_consumer,
        }
        native_evidence["prior_receipt"] = dict(prior_receipt)
        native = _run_native_actions_family_check(
            mncs_binary=mncs_binary,
            source_path=native_actions_shadow_source,
            evidence=native_evidence,
            cwd=workspace_checkout,
        )
        if native["verdict"] != "PASS" or not native["reusable"]:
            return None
    destination = output_dir / relative
    destination.mkdir(parents=True, exist_ok=True)
    for name in required:
        shutil.copyfile(prior_dir / name, destination / name)
    return {
        **dict(match),
        "status": "reused",
        "evidence_directory": relative,
        "native_receipt": match.get("native_receipt"),
        "native_family_result": match.get("native_family_result"),
        "native_evidence": match.get("native_evidence"),
    }


def build_selective_proof(
    *,
    plan_path: Path,
    graph_path: Path,
    workspace_root: Path,
    output_dir: Path,
    prior_proof: Path | None = None,
    mncs_test_runner: str = "mncs-test",
    mncs_binary: str = "mncs",
    mncs_test_libraries: list[str] | None = None,
    native_actions_shadow_source: Path | None = None,
    compatibility_oracle: bool = False,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SelectiveFamilyError(f"output directory must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if native_actions_shadow_source is None and not compatibility_oracle:
        native_actions_shadow_source = (
            Path(__file__).resolve().parents[1] / "native/mncs/actions/family/v1.mncs"
        )
    plan = validate_plan(_read_json(plan_path, "verification plan"))
    graph = load_family_graph(graph_path)
    cross = plan["impact"]["cross_repository"]
    selection = plan["selection"]
    if selection.get("routing_scope") != "selected_repositories":
        raise SelectiveFamilyError("selective family execution requires selected_repositories routing")
    if not cross.get("complete"):
        raise SelectiveFamilyError("family graph is incomplete; selective execution refuses to guess")
    if cross.get("graph_identity") != graph.get("graph_identity"):
        raise SelectiveFamilyError("verification plan and family graph identities disagree")
    selected = selection.get("selected_repositories")
    edges = cross.get("edges")
    if not isinstance(selected, list) or not selected or not isinstance(edges, list):
        raise SelectiveFamilyError("selective plan has no bounded consumer set")
    if sorted({edge.get("consumer_repository") for edge in edges}) != sorted(selected):
        raise SelectiveFamilyError("plan selected repositories do not match its edges")
    exact_edges = [_matching_edge(plan, graph, edge) for edge in edges]
    producer = _producer_binding(graph, exact_edges, workspace_root)
    plan_digest = _file_digest(plan_path)
    graph_digest = _file_digest(graph_path)
    prior = _prior_document(prior_proof)
    mncs_test_libraries = list(mncs_test_libraries or [])
    runtime_bindings = _runtime_bindings(
        mncs_test_runner=mncs_test_runner,
        mncs_binary=mncs_binary,
        mncs_test_libraries=mncs_test_libraries,
    )
    records: list[dict[str, Any]] = []
    generated = 0
    reused = 0
    for edge in exact_edges:
        repository_id = edge["consumer_repository"]
        checkout = workspace_root / repository_id
        if not checkout.is_dir():
            raise SelectiveFamilyError(f"selected consumer checkout is unavailable: {repository_id}")
        manifest_path = checkout / "family-verification-checks-v1.json"
        if not manifest_path.is_file():
            raise SelectiveFamilyError(f"selected consumer verification surface is unavailable: {repository_id}")
        current_bound = bind_declaration_evidence(
            checkout,
            _read_json(checkout / "family-semantic-contracts-v1.json", f"{repository_id} declaration"),
        )
        current_identity = declaration_identity(current_bound)
        revision = _repository_revision(checkout, current_identity)
        record = _reuse_consumer(
            prior=prior,
            output_dir=output_dir,
            repository_id=repository_id,
            edge=edge,
            plan=plan,
            repository_revision=revision,
            producer_repository_revision=producer["repository_revision"],
            runtime_bindings=runtime_bindings,
            plan_digest=plan_digest,
            graph_identity=graph["graph_identity"],
            mncs_binary=mncs_binary,
            native_actions_shadow_source=native_actions_shadow_source,
            workspace_checkout=checkout,
        )
        if record is not None:
            records.append(record)
            reused += 1
            continue
        try:
            result, revision = _consumer_check(
                checkout=checkout,
                repository_id=repository_id,
                edge=edge,
                plan=plan,
                plan_digest=plan_digest,
                graph_identity=graph["graph_identity"],
                graph_digest=graph_digest,
                producer_repository_revision=producer["repository_revision"],
                mncs_test_runner=mncs_test_runner,
                mncs_binary=mncs_binary,
                mncs_test_libraries=mncs_test_libraries,
                runtime_bindings=runtime_bindings,
                native_actions_shadow_source=native_actions_shadow_source,
            )
        except SelectiveFamilyError as error:
            result = {
                "schema_version": "mncs.check-result/1",
                "id": edge["verification"]["check_identity"],
                "provider": repository_id,
                "verdict": "UNKNOWN" if edge.get("verification", {}).get("runner") == "mncs-test" else "FAIL",
                "scope": edge["verification"]["surface"],
                "claim": "selected consumer contract proof",
                "summary": str(error),
                "contract_revision": edge["contract_revision"],
                "producer_revision": plan["source"]["sha256"],
                "producer_repository_revision": producer["repository_revision"],
                "unresolved": [str(error)],
            }
        records.append(
            _write_consumer_evidence(
                output_dir=output_dir,
                repository_id=repository_id,
                result=result,
                plan=plan,
                plan_digest=plan_digest,
                graph_digest=graph_digest,
                edge=edge,
                repository_revision=revision,
                producer_repository_revision=producer["repository_revision"],
                runtime_bindings=runtime_bindings,
            )
        )
        generated += 1
    verdicts = [record["verdict"] for record in records]
    compatibility_status = "FAIL" if "FAIL" in verdicts else ("UNKNOWN" if "UNKNOWN" in verdicts else "PASS")
    native_actions_proof = None
    if native_actions_shadow_source is not None:
        native_actions_proof = _run_native_actions_selected_proof(
            mncs_binary=mncs_binary,
            source_path=native_actions_shadow_source,
            records=records,
            cwd=workspace_root,
            strict_native=True,
        )
        status = str(native_actions_proof["verdict"])
    else:
        # Explicit compatibility/oracle mode only. The canonical path is
        # required to supply the native Actions source and therefore never
        # silently falls back to this projection.
        status = compatibility_status
    contract_identities = sorted({edge["contract_identity"] for edge in exact_edges})
    core: dict[str, Any] = {
        "schema_version": PROOF_SCHEMA,
        "status": status,
        "plan_id": plan["plan_id"],
        "plan_sha256": plan_digest,
        "source_change_sha256": plan["source"]["sha256"],
        "producer": producer,
        "contract_identities": contract_identities,
        "contract_revision": sorted({edge["contract_revision"] for edge in exact_edges}),
        "graph_identity": graph["graph_identity"],
        "graph_sha256": graph_digest,
        "routing": {
            "scope": "selected_repositories",
            "selected_repositories": selected,
            # Backward-compatible field: this is the semantic-graph count,
            # never the total registry family size.
            "family_repository_count": len(graph["repositories"]),
            "semantic_graph_repository_count": len(graph["repositories"]),
            "registered_family_project_count": graph["coverage"]["registered_family_project_count"],
            "coverage_classified_project_count": graph["coverage"]["classified_project_count"],
            "unclassified_project_count": graph["coverage"]["unclassified_project_count"],
            "unclassified_repositories": list(graph["coverage"]["unclassified_repositories"]),
            "coverage_status": graph["coverage"]["coverage_status"],
            "topology_status": graph["coverage"]["topology_status"],
            "selected_repository_count": len(selected),
            "unselected_repositories": sorted(
                {repository["id"] for repository in graph["repositories"]} - set(selected)
            ),
        },
        "edges": exact_edges,
        "consumers": records,
        "metrics": {
            "repositories_selected": len(selected),
            "repositories_available": len(graph["repositories"]),
            "semantic_graph_participants": len(graph["repositories"]),
            "registered_family_projects": graph["coverage"]["registered_family_project_count"],
            "coverage_classified_projects": graph["coverage"]["classified_project_count"],
            "unclassified_projects": graph["coverage"]["unclassified_project_count"],
            "coverage_status": graph["coverage"]["coverage_status"],
            "checks_executed": generated,
            "checks_available": len(exact_edges),
            "receipts_reused": reused,
            "receipts_generated": generated,
        },
        "proof": {
            "boundary": "selected_repositories",
            "required_evidence": "selected_consumer_proofs_pass",
            "established": status == "PASS",
            "claim": "all selected consumer proofs required by this plan are established",
        },
    }
    if native_actions_proof is not None:
        core["native_actions"] = native_actions_proof
        core["native_proof_identity"] = native_actions_proof["proof_identity"]
        # Compatibility readers may still look for the Phase II shadow key;
        # it aliases the same native result and carries no alternate authority.
        core["native_actions_shadow"] = native_actions_proof
    core["proof_identity"] = sha256_hex(canonical_bytes(core))
    _write_json(output_dir / "composite-proof.json", core)
    return core


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prior-proof", type=Path)
    parser.add_argument("--mncs-test", dest="mncs_test_runner", default="mncs-test")
    parser.add_argument("--mncs", dest="mncs_binary", default="mncs")
    parser.add_argument("--mncs-test-library", action="append", default=[])
    native_group = parser.add_mutually_exclusive_group()
    native_group.add_argument(
        "--native-actions-source",
        dest="native_actions_source",
        type=Path,
        help="Run the canonical Actions application through generic `mncs call`.",
    )
    native_group.add_argument(
        "--native-actions-shadow",
        dest="native_actions_source",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--python-compatibility-oracle",
        action="store_true",
        help="Use the legacy Python projection explicitly; never used by the canonical path.",
    )
    args = parser.parse_args(argv)
    default_native_source = Path(__file__).resolve().parents[1] / "native/mncs/actions/family/v1.mncs"
    native_actions_source = None
    if not args.python_compatibility_oracle:
        native_actions_source = (
            args.native_actions_source.resolve()
            if args.native_actions_source
            else default_native_source
        )
    try:
        proof = build_selective_proof(
            plan_path=args.plan.resolve(),
            graph_path=args.graph.resolve(),
            workspace_root=args.workspace_root.resolve(),
            output_dir=args.output_dir.resolve(),
            prior_proof=args.prior_proof.resolve() if args.prior_proof else None,
            mncs_test_runner=args.mncs_test_runner,
            mncs_binary=args.mncs_binary,
            mncs_test_libraries=args.mncs_test_library,
            native_actions_shadow_source=native_actions_source,
            compatibility_oracle=args.python_compatibility_oracle,
        )
    except (OSError, SelectiveFamilyError, ValueError) as error:
        print(f"SELECTIVE FAMILY VERIFICATION REFUSED: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": proof["status"],
                "proof_identity": proof["proof_identity"],
                "repositories_selected": proof["metrics"]["repositories_selected"],
                "repositories_available": proof["metrics"]["repositories_available"],
                "checks_executed": proof["metrics"]["checks_executed"],
                "receipts_reused": proof["metrics"]["receipts_reused"],
            },
            sort_keys=True,
        )
    )
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
