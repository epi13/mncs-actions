#!/usr/bin/env python3
"""Execute only the consumer proof surfaces selected by a verification plan.

The Commons graph supplies topology and edge identities.  Repository-owned
``family-verification-checks-v1.json`` files supply a typed check identity;
the only runner currently supported here is the command-free declaration
surface check.  Actions owns process transport, receipts, and composition,
while Commons remains the authority for the graph and plan contracts.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent / ".." / "lib"))
from mncs_actions import (  # noqa: E402
    CLAIM_ESTABLISHED,
    build_evidence_manifest,
    build_execution_receipt,
    canonical_bytes,
    sha256_hex,
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


def _check_manifest(checkout: Path, repository_id: str) -> tuple[Path, dict[str, Any]]:
    path = checkout / "family-verification-checks-v1.json"
    value = _read_json(path, f"{repository_id} verification manifest")
    return path, validate_verification_manifest(value, repository_id=repository_id)


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


def _consumer_check(
    *,
    checkout: Path,
    repository_id: str,
    edge: Mapping[str, Any],
    plan: Mapping[str, Any],
    plan_digest: str,
    graph_digest: str,
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
    }
    receipt = build_execution_receipt(
        command=f"declaration check {repository_id}",
        command_exit_code=0 if result["verdict"] == "PASS" else 1,
        claim_status=CLAIM_ESTABLISHED,
        result_path=str(check_path),
        result_present=True,
        result_valid=True,
        claim_verdict=str(result["verdict"]),
        produced_files=[{"path": check_path.name, "sha256": check_digest}],
        inputs=inputs,
    )
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
        "consumer_manifest_identity": edge["consumer_manifest_identity"],
        "consumer_evidence_sha256": edge["consumer_evidence_sha256"],
        "verification_evidence_sha256": edge["verification_evidence_sha256"],
        "edge_fingerprint": edge["fingerprint"],
        "contract_revision": edge["contract_revision"],
        "check_digest": check_digest,
        "evidence_directory": f"evidence/{repository_id}",
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
    ):
        if match.get(field) != expected:
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
    check = _read_json(prior_dir / "check-result.json", "prior consumer check")
    if validate_check_result(check) or check.get("verdict") != "PASS":
        return None
    destination = output_dir / relative
    destination.mkdir(parents=True, exist_ok=True)
    for name in required:
        shutil.copyfile(prior_dir / name, destination / name)
    return {
        **dict(match),
        "status": "reused",
        "evidence_directory": relative,
    }


def build_selective_proof(
    *,
    plan_path: Path,
    graph_path: Path,
    workspace_root: Path,
    output_dir: Path,
    prior_proof: Path | None = None,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SelectiveFamilyError(f"output directory must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
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
    plan_digest = _file_digest(plan_path)
    graph_digest = _file_digest(graph_path)
    prior = _prior_document(prior_proof)
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
                graph_digest=graph_digest,
            )
        except SelectiveFamilyError as error:
            result = {
                "schema_version": "mncs.check-result/1",
                "id": edge["verification"]["check_identity"],
                "provider": repository_id,
                "verdict": "FAIL",
                "scope": edge["verification"]["surface"],
                "claim": "selected consumer contract proof",
                "summary": str(error),
                "contract_revision": edge["contract_revision"],
                "producer_revision": plan["source"]["sha256"],
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
            )
        )
        generated += 1
    verdicts = [record["verdict"] for record in records]
    status = "FAIL" if "FAIL" in verdicts else ("UNKNOWN" if "UNKNOWN" in verdicts else "PASS")
    contract_identities = sorted({edge["contract_identity"] for edge in exact_edges})
    core: dict[str, Any] = {
        "schema_version": PROOF_SCHEMA,
        "status": status,
        "plan_id": plan["plan_id"],
        "plan_sha256": plan_digest,
        "source_change_sha256": plan["source"]["sha256"],
        "contract_identities": contract_identities,
        "contract_revision": sorted({edge["contract_revision"] for edge in exact_edges}),
        "graph_identity": graph["graph_identity"],
        "graph_sha256": graph_digest,
        "routing": {
            "scope": "selected_repositories",
            "selected_repositories": selected,
            "family_repository_count": len(graph["repositories"]),
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
    args = parser.parse_args(argv)
    try:
        proof = build_selective_proof(
            plan_path=args.plan.resolve(),
            graph_path=args.graph.resolve(),
            workspace_root=args.workspace_root.resolve(),
            output_dir=args.output_dir.resolve(),
            prior_proof=args.prior_proof.resolve() if args.prior_proof else None,
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
