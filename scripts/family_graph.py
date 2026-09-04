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
      [--boundary B.json [--boundary-template T.json] [--authority-map AM.json]]
  family_graph.py advance --graph GRAPH.json --fixed FIXED
      --promotion-result PROM.json --lib-dir LIB --coherence COH.json
      --commons-record CS.json --commons-root DIR --boundary B.json
      [--boundary-template T.json] [--authority-map AM.json]
      --checks-dir DIR --obligations-dir DIR
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
    if args.promotion_result:
        raw = Path(args.promotion_result).read_bytes()
        claim = json.loads(raw.decode("utf-8"))
        # Durable reference only: bundle-relative conventional name plus the
        # content digest. No absolute or machine-local path may survive into
        # accepted state (see schemas/family-acceptance-proof.schema.json).
        promotion = {
            "verdict": claim.get("verdict", "UNKNOWN"),
            "digest": hashlib.sha256(raw).hexdigest(),
            "ref": "promotion-check.json",
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


BOUNDARY_SCHEMA = "mncs-promotion-boundary/0.1"
AUTHORITY_MAP_SCHEMA = "mncs-authority-map/0.1"


def _read_json_no_dupes(path: Path, label: str) -> dict:
    """Read a JSON object, refusing duplicate keys (fail closed).

    Duplicate object keys collapse silently under a plain parse, so a map
    carrying two bindings for one check id would verify against only the
    survivor. Transport must see the contradiction.
    """

    def _no_dupes(pairs: list[tuple[str, object]]) -> dict:
        seen: dict = {}
        for key, value in pairs:
            if key in seen:
                raise GraphError(f"{label} {path}: duplicate key {key!r}")
            seen[key] = value
        return seen

    try:
        doc = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_dupes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GraphError(f"{label} {path}: cannot read: {exc}") from exc
    if not isinstance(doc, dict):
        raise GraphError(f"{label} {path}: must be a JSON object")
    return doc


def acceptance_tier(
    *,
    verdict: str,
    required_total: int,
    required_passed: int,
    optional_verdicts: dict[str, str],
    optional_expected: list[str],
    open_required_obligations: list[str],
) -> str:
    """Project the acceptance tier. Pure decision logic.

    Mirrored in ``pressure/family-acceptance.mncs`` (``project_tier``);
    the host computes the input booleans, MNCS projects the tier, and
    agreement tests pin the two together. ``core`` means the graph
    crossed the current core advancement boundary (required evidence
    decides); ``full`` additionally means every boundary-listed optional
    authority returned PASS with no open required obligations.
    ``refused`` is never accepted.
    """
    if verdict != "PASS" or required_total < 1 or required_passed != required_total:
        return "refused"
    if open_required_obligations:
        return "core"
    for check_id in optional_expected:
        if optional_verdicts.get(check_id) != "PASS":
            return "core"
    return "full"


def acceptance_record(
    *,
    claim: dict,
    boundary: dict,
    checks: dict[str, dict],
    obligations: list[dict],
) -> dict:
    """Build the machine-readable acceptance record for a promotion claim."""
    optional_expected = [
        e.get("check_id", "") for e in boundary.get("optional_evidence", [])
    ]
    optional = [
        {
            "check_id": check_id,
            "verdict": checks.get(check_id, {}).get("verdict", "missing"),
        }
        for check_id in optional_expected
    ]
    open_required = sorted(
        str(o.get("obligation_key", ""))
        for o in obligations
        if o.get("status") == "open" and o.get("required") is True
    )
    promotion = claim.get("promotion", {}) if isinstance(claim, dict) else {}
    tier = acceptance_tier(
        verdict=str(claim.get("verdict", "")),
        required_total=int(promotion.get("required_total", 0) or 0),
        required_passed=int(promotion.get("required_passed", 0) or 0),
        optional_verdicts={
            c: v for c, v in ((o["check_id"], o["verdict"]) for o in optional)
        },
        optional_expected=optional_expected,
        open_required_obligations=open_required,
    )
    return {
        "tier": tier,
        "policy": (
            "family-advancement core boundary: required owner evidence decides; "
            "optional observations never decide. 'core' crossed the required "
            "boundary; 'full' additionally has every boundary-listed optional "
            "authority at PASS with no open required obligations."
        ),
        "required": {
            "total": promotion.get("required_total", 0),
            "passed": promotion.get("required_passed", 0),
        },
        "optional": optional,
        "obligations_open_required": open_required,
    }


def _expected_boundary_id(args: argparse.Namespace) -> str:
    """Boundary id from the externally provided boundary, never the claim."""
    boundary_path = getattr(args, "boundary", "")
    if not boundary_path:
        raise GraphError(
            "promotion verification needs --boundary "
            "(materialized advancement boundary, not the claim's own word)"
        )
    return str(_read(Path(boundary_path), "boundary").get("boundary_id", ""))


def _verify_claim_against_boundary(
    args: argparse.Namespace, graph: dict, claim: dict, digest: str
) -> None:
    """Bind a promotion claim to an externally established boundary.

    The claim may not declare its own expected boundary: every binding
    below is anchored in the materialized boundary file (and, when given,
    the boundary template). A genuine PASS evaluated under a weaker or
    different boundary fails here, as does any digest or member mismatch.
    Verdict semantics stay in the owner-native MNCS evaluator; this only
    proves the claim was produced under the exact declared boundary and
    is correctly bound to this graph.
    """
    boundary_path = getattr(args, "boundary", "")
    if not boundary_path:
        raise GraphError(
            "promotion verification needs --boundary "
            "(materialized advancement boundary)"
        )
    boundary_raw = Path(boundary_path).read_bytes()
    try:
        boundary = json.loads(boundary_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GraphError(f"boundary {boundary_path}: cannot read: {exc}") from exc
    if not isinstance(boundary, dict):
        raise GraphError(f"boundary {boundary_path}: must be a JSON object")
    if boundary.get("schema_version") != BOUNDARY_SCHEMA:
        raise GraphError(
            f"boundary schema_version must be {BOUNDARY_SCHEMA}, "
            f"got {boundary.get('schema_version')!r}"
        )
    if boundary.get("subject_repository") != GRAPH_SUBJECT_REPOSITORY:
        raise GraphError(
            "boundary is not a family graph boundary: "
            f"subject_repository is {boundary.get('subject_repository')!r}"
        )
    declared = boundary.get("graph")
    if not isinstance(declared, dict):
        raise GraphError("boundary declares no graph")
    if declared.get("digest") != digest:
        raise GraphError("boundary declares a different graph digest")
    member_pairs = {(m.get("repository"), m.get("commit")) for m in graph["members"]}
    declared_pairs = {
        (m.get("repository"), m.get("commit"))
        for m in declared.get("members", [])
        if isinstance(m, dict)
    }
    if declared_pairs != member_pairs:
        raise GraphError("boundary graph members do not match the graph members")

    refs = {}
    for ref in claim.get("references", []):
        if isinstance(ref, dict) and isinstance(ref.get("kind"), str):
            refs[ref["kind"]] = ref.get("digest", "")
    boundary_digest = "sha256:" + hashlib.sha256(boundary_raw).hexdigest()
    if refs.get("promotion-boundary") != boundary_digest:
        raise GraphError(
            "promotion claim was not evaluated under the provided boundary "
            "(boundary digest mismatch)"
        )
    promotion = claim.get("promotion", {})
    if not isinstance(promotion, dict):
        raise GraphError("promotion claim carries no promotion extension")
    if promotion.get("boundary_id") != boundary.get("boundary_id"):
        raise GraphError(
            "promotion claim boundary_id "
            f"{promotion.get('boundary_id')!r} does not match boundary "
            f"{boundary.get('boundary_id')!r}"
        )
    required_ids = [
        e.get("check_id")
        for e in boundary.get("required_evidence", [])
        if isinstance(e, dict) and e.get("check_id") != claim.get("id")
    ]
    if promotion.get("required_total") != len(required_ids):
        raise GraphError(
            "promotion claim required_total "
            f"{promotion.get('required_total')!r} does not match the boundary "
            f"({len(required_ids)} required checks)"
        )

    template_path = getattr(args, "boundary_template", "")
    if template_path:
        template = _read(Path(template_path), "boundary template")
        stripped = {k: v for k, v in boundary.items() if k != "graph"}
        template_core = {k: v for k, v in template.items() if k != "graph"}
        if stripped != template_core:
            raise GraphError(
                "materialized boundary is not the template plus the graph "
                "declaration (template mismatch)"
            )

    if refs.get("authority-map"):
        map_path = getattr(args, "authority_map", "")
        if not map_path:
            raise GraphError(
                "promotion claim binds an authority map that was not provided"
            )
        map_raw = Path(map_path).read_bytes()
        if "sha256:" + hashlib.sha256(map_raw).hexdigest() != refs["authority-map"]:
            raise GraphError("authority map bytes do not match the claim reference")
        authority_map = _read_json_no_dupes(Path(map_path), "authority map")
        if authority_map.get("schema_version") != AUTHORITY_MAP_SCHEMA:
            raise GraphError(
                f"authority map schema_version must be {AUTHORITY_MAP_SCHEMA}"
            )
        bindings = authority_map.get("authorities")
        if not isinstance(bindings, dict) or not bindings:
            raise GraphError("authority map carries no bindings")
        known = {
            e.get("check_id")
            for group in ("required_evidence", "optional_evidence")
            for e in boundary.get(group, [])
            if isinstance(e, dict)
        }
        for check_id, binding in bindings.items():
            if check_id not in known:
                raise GraphError(
                    f"authority map binds unexpected check id {check_id!r}"
                )
            if not isinstance(binding, dict):
                raise GraphError(
                    f"authority map binding for {check_id!r} must be an object"
                )
            for field in ("provider", "authority"):
                if not binding.get(field) or not isinstance(binding[field], str):
                    raise GraphError(
                        f"authority map binding for {check_id!r} needs {field}"
                    )
        for group in ("required_evidence", "optional_evidence"):
            for entry in boundary.get(group, []):
                if not isinstance(entry, dict):
                    continue
                check_id = entry.get("check_id")
                binding = bindings.get(check_id)
                if binding is None:
                    raise GraphError(
                        f"authority map is missing a binding for {check_id!r}"
                    )
                if binding.get("authority") != entry.get("authority"):
                    raise GraphError(
                        f"authority map binds {check_id!r} to "
                        f"{binding.get('authority')!r}, boundary requires "
                        f"{entry.get('authority')!r}"
                    )
    print(
        f"boundary {boundary.get('boundary_id')} verified for graph {digest[:12]} "
        f"({len(required_ids)} required checks)"
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
        _verify_claim_against_boundary(args, graph, claim, digest)
        errors = lib.validate_promotion_claim(
            claim,
            boundary_id=_expected_boundary_id(args),
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


def _load_checks(checks_dir: Path) -> dict[str, dict]:
    """Load check-result documents keyed by check id (filename stem)."""
    checks: dict[str, dict] = {}
    if not checks_dir.is_dir():
        raise GraphError(f"checks directory not found: {checks_dir}")
    for path in sorted(checks_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GraphError(f"check {path}: cannot read: {exc}") from exc
        if not isinstance(doc, dict):
            raise GraphError(f"check {path}: must be a JSON object")
        check_id = str(doc.get("id", path.stem))
        if check_id in checks:
            raise GraphError(f"duplicate check id: {check_id}")
        checks[check_id] = doc
    return checks


def _load_obligations(obligations_dir: Path) -> list[dict]:
    """Load obligation records (structural read; semantics stay owner-side)."""
    if not obligations_dir.is_dir():
        raise GraphError(f"obligations directory not found: {obligations_dir}")
    records: list[dict] = []
    for path in sorted(obligations_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GraphError(f"obligation {path}: cannot read: {exc}") from exc
        if not isinstance(doc, dict):
            raise GraphError(f"obligation {path}: must be a JSON object")
        records.append(doc)
    return records


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
        boundary=args.boundary,
        boundary_template=args.boundary_template,
        authority_map=args.authority_map,
    )
    try:
        cmd_verify(verify_args)
    except GraphError as exc:
        refusals.append(str(exc))

    try:
        boundary = _read(Path(args.boundary), "boundary")
        claim_doc = json.loads(Path(args.promotion_result).read_bytes().decode())
        checks = _load_checks(Path(args.checks_dir))
        obligations = _load_obligations(Path(args.obligations_dir))
        acceptance = acceptance_record(
            claim=claim_doc,
            boundary=boundary,
            checks=checks,
            obligations=obligations,
        )
    except (GraphError, OSError, ValueError) as exc:
        refusals.append(f"cannot project acceptance tier: {exc}")
        acceptance = {"tier": "refused"}
    if acceptance.get("tier") == "refused":
        refusals.append("acceptance tier projects refused for a PASS claim")

    if refusals:
        print("advancement REFUSED:")
        for refusal in refusals:
            print(f"  - {refusal}")
        return 2

    member_commit = {m["name"]: m["commit"] for m in graph["members"]}
    proposed = json.loads(json.dumps(fixed))
    for entry in proposed["repositories"]:
        entry["revision"] = member_commit[entry["name"]]
    if sha256_hex(canonical_bytes(proposed)) == sha256_hex(canonical_bytes(fixed)):
        refusals.append(
            "no member revision changes: the accepted contracts already "
            "reflect this constellation; nothing to advance"
        )
    if refusals:
        print("advancement REFUSED:")
        for refusal in refusals:
            print(f"  - {refusal}")
        return 2
    out_contracts = Path(args.output_contracts)
    out_contracts.parent.mkdir(parents=True, exist_ok=True)
    before = json.dumps(fixed, indent=2, sort_keys=True).splitlines()
    after = json.dumps(proposed, indent=2, sort_keys=True).splitlines()
    out_contracts.write_text("\n".join(after) + "\n", encoding="utf-8")

    accepted = json.loads(json.dumps(graph))
    accepted["status"] = "accepted"
    accepted["acceptance"] = acceptance
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
    verify_parser.add_argument("--boundary", default="")
    verify_parser.add_argument("--boundary-template", default="")
    verify_parser.add_argument("--authority-map", default="")

    advance_parser = subparsers.add_parser("advance", help="propose the accepted state")
    advance_parser.add_argument("--graph", required=True)
    advance_parser.add_argument("--fixed", required=True)
    advance_parser.add_argument("--promotion-result", required=True)
    advance_parser.add_argument("--lib-dir", required=True)
    advance_parser.add_argument("--coherence", required=True)
    advance_parser.add_argument("--commons-record", required=True)
    advance_parser.add_argument("--commons-root", required=True)
    advance_parser.add_argument("--boundary", required=True)
    advance_parser.add_argument("--boundary-template", default="")
    advance_parser.add_argument("--authority-map", default="")
    advance_parser.add_argument("--checks-dir", required=True)
    advance_parser.add_argument("--obligations-dir", required=True)
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
