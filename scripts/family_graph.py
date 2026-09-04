#!/usr/bin/env python3
"""Family candidate graph: build, verify, and advance the accepted state.

Orchestration tooling only (mncs-actions owns no semantic decisions).
Graphs compose existing contracts: the moving-head candidate document,
fixed family contracts, producer descriptors, coherence analysis,
owner-native evidence, MNCS graph promotion, and Commons ChangeSets.

Identity: digest = sha256 over canonical bytes of
{schema_version, base, members (by name), dependencies (sorted)}.
Evidence accumulation never mutates identity.

Usage:
  family_graph.py build --candidate CAND --fixed FIXED --descriptors DESC
      --graph-id family-graph-N --actions-revision SHA
      [--accepted-graph PREV] [--coherence COH.json] [--evidence-dir DIR]
      [--promotion-result PROM.json] [--commons-record CS.json]
      --output GRAPH.json
  family_graph.py verify --graph GRAPH.json --fixed FIXED
      [--promotion-result PROM.json --lib-dir LIB]
      [--coherence COH.json] [--commons-record CS.json --commons-root DIR]
  family_graph.py advance --graph GRAPH.json --fixed FIXED
      --promotion-result PROM.json --lib-dir LIB --coherence COH.json
      --commons-record CS.json --commons-root DIR
      --output-contracts PROPOSED.json --output-graph ACCEPTED.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / ".." / "lib"))
from mncs_actions import canonical_bytes, sha256_hex, utc_now_z

GRAPH_SCHEMA = "mncs-actions.family-candidate-graph/1"
GRAPH_SUBJECT_REPOSITORY = "mncs-family/graph"
ACTIONS_MEMBER = "mncs-actions"
ACTIONS_REPOSITORY = "epi13/mncs-actions"


class GraphError(RuntimeError):
    """Graph construction or verification failed."""


def _read(path: Path, label: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GraphError(f"{label} {path}: cannot read: {exc}") from exc
    if not isinstance(doc, dict):
        raise GraphError(f"{label} {path}: must be a JSON object")
    return doc


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise GraphError(f"{label} must be a 40-hex revision")
    try:
        return bytes.fromhex(value).hex()
    except ValueError as exc:
        raise GraphError(f"{label} must be lowercase hex") from exc


def graph_digest(doc: dict) -> str:
    """Recompute the identity digest over the canonical core."""
    core = {
        "schema_version": doc["schema_version"],
        "base": doc["base"],
        "members": sorted(doc["members"], key=lambda m: m["name"]),
        "dependencies": sorted(
            doc["dependencies"],
            key=lambda d: (d["from"], d["to"], d["edge"], d["target"]),
        ),
    }
    return sha256_hex(canonical_bytes(core))


def _descriptors_by_producer(descriptors: dict) -> dict[str, dict]:
    entries = descriptors.get("descriptors")
    if not isinstance(entries, list) or not entries:
        raise GraphError("descriptors must hold a descriptor array")
    by_producer = {}
    for entry in entries:
        producer = entry.get("producer")
        contract = entry.get("contract") or {}
        outputs = {
            output.get("check_id"): output.get("contract_revision")
            for output in entry.get("outputs", [])
            if isinstance(output, dict)
        }
        by_producer[producer] = {
            "contract_id": contract.get("id", ""),
            "contract_revision": contract.get("revision", ""),
            "outputs": outputs,
        }
    return by_producer


def cmd_build(args: argparse.Namespace) -> int:
    candidate = _read(Path(args.candidate), "candidate")
    fixed = _read(Path(args.fixed), "fixed contracts")
    descriptors = _read(Path(args.descriptors), "descriptors")
    fixed_entries = {item["name"]: item for item in fixed.get("repositories", [])}
    contracts = _descriptors_by_producer(descriptors)

    accepted_digest = sha256_hex(canonical_bytes(fixed))
    if candidate.get("base_contract_digest") != accepted_digest:
        raise GraphError("candidate does not continue the accepted fixed contracts")
    if args.accepted_graph:
        previous = _read(Path(args.accepted_graph), "accepted graph")
        base = {
            "graph_id": previous["graph_id"],
            "digest": previous["digest"],
            "contract_digest": accepted_digest,
        }
        if previous.get("status") != "accepted":
            raise GraphError("previous graph is not an accepted graph")
    else:
        base = {
            "graph_id": "family-graph-0",
            "digest": accepted_digest,
            "contract_digest": accepted_digest,
        }

    members = []
    for entry in candidate.get("repositories", []):
        name = entry["name"]
        base_revision = _sha(entry.get("base_revision"), f"{name}.base_revision")
        candidate_revision = _sha(
            entry.get("candidate_revision"), f"{name}.candidate_revision"
        )
        accepted = fixed_entries.get(name, {}).get("revision", "")
        if accepted != base_revision:
            raise GraphError(f"{name}: candidate base does not match accepted revision")
        producer = contracts.get(name, {})
        members.append(
            {
                "name": name,
                "repository": entry["repository"],
                "commit": candidate_revision,
                "changed": candidate_revision != base_revision,
                "contracts": {
                    "descriptor_contract": producer.get("contract_revision", ""),
                    **{
                        f"output:{check}": revision
                        for check, revision in producer.get("outputs", {}).items()
                    },
                },
            }
        )
    actions_revision = _sha(args.actions_revision, "actions revision")
    members.append(
        {
            "name": ACTIONS_MEMBER,
            "repository": ACTIONS_REPOSITORY,
            "commit": actions_revision,
            "changed": True,
            "contracts": {},
        }
    )
    members.sort(key=lambda m: m["name"])

    dependencies = []
    for member in members:
        if member["name"] == ACTIONS_MEMBER:
            continue
        accepted_revision = fixed_entries[member["name"]]["revision"]
        dependencies.append(
            {
                "from": member["name"],
                "to": member["name"],
                "edge": "compat",
                "target": accepted_revision,
            }
        )
    dependencies.sort(key=lambda d: (d["from"], d["to"], d["edge"], d["target"]))
    # Pin edges are analysis evidence, not identity: folding them into
    # dependencies would mutate the digest on every rebuild and break the
    # skeleton -> validated -> promoted -> related chain.
    pin_edges: list[dict] = []
    coherence: dict = {}
    if args.coherence:
        coherence = _read(Path(args.coherence), "coherence")
        for edge in coherence.get("edges", []):
            pin_edges.append(
                {
                    "from": edge["from"],
                    "to": edge["to"],
                    "edge": edge["edge"],
                    "target": _sha(edge.get("target"), "coherence edge target"),
                }
            )
    pin_edges.sort(key=lambda d: (d["from"], d["to"], d["edge"], d["target"]))

    requirements = {"required": [], "optional": []}
    if args.boundary:
        template = _read(Path(args.boundary), "advancement boundary template")
        requirements = {
            "required": [e["check_id"] for e in template.get("required_evidence", [])],
            "optional": [e["check_id"] for e in template.get("optional_evidence", [])],
        }

    evidence = []
    if args.evidence_dir:
        root = Path(args.evidence_dir)
        for path in sorted(root.rglob("*.json")):
            raw = path.read_bytes()
            evidence.append(
                {
                    "check_id": path.stem,
                    "path": path.relative_to(root).as_posix(),
                    "digest": hashlib.sha256(raw).hexdigest(),
                }
            )

    promotion = None
    promotion_path = ""
    if args.promotion_result:
        promotion_path = args.promotion_result
        raw = Path(promotion_path).read_bytes()
        claim = json.loads(raw.decode("utf-8"))
        promotion = {
            "verdict": claim.get("verdict", "UNKNOWN"),
            "path": promotion_path,
            "digest": hashlib.sha256(raw).hexdigest(),
        }

    commons_record = ""
    if args.commons_record:
        commons_record = args.commons_record

    blockers = list(coherence.get("blockers", []))
    if promotion is not None and promotion["verdict"] != "PASS":
        blockers.append(f"graph promotion verdict is {promotion['verdict']}")

    if commons_record:
        status = "related"
    elif promotion is not None:
        status = "promoted"
    elif evidence:
        status = "validated"
    else:
        status = "candidate"

    graph = {
        "schema_version": GRAPH_SCHEMA,
        "graph_id": args.graph_id,
        "base": base,
        "members": members,
        "dependencies": dependencies,
        "pin_edges": pin_edges,
        "evidence_requirements": requirements,
        "evidence": evidence,
        "blockers": sorted(set(blockers)),
        "status": status,
        "provenance": {
            "generator": args.provenance_generator,
            "generated_at": args.generated_at or utc_now_z(),
        },
    }
    if promotion is not None:
        graph["promotion"] = promotion
    graph["digest"] = graph_digest(graph)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"graph {args.graph_id} {status} -> {args.output} ({graph['digest'][:12]})")
    return 0


def _check_digest(doc: dict, label: str) -> None:
    expected = doc.get("digest", "")
    computed = graph_digest(doc)
    if expected != computed:
        raise GraphError(
            f"{label} digest mismatch: declared {expected[:12]} recomputed {computed[:12]}"
        )


def cmd_verify(args: argparse.Namespace) -> int:
    """Recompute identity and check every bound artifact. Exit 2 refuses."""
    graph = _read(Path(args.graph), "graph")
    fixed = _read(Path(args.fixed), "fixed contracts")
    for field in (
        "schema_version",
        "graph_id",
        "base",
        "members",
        "dependencies",
        "evidence_requirements",
        "status",
    ):
        if field not in graph:
            raise GraphError(f"graph is missing {field}")
    if graph["schema_version"] != GRAPH_SCHEMA:
        raise GraphError(f"graph schema_version must be {GRAPH_SCHEMA}")
    _check_digest(graph, "graph")
    if sha256_hex(canonical_bytes(fixed)) != graph["base"]["contract_digest"]:
        raise GraphError("graph base does not match the fixed contracts")
    names = [m["name"] for m in graph["members"]]
    if len(set(names)) != len(names):
        raise GraphError("graph has duplicate member names")

    if args.promotion_result:
        if "promotion" not in graph:
            raise GraphError("graph carries no promotion reference")
        raw = Path(args.promotion_result).read_bytes()
        claim = json.loads(raw.decode("utf-8"))
        if hashlib.sha256(raw).hexdigest() != graph["promotion"]["digest"]:
            raise GraphError("promotion result bytes do not match the graph reference")
        if claim.get("verdict") != graph["promotion"]["verdict"]:
            raise GraphError("promotion verdict does not match the graph reference")
        sys.path.insert(0, args.lib_dir)
        import mncs_actions as lib

        digest = graph["digest"]
        errors = lib.validate_promotion_claim(
            claim,
            boundary_id=claim.get("promotion", {}).get("boundary_id", ""),
            subject_repository=GRAPH_SUBJECT_REPOSITORY,
            subject_commit=f"graph:{digest}",
        )
        if errors:
            raise GraphError(f"promotion claim invalid: {errors[0]}")
        if claim.get("promotion", {}).get("graph_digest") != digest:
            raise GraphError("promotion claim binds a different graph digest")
        print(f"promotion {claim['verdict']} bound to graph {digest[:12]}")

    if args.coherence:
        coherence = _read(Path(args.coherence), "coherence")
        if coherence.get("graph_digest") != graph["digest"]:
            raise GraphError("coherence report is for a different graph")
        print(
            f"coherence: {len(coherence.get('blockers', []))} blockers, "
            f"{len(coherence.get('movements', []))} movements"
        )

    if args.commons_record:
        if not args.commons_root:
            raise GraphError("commons validation needs --commons-root")
        sys.path.insert(0, str(Path(args.commons_root) / "src"))
        from mncs_commons.validation import validate_record

        record = _read(Path(args.commons_record), "commons record")
        report = validate_record(record)
        if not report.valid:
            raise GraphError(f"commons record invalid: {report.diagnostics[:1]}")
        details = record.get("details", {})
        members = {(m["repository"], m["commit"]) for m in graph["members"]}
        based = {
            (r["repository"], r["commit"]) for r in details.get("baseRevisions", [])
        }
        if based != members:
            raise GraphError("commons base revisions do not cover the graph members")
        promotes = [
            item
            for item in details.get("references", [])
            if item.get("group") == "promotes"
        ]
        if len(promotes) != 1:
            raise GraphError("commons record must carry exactly one promotion result")
        claim_raw = (
            Path(args.promotion_result).read_bytes() if args.promotion_result else None
        )
        if claim_raw is not None and promotes[0]["reference"].get("contentDigest") != (
            "sha256:" + hashlib.sha256(claim_raw).hexdigest()
        ):
            raise GraphError("commons promotes edge does not bind the promotion bytes")
        if details.get("predecessorGraph") != graph["base"]["digest"]:
            raise GraphError("commons record does not chain the accepted predecessor")
        print(f"commons {record.get('kind')} relates graph {graph['digest'][:12]}")

    print(f"graph {graph['graph_id']} verified ({graph['status']})")
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    """Emit the proposed accepted state. Refuses unless the proof holds."""
    graph = _read(Path(args.graph), "graph")
    fixed = _read(Path(args.fixed), "fixed contracts")
    coherence = _read(Path(args.coherence), "coherence")
    refusals: list[str] = []

    try:
        _check_digest(graph, "graph")
    except GraphError as exc:
        refusals.append(str(exc))
    if sha256_hex(canonical_bytes(fixed)) != graph["base"].get("contract_digest"):
        refusals.append("graph base does not continue the fixed contracts")
    if graph.get("status") != "related":
        refusals.append(f"graph status is {graph.get('status')}, not related")
    if graph.get("promotion", {}).get("verdict") != "PASS":
        refusals.append("graph promotion is not PASS")
    if graph.get("blockers"):
        refusals.append(f"graph carries blockers: {graph['blockers'][:2]}")
    unmet = [
        m
        for m in coherence.get("movements", [])
        if m.get("classification") == "UNKNOWN" and not m.get("satisfied")
    ]
    if coherence.get("graph_digest") != graph.get("digest"):
        refusals.append("coherence report is for a different graph")
    if unmet:
        refusals.append(
            "unsatisfied pin movements: "
            + ", ".join(
                f"{m['from']}->{m['to']} ({m['classification']})" for m in unmet[:4]
            )
        )
    if coherence.get("cycles", {}).get("unsafe"):
        refusals.append("authority cycle audit reports unsafe patterns")

    # Re-run the read-only verifications (promotion claim, Commons record).
    verify_args = argparse.Namespace(
        graph=args.graph,
        fixed=args.fixed,
        promotion_result=args.promotion_result,
        lib_dir=args.lib_dir,
        coherence=args.coherence,
        commons_record=args.commons_record,
        commons_root=args.commons_root,
    )
    try:
        cmd_verify(verify_args)
    except GraphError as exc:
        refusals.append(str(exc))

    if refusals:
        print("advancement REFUSED:")
        for refusal in refusals:
            print(f"  - {refusal}")
        return 2

    member_commit = {m["name"]: m["commit"] for m in graph["members"]}
    proposed = json.loads(json.dumps(fixed))
    for entry in proposed["repositories"]:
        entry["revision"] = member_commit[entry["name"]]
    out_contracts = Path(args.output_contracts)
    out_contracts.parent.mkdir(parents=True, exist_ok=True)
    before = json.dumps(fixed, indent=2, sort_keys=True).splitlines()
    after = json.dumps(proposed, indent=2, sort_keys=True).splitlines()
    out_contracts.write_text("\n".join(after) + "\n", encoding="utf-8")

    accepted = json.loads(json.dumps(graph))
    accepted["status"] = "accepted"
    out_graph = Path(args.output_graph)
    out_graph.write_text(
        json.dumps(accepted, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    import difflib

    diff = list(
        difflib.unified_diff(before, after, "family-contracts.json", "proposed")
    )
    print(f"advancement PROPOSED: graph {graph['graph_id']} -> accepted")
    print("\n".join(diff[:40]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Family candidate graph lifecycle.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build", help="build a candidate graph document"
    )
    build_parser.add_argument("--candidate", required=True)
    build_parser.add_argument("--fixed", required=True)
    build_parser.add_argument("--descriptors", required=True)
    build_parser.add_argument("--graph-id", required=True)
    build_parser.add_argument("--actions-revision", required=True)
    build_parser.add_argument("--accepted-graph", default="")
    build_parser.add_argument("--coherence", default="")
    build_parser.add_argument("--evidence-dir", default="")
    build_parser.add_argument("--promotion-result", default="")
    build_parser.add_argument("--commons-record", default="")
    build_parser.add_argument("--boundary", default="")
    build_parser.add_argument("--provenance-generator", default="family_graph.py build")
    build_parser.add_argument("--generated-at", default="")
    build_parser.add_argument("--output", required=True)

    verify_parser = subparsers.add_parser("verify", help="verify a graph document")
    verify_parser.add_argument("--graph", required=True)
    verify_parser.add_argument("--fixed", required=True)
    verify_parser.add_argument("--promotion-result", default="")
    verify_parser.add_argument("--lib-dir", default="")
    verify_parser.add_argument("--coherence", default="")
    verify_parser.add_argument("--commons-record", default="")
    verify_parser.add_argument("--commons-root", default="")

    advance_parser = subparsers.add_parser("advance", help="propose the accepted state")
    advance_parser.add_argument("--graph", required=True)
    advance_parser.add_argument("--fixed", required=True)
    advance_parser.add_argument("--promotion-result", required=True)
    advance_parser.add_argument("--lib-dir", required=True)
    advance_parser.add_argument("--coherence", required=True)
    advance_parser.add_argument("--commons-record", required=True)
    advance_parser.add_argument("--commons-root", required=True)
    advance_parser.add_argument("--output-contracts", required=True)
    advance_parser.add_argument("--output-graph", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            return cmd_build(args)
        if args.command == "verify":
            return cmd_verify(args)
        return cmd_advance(args)
    except GraphError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
