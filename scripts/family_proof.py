#!/usr/bin/env python3
"""Durable acceptance proof bundle: build the closure, replay it cold.

Build (``build-proof``) assembles every artifact behind an accepted
family graph into one content-addressed bundle directory plus a proof
manifest (``proof.json``) whose ``proof_digest`` covers the closure.
Replay (``verify-accepted``) re-verifies the bundle without trusting
the machine that produced it: digests, schemas, graph identity, the
exact advancement boundary and template, the owner-native MNCS
promotion claim (re-executed), the Commons ChangeSet (owner
validator), the predecessor chain, and the tool/generator revisions.

Orchestration only: verdict semantics stay in the owner-native MNCS
evaluator, obligation semantics in MNCDS, relationship semantics in
Commons. This module checks bindings, digests, and document linkage.

Bundle layout (all refs bundle-relative; no absolute paths)::

    proof.json               manifest + proof_digest
    graph.json               related graph exactly as evaluated
    accepted-graph.json      accepted graph (status accepted + proof block)
    boundary.json            materialized advancement boundary
    boundary-template.json   boundary template bytes used
    authority-map.json
    coherence.json
    capability.json          coherence policy input used
    descriptors.json         producer descriptors used
    checks/<id>.json         owner-native check results
    obligations/*.json       member obligation records
    promotion-check.json     MNCS graph promotion claim
    commons-record.json      Commons ChangeSet (contentDigest stamped)
    contracts-before.json    fixed contracts the graph continues
    contracts-after.json     proposed accepted contracts

Usage:
  family_proof.py build-proof --graph GRAPH.json --accepted-graph A.json
      --coherence COH.json --boundary B.json --boundary-template T.json
      --authority-map AM.json --capability CAP.json --descriptors DESC.json
      --checks-dir DIR --obligations-dir DIR --promotion-result P.json
      --commons-record C.json --fixed FIXED --proposed-contracts PROP.json
      --orchestrator-revision SHA --evaluator-path EVAL --commons-root CROOT
      [--previous-proof DIR] --output-dir DIR
  family_proof.py verify-accepted --proof DIR --boundary-template T.json
      --evaluator EVAL --commons-root CROOT [--lib-dir DIR]
      [--coherence-path PATH] [--checkouts-root DIR] [--previous-proof DIR]
  family_proof.py publish-commons --proof DIR --commons-checkout DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / ".." / "lib"))
import family_graph
import mncs_actions as lib
from family_graph import GraphError

PROOF_SCHEMA = "mncs-actions.family-acceptance-proof/1"
PROOF_DIGEST_LABEL = "sha256:"
ARTIFACT_ROLES = {
    "graph.json": "graph-related",
    "accepted-graph.json": "graph-accepted",
    "boundary.json": "materialized-boundary",
    "boundary-template.json": "boundary-template",
    "authority-map.json": "authority-map",
    "coherence.json": "coherence-report",
    "capability.json": "coherence-capability",
    "descriptors.json": "producer-descriptors",
    "promotion-check.json": "promotion-claim",
    "commons-record.json": "commons-changeset",
    "contracts-before.json": "contracts-before",
    "contracts-after.json": "contracts-after",
}

# Check/obligation roles are assigned by directory; every role string must
# stay in sync with schemas/family-acceptance-proof.schema.json.
CHECK_ROLE = "check-result"
OBLIGATION_ROLE = "obligation"


def _read(path: Path, label: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GraphError(f"{label} {path}: cannot read: {exc}") from exc
    if not isinstance(doc, dict):
        raise GraphError(f"{label} {path}: must be a JSON object")
    return doc


def _sha40(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise GraphError(f"{label} must be a 40-hex revision")
    try:
        return bytes.fromhex(value).hex()
    except ValueError as exc:
        raise GraphError(f"{label} must be lowercase hex") from exc


def _digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _no_temp_paths(value: object, where: str) -> None:
    """Refuse absolute or machine-local filesystem paths in trust fields."""
    bad: list[str] = []

    def _walk(node: object) -> None:
        if isinstance(node, str):
            if node.startswith(
                ("/tmp/", "/home/", "/Users/", "/var/", "/private/", "\\\\")
            ) or (len(node) > 2 and node[1] == ":" and node[2] in ("\\", "/")):
                bad.append(node[:80])
        elif isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            for item in node.values():
                _walk(item)

    _walk(value)
    if bad:
        raise GraphError(
            f"{where} carries machine-local paths (not durable): {bad[:3]}"
        )


def _proof_digest(manifest: dict) -> str:
    core = {k: v for k, v in manifest.items() if k != "proof_digest"}
    return lib.sha256_hex(lib.canonical_bytes(core))


def _tool_digests(evaluator_path: Path, commons_root: Path) -> dict[str, str]:
    """Digest the exact tool files invoked for this proof closure."""
    here = Path(__file__).resolve().parent
    files = {
        "mncs_actions_lib": here / ".." / "lib" / "mncs_actions.py",
        "family_graph": here / "family_graph.py",
        "family_coherence": here / "family_coherence.py",
        "promotion_evaluator": evaluator_path,
        "commons_validation": commons_root / "src" / "mncs_commons" / "validation.py",
        "commons_family": commons_root / "src" / "mncs_commons" / "family.py",
    }
    digests = {}
    for key, path in files.items():
        if not path.is_file():
            raise GraphError(f"proof tool file missing: {key} ({path})")
        digests[key] = _digest_file(path)
    return digests


def _commons_content_digest(record: dict, commons_root: Path) -> tuple[dict, str]:
    """Stamp the owner-defined content digest onto a ChangeSet record.

    Uses the owner-native Commons canonicalization (never the transport
    canonical form): a digest computed any other way would be meaningless
    to Commons validators and stores.
    """
    sys.path.insert(0, str(Path(commons_root) / "src"))
    try:
        from mncs_commons.canonical import canonical_digest
        from mncs_commons.validation import validate_record
    finally:
        try:
            sys.path.remove(str(Path(commons_root) / "src"))
        except ValueError:
            pass
    stamped = json.loads(json.dumps(record))
    stamped["contentDigest"] = canonical_digest(
        {k: v for k, v in stamped.items() if k != "contentDigest"}
    )
    report = validate_record(stamped)
    if not report.valid:
        raise GraphError(
            "stamped Commons record invalid: "
            + "; ".join(str(d) for d in report.diagnostics[:3])
        )
    digest = stamped["contentDigest"]
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise GraphError("Commons canonical digest is not a sha256: identity")
    return stamped, digest


def cmd_build_proof(args: argparse.Namespace) -> int:
    """Assemble the durable acceptance proof bundle. Refuses on any gap."""
    out = Path(args.output_dir)
    if out.exists() and any(out.iterdir()):
        raise GraphError(f"proof output directory must be empty: {out}")

    graph = _read(Path(args.graph), "graph")
    accepted = _read(Path(args.accepted_graph), "accepted graph")
    family_graph._check_digest(graph, "graph")
    if accepted.get("status") != "accepted":
        raise GraphError(
            f"accepted graph input has status {accepted.get('status')!r}, not accepted"
        )
    if accepted.get("graph_id") != graph.get("graph_id"):
        raise GraphError("accepted graph is for a different graph id")
    if accepted.get("digest") != graph.get("digest"):
        raise GraphError("accepted graph carries a different graph digest")
    if "promotion" not in graph or graph["promotion"].get("verdict") != "PASS":
        raise GraphError("proof bundle requires a PASS promotion reference")
    _no_temp_paths(graph, "graph")
    _no_temp_paths(accepted, "accepted graph")

    # Re-verify the closure with the same read-only checks advance ran,
    # now anchored on the explicit boundary inputs.
    verify_args = argparse.Namespace(
        graph=args.graph,
        fixed=args.fixed,
        promotion_result=args.promotion_result,
        lib_dir=str(Path(__file__).resolve().parent / ".." / "lib"),
        coherence=args.coherence,
        commons_record=args.commons_record,
        commons_root=args.commons_root,
        boundary=args.boundary,
        boundary_template=args.boundary_template,
        authority_map=args.authority_map,
    )
    family_graph.cmd_verify(verify_args)

    boundary_raw = Path(args.boundary).read_bytes()
    boundary = json.loads(boundary_raw.decode("utf-8"))
    template_raw = Path(args.boundary_template).read_bytes()
    template = json.loads(template_raw.decode("utf-8"))
    stripped = {k: v for k, v in boundary.items() if k != "graph"}
    template_core = {k: v for k, v in template.items() if k != "graph"}
    if stripped != template_core:
        raise GraphError("materialized boundary is not the template plus graph")
    coherence = _read(Path(args.coherence), "coherence")
    if coherence.get("graph_digest") != graph["digest"]:
        raise GraphError("coherence report is for a different graph")
    if coherence.get("blockers"):
        raise GraphError(
            f"coherence report carries blockers: {coherence['blockers'][:2]}"
        )
    claim = _read(Path(args.promotion_result), "promotion claim")
    if claim.get("verdict") != "PASS":
        raise GraphError("promotion claim verdict is not PASS")
    checks = family_graph._load_checks(Path(args.checks_dir))
    obligations = family_graph._load_obligations(Path(args.obligations_dir))
    acceptance = family_graph.acceptance_record(
        claim=claim, boundary=boundary, checks=checks, obligations=obligations
    )
    if acceptance["tier"] not in ("core", "full"):
        raise GraphError("acceptance tier is not core or full")
    if "acceptance" in accepted:
        if accepted["acceptance"].get("tier") != acceptance["tier"]:
            raise GraphError(
                "accepted graph acceptance tier disagrees with recomputation"
            )
    else:
        # Reaffirmation path: the accepted file predates acceptance
        # records (or was never enriched). The bundle copy gains the
        # recomputed record; the repository file is left for human review.
        accepted = {**accepted, "acceptance": acceptance}

    members = [
        {"name": m["name"], "repository": m["repository"], "commit": m["commit"]}
        for m in graph["members"]
    ]
    by_name = {m["name"]: m for m in members}
    for role, name in (("evaluator", "mncs-standard"), ("commons", "commons")):
        if name not in by_name:
            raise GraphError(f"graph has no {role} member {name!r}")
    producer_revision = str(claim.get("producer_revision", ""))
    if not producer_revision:
        raise GraphError("promotion claim carries no producer_revision")
    _sha40(producer_revision, "promotion claim producer_revision")
    if producer_revision != by_name["mncs-standard"]["commit"]:
        raise GraphError(
            "promotion evaluator revision is not the evaluated MNCS member"
        )
    orchestrator_revision = _sha40(args.orchestrator_revision, "orchestrator revision")

    fixed_raw = Path(args.fixed).read_bytes()
    proposed_raw = Path(args.proposed_contracts).read_bytes()
    fixed = json.loads(fixed_raw.decode("utf-8"))
    proposed = json.loads(proposed_raw.decode("utf-8"))
    if lib.sha256_hex(lib.canonical_bytes(fixed)) != graph["base"]["contract_digest"]:
        raise GraphError("contracts-before does not match the graph base")
    after_names = {e["name"] for e in proposed.get("repositories", [])}
    member_commits = {m["name"]: m["commit"] for m in members}
    if after_names != set(member_commits) - {"mncs-actions"}:
        raise GraphError("contracts-after member set does not match graph subjects")
    for entry in proposed.get("repositories", []):
        if entry.get("revision") != member_commits[entry["name"]]:
            raise GraphError(
                f"contracts-after revision for {entry['name']} "
                "does not match the graph member"
            )

    # Commons record: owner-validate, then stamp the owner content digest.
    commons_record = _read(Path(args.commons_record), "commons record")
    stamped_record, commons_digest = _commons_content_digest(
        commons_record, Path(args.commons_root)
    )
    details = stamped_record.get("details", {})
    based = {
        (r.get("repository"), r.get("commit")) for r in details.get("baseRevisions", [])
    }
    if based != {(m["repository"], m["commit"]) for m in members}:
        raise GraphError("commons base revisions do not cover the graph members")
    promotes = [
        i for i in details.get("references", []) if i.get("group") == "promotes"
    ]
    if len(promotes) != 1:
        raise GraphError("commons record must carry exactly one promotes edge")
    claim_digest = _digest_file(Path(args.promotion_result))
    if promotes[0]["reference"].get("contentDigest") != claim_digest:
        raise GraphError("commons promotes edge does not bind the promotion bytes")
    if details.get("predecessorGraph") != graph["base"]["digest"]:
        raise GraphError("commons record does not chain the accepted predecessor")

    # Assemble the closed file inventory.
    artifacts: list[dict] = []

    def _add(rel: str, raw: bytes, role: str) -> None:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(raw)
        artifacts.append({"role": role, "ref": rel, "digest": _digest_bytes(raw)})

    _add("graph.json", Path(args.graph).read_bytes(), "graph-related")
    _add("boundary.json", boundary_raw, "materialized-boundary")
    _add("boundary-template.json", template_raw, "boundary-template")
    _add("authority-map.json", Path(args.authority_map).read_bytes(), "authority-map")
    _add("coherence.json", Path(args.coherence).read_bytes(), "coherence-report")
    _add("capability.json", Path(args.capability).read_bytes(), "coherence-capability")
    _add(
        "descriptors.json",
        Path(args.descriptors).read_bytes(),
        "producer-descriptors",
    )
    _add(
        "promotion-check.json",
        Path(args.promotion_result).read_bytes(),
        "promotion-claim",
    )
    _add(
        "commons-record.json",
        json.dumps(stamped_record, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        "commons-changeset",
    )
    _add("contracts-before.json", fixed_raw, "contracts-before")
    _add("contracts-after.json", proposed_raw, "contracts-after")
    for entry in graph.get("evidence", []):
        src = Path(args.checks_dir) / entry["path"]
        if not src.is_file():
            raise GraphError(f"bundle evidence missing: {entry['path']}")
        rel = f"checks/{entry['path']}"
        _add(rel, src.read_bytes(), CHECK_ROLE)
    for path in sorted(Path(args.obligations_dir).glob("*.json")):
        _add(f"obligations/{path.name}", path.read_bytes(), OBLIGATION_ROLE)
    # Closed inventory: every check file on disk must be claimed evidence.
    for path in sorted((out / "checks").rglob("*.json")):
        rel = path.relative_to(out).as_posix()
        if rel not in {a["ref"] for a in artifacts}:
            raise GraphError(f"unlisted bundle file: {rel}")

    predecessor = {
        "graph_id": graph["base"]["graph_id"],
        "graph_digest": graph["base"]["digest"],
        "proof_digest": None,
    }
    if getattr(args, "previous_proof", ""):
        previous = _read(Path(args.previous_proof) / "proof.json", "previous proof")
        if previous.get("graph_digest") != predecessor["graph_digest"]:
            raise GraphError("previous proof is for a different predecessor graph")
        predecessor["proof_digest"] = previous.get("proof_digest")
    else:
        # First bundle after the proof model exists: the predecessor was
        # accepted before bundles existed, so there is nothing to chain.
        # The null link plus this note is the documented genesis case.
        predecessor["note"] = (
            f"predecessor {graph['base']['graph_id']} accepted before "
            "the proof model existed; no prior bundle to chain"
        )

    tool_digests = _tool_digests(Path(args.evaluator_path), Path(args.commons_root))
    manifest = {
        "schema_version": PROOF_SCHEMA,
        "graph_id": graph["graph_id"],
        "graph_digest": graph["digest"],
        "predecessor": predecessor,
        "acceptance": acceptance,
        "subject_members": sorted(members, key=lambda m: m["name"]),
        "generators": {
            "orchestrator": {
                "repository": "epi13/mncs-actions",
                "commit": orchestrator_revision,
            },
            "evaluator": {
                "repository": "epi13/machine-native-complexity-standard",
                "commit": producer_revision,
                "contract_revision": str(
                    claim.get("contract_revision", "mncs-promotion-boundary/0.1")
                ),
            },
            "commons_validator": {
                "repository": "epi13/MNCS-Commons",
                "commit": by_name["commons"]["commit"],
            },
            "tool_digests": tool_digests,
        },
        "boundary": {
            "boundary_id": boundary.get("boundary_id"),
            "contract_revision": boundary.get("schema_version"),
            "digest": _digest_bytes(boundary_raw),
            "template_digest": _digest_bytes(template_raw),
        },
        "authority_map_digest": _digest_file(Path(args.authority_map)),
        "contracts": {
            "before_digest": lib.sha256_hex(lib.canonical_bytes(fixed)),
            "after_digest": lib.sha256_hex(lib.canonical_bytes(proposed)),
        },
        "commons": {
            "changeset_id": details.get("changesetId", ""),
            "content_digest": commons_digest,
        },
        "promotion": {"verdict": "PASS", "digest": claim_digest},
        "coherence": {
            "blockers": 0,
            "movements": len(coherence.get("movements", [])),
            "digest": _digest_file(Path(args.coherence)),
        },
        "accepted_graph_digest": "",
        "artifacts": sorted(artifacts, key=lambda a: a["ref"]),
    }
    _no_temp_paths(manifest, "proof manifest")

    # Two-way accepted-graph binding without circularity: the manifest
    # covers the accepted bytes minus the proof block; the accepted file
    # then carries the proof digest. Replay checks both directions.
    accepted_core = json.loads(json.dumps(accepted))
    accepted_core.pop("proof", None)
    accepted_core["status"] = "accepted"
    manifest["accepted_graph_digest"] = lib.sha256_hex(
        lib.canonical_bytes(accepted_core)
    )
    manifest["proof_digest"] = _proof_digest(manifest)
    (out / "proof.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    final_accepted = json.loads(json.dumps(accepted_core))
    final_accepted["proof"] = {"digest": manifest["proof_digest"], "ref": "proof.json"}
    (out / "accepted-graph.json").write_text(
        json.dumps(final_accepted, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"proof {manifest['graph_id']} tier={acceptance['tier']} "
        f"-> {out} ({manifest['proof_digest'][:12]})"
    )
    return 0


PUBLISH_CHANGESETS_DIR = Path("family/changesets")


def cmd_publish_commons(args: argparse.Namespace) -> int:
    """Stage a proof bundle's ChangeSet for publication in Commons.

    Transport only, append-only: re-derives the owner content digest
    with the owner-native Commons code from the target checkout,
    checks the manifest binding, and writes the bundle bytes verbatim
    to ``family/changesets/<changeset-id>.json`` inside the checkout.
    An existing different record refuses (never overwrite published
    history); identical bytes are a no-op. Merging the staged file
    stays human governance; Commons validators decide validity.
    """
    proof_dir = Path(args.proof)
    manifest = _read(proof_dir / "proof.json", "proof manifest")
    if manifest.get("schema_version") != PROOF_SCHEMA:
        raise GraphError(
            f"proof schema_version must be {PROOF_SCHEMA}, "
            f"got {manifest.get('schema_version')!r}"
        )
    graph_id = manifest.get("graph_id", "")
    expected_id = f"changeset.{graph_id}"
    if manifest.get("commons", {}).get("changeset_id") != expected_id:
        raise GraphError("manifest commons binding is not for this graph")
    try:
        record_raw = (proof_dir / "commons-record.json").read_bytes()
    except OSError as exc:
        raise GraphError(f"bundle commons record unreadable: {exc}") from exc
    try:
        record = json.loads(record_raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise GraphError(f"bundle commons record is not JSON: {exc}") from exc
    details = record.get("details", {})
    if details.get("changesetId") != expected_id:
        raise GraphError("bundle commons record id does not match the manifest")
    checkout = Path(args.commons_checkout)
    if not (checkout / "src" / "mncs_commons" / "validation.py").is_file():
        raise GraphError(f"not a Commons checkout: {checkout}")
    _no_temp_paths(record, "commons record")
    _no_temp_paths(manifest.get("commons", {}), "manifest commons binding")
    _stamped, digest = _commons_content_digest(record, checkout)
    if digest != manifest["commons"].get("content_digest"):
        raise GraphError(
            "recomputed Commons content digest does not match the manifest binding"
        )
    head = None
    if (checkout / ".git").exists():
        try:
            head = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise GraphError(f"cannot resolve Commons checkout HEAD: {exc}") from exc
        recorded = manifest.get("generators", {}).get("commons_validator", {})
        if head != recorded.get("commit"):
            raise GraphError(
                "Commons checkout is not at the recorded validator revision "
                f"(checkout {head[:12] if head else '?'}, "
                f"recorded {str(recorded.get('commit', ''))[:12]})"
            )
    dest = checkout / PUBLISH_CHANGESETS_DIR / f"{expected_id}.json"
    if dest.is_file():
        if dest.read_bytes() != record_raw:
            raise GraphError(
                f"refusing to overwrite published {dest}: bytes differ "
                "(published history is append-only)"
            )
        print(f"commons publication already staged: {dest}")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(record_raw)
    print(
        f"commons publication staged: {dest} "
        f"({manifest['commons']['content_digest'][:19]}...)"
    )
    return 0


class _Refusals:
    """Collect replay refusals; PASS only when empty (fail closed)."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, message: str) -> None:
        self.items.append(message)

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.items.append(message)

    def extend(self, prefix: str, errors: list[str]) -> None:
        for error in errors:
            self.items.append(f"{prefix}: {error}")


def _resolve(proof_dir: Path, ref: str, refusals: _Refusals) -> Path | None:
    """Resolve a bundle-relative ref; refuse escapes and missing files."""
    if not ref or ref.startswith("/") or ".." in Path(ref).parts:
        refusals.add(f"artifact ref escapes the bundle: {ref!r}")
        return None
    path = proof_dir / ref
    if not path.is_file():
        refusals.add(f"bundle artifact missing: {ref}")
        return None
    return path


def _tool_bytes_ok(label: str, path: Path, expected: str, refusals: _Refusals) -> None:
    if not path.is_file():
        refusals.add(f"replay tool missing: {label} ({path})")
        return
    actual = _digest_file(path)
    if actual != expected:
        refusals.add(
            f"replay tool digest mismatch: {label} "
            f"(invoked {actual[:12]}, proof records {expected[:19]}...)"
        )


def _run_evaluator(
    evaluator: Path,
    *,
    boundary: Path,
    authority_map: Path,
    checks: list[Path],
    obligations: list[Path],
    graph: Path,
    claim: dict,
    out: Path,
    refusals: _Refusals,
) -> dict | None:
    """Re-execute the owner-native MNCS evaluator over bundle bytes."""
    cmd = [
        sys.executable,
        str(evaluator),
        "--boundary",
        str(boundary),
        "--authority-map",
        str(authority_map),
        *(["--checks", *[str(p) for p in checks]] if checks else []),
        *(["--obligations", *[str(p) for p in obligations]] if obligations else []),
        "--subject-graph",
        str(graph),
        "--check-id",
        str(claim.get("id", "")),
        "--provider",
        str(claim.get("provider", "")),
        "--contract-revision",
        str(claim.get("contract_revision", "")),
        "--producer-revision",
        str(claim.get("producer_revision", "")),
        "--output",
        str(out),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        refusals.add(f"evaluator execution failed: {exc}")
        return None
    if proc.returncode != 0:
        refusals.add(
            "evaluator rerun refused the closure: "
            + (proc.stderr.strip().splitlines() or ["exit " + str(proc.returncode)])[
                -1
            ][:200]
        )
        return None
    try:
        rerun = json.loads(out.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        refusals.add(f"evaluator rerun produced no claim: {exc}")
        return None
    if not isinstance(rerun, dict):
        refusals.add("evaluator rerun claim is not an object")
        return None
    return rerun


def _rerun_coherence(
    coherence_tool: Path,
    *,
    proof_dir: Path,
    manifest: dict,
    graph: dict,
    checkouts_root: Path,
    refusals: _Refusals,
) -> None:
    """Re-execute coherence over exact-revision checkouts; compare verdicts."""
    by_ref = {a["ref"]: a for a in manifest["artifacts"]}
    checkout_args: list[str] = []
    for member in graph["members"]:
        checkout = checkouts_root / member["name"]
        if not checkout.is_dir():
            refusals.add(f"replay checkout missing for member {member['name']}")
            return
        checkout_args.append(f"{member['name']}={checkout}")
    coh_ref = _artifact_ref(by_ref, "coherence-report", refusals)
    graph_ref = _artifact_ref(by_ref, "graph-related", refusals)
    cap_ref = _artifact_ref(by_ref, "coherence-capability", refusals)
    desc_ref = _artifact_ref(by_ref, "producer-descriptors", refusals)
    map_ref = _artifact_ref(by_ref, "authority-map", refusals)
    boundary_ref = _artifact_ref(by_ref, "materialized-boundary", refusals)
    if not all([coh_ref, graph_ref, cap_ref, desc_ref, map_ref, boundary_ref]):
        return
    try:
        recorded = json.loads((proof_dir / coh_ref).read_bytes().decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        refusals.add(f"recorded coherence report unreadable: {exc}")
        return
    if not isinstance(recorded, dict):
        refusals.add("recorded coherence report is not an object")
        return
    out_path = proof_dir / ".replay-coherence.json"
    _tool_bytes_ok(
        "family_coherence",
        coherence_tool,
        manifest.get("generators", {})
        .get("tool_digests", {})
        .get("family_coherence", ""),
        refusals,
    )
    if refusals.items:
        return
    cmd = [
        sys.executable,
        str(coherence_tool),
        "--graph",
        str(proof_dir / graph_ref),
        *[arg for pair in checkout_args for arg in ("--checkout", pair)],
        "--capability",
        str(proof_dir / cap_ref),
        "--descriptors",
        str(proof_dir / desc_ref),
        "--authority-map",
        str(proof_dir / map_ref),
        "--boundary",
        str(proof_dir / boundary_ref),
        "--check-id",
        "promotion-boundary",
        "--output",
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        refusals.add(f"coherence rerun failed: {exc}")
        return
    if proc.returncode != 0:
        refusals.add(
            "coherence rerun refused: "
            + (proc.stderr.strip().splitlines() or ["exit " + str(proc.returncode)])[
                -1
            ][:200]
        )
        return
    try:
        rerun = json.loads(out_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        refusals.add(f"coherence rerun produced no report: {exc}")
        return
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass
    if not isinstance(rerun, dict):
        refusals.add("coherence rerun report is not an object")
        return
    if rerun.get("graph_digest") != recorded.get("graph_digest"):
        refusals.add("coherence rerun binds a different graph digest")
    if sorted(rerun.get("blockers", [])) != sorted(recorded.get("blockers", [])):
        refusals.add("coherence rerun blockers differ from the recorded report")
    old_movements = {
        (
            m.get("from"),
            m.get("to"),
            m.get("edge"),
            m.get("classification"),
            m.get("satisfied"),
        )
        for m in recorded.get("movements", [])
    }
    new_movements = {
        (
            m.get("from"),
            m.get("to"),
            m.get("edge"),
            m.get("classification"),
            m.get("satisfied"),
        )
        for m in rerun.get("movements", [])
    }
    if old_movements != new_movements:
        refusals.add("coherence rerun movements differ from the recorded report")
    old_floors = {(f.get("id"), f.get("status")) for f in recorded.get("floors", [])}
    new_floors = {(f.get("id"), f.get("status")) for f in rerun.get("floors", [])}
    if old_floors != new_floors:
        refusals.add("coherence rerun floors differ from the recorded report")
    if bool(rerun.get("cycles", {}).get("unsafe")) != bool(
        recorded.get("cycles", {}).get("unsafe")
    ):
        refusals.add("coherence rerun cycle audit differs from the recorded report")


def _artifact_ref(by_ref: dict, role: str, refusals: _Refusals) -> str | None:
    matches = [ref for ref, entry in by_ref.items() if entry["role"] == role]
    if len(matches) != 1 and role in (
        "graph-related",
        "graph-accepted",
        "materialized-boundary",
        "boundary-template",
        "authority-map",
        "coherence-report",
        "coherence-capability",
        "producer-descriptors",
        "promotion-claim",
        "commons-changeset",
        "contracts-before",
        "contracts-after",
    ):
        refusals.add(f"bundle must carry exactly one {role} artifact")
        return None
    return matches[0] if matches else None


def _git_bytes(checkouts_root: Path, member: str, rev: str, rel: str) -> bytes | None:
    """Read exact-revision file bytes from a member checkout, or None."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(checkouts_root / member), "show", f"{rev}:{rel}"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def cmd_verify_accepted(args: argparse.Namespace) -> int:
    """Independently replay an acceptance proof bundle. Exit 2 refuses."""
    refusals = _Refusals()
    proof_dir = Path(args.proof)
    try:
        manifest = json.loads((proof_dir / "proof.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"replay REFUSED: proof manifest unreadable: {exc}")
        return 2
    if not isinstance(manifest, dict):
        print("replay REFUSED: proof manifest is not an object")
        return 2
    if manifest.get("schema_version") != PROOF_SCHEMA:
        refusals.add(
            f"proof schema_version must be {PROOF_SCHEMA}, "
            f"got {manifest.get('schema_version')!r}"
        )

    # 1-2. Closed inventory first: every listed artifact present with the
    # recorded digest; no unlisted files except proof.json itself.
    listed = {}
    for entry in manifest.get("artifacts", []):
        if not isinstance(entry, dict):
            refusals.add("proof artifact entry is not an object")
            continue
        ref, digest = entry.get("ref", ""), entry.get("digest", "")
        if ref in listed:
            refusals.add(f"duplicate artifact ref: {ref!r}")
            continue
        listed[ref] = digest
        path = _resolve(proof_dir, ref, refusals)
        if path is None:
            continue
        if _digest_file(path) != digest:
            refusals.add(f"artifact digest mismatch: {ref}")
    actual_files = set()
    for path in sorted(proof_dir.rglob("*")):
        if path.is_file() and path.name not in ("proof.json", "accepted-graph.json"):
            actual_files.add(path.relative_to(proof_dir).as_posix())
    for extra in sorted(actual_files - set(listed)):
        refusals.add(f"unlisted bundle file: {extra}")
    # accepted-graph.json binds separately (see step 4); it must exist.
    accepted_path = proof_dir / "accepted-graph.json"
    if not accepted_path.is_file():
        refusals.add("bundle artifact missing: accepted-graph.json")

    # 3. Proof digest over the closure.
    if manifest.get("proof_digest") != _proof_digest(manifest):
        refusals.add("proof digest does not recompute over the closure")

    by_ref = {}
    for entry in manifest.get("artifacts", []):
        if isinstance(entry, dict) and entry.get("ref"):
            by_ref[entry["ref"]] = entry

    def _load_role(role: str) -> dict | None:
        ref = _artifact_ref(by_ref, role, refusals)
        if ref is None:
            return None
        path = _resolve(proof_dir, ref, refusals)
        if path is None:
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            refusals.add(f"bundle {role} unreadable: {exc}")
            return None
        if not isinstance(doc, dict):
            refusals.add(f"bundle {role} is not an object")
            return None
        return doc

    graph = _load_role("graph-related")
    accepted = None
    try:
        accepted = json.loads(accepted_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        refusals.add(f"bundle accepted graph unreadable: {exc}")
    boundary = _load_role("materialized-boundary")
    template_bundled = _load_role("boundary-template")
    authority_doc = _load_role("authority-map")
    map_ref = _artifact_ref(by_ref, "authority-map", refusals)
    if map_ref is not None:
        try:
            family_graph._read_json_no_dupes(proof_dir / map_ref, "authority map")
        except (OSError, UnicodeDecodeError) as exc:
            refusals.add(f"authority map unreadable: {exc}")
        except GraphError as exc:
            refusals.add(str(exc))
    coherence = _load_role("coherence-report")
    claim = _load_role("promotion-claim")
    commons_record = _load_role("commons-changeset")
    contracts_before = _load_role("contracts-before")
    contracts_after = _load_role("contracts-after")

    # 4. Graph identities + two-way accepted binding + no temp paths.
    if graph is not None:
        try:
            family_graph._check_digest(graph, "bundle graph")
        except GraphError as exc:
            refusals.add(str(exc))
        refusals.check(
            graph.get("digest") == manifest.get("graph_digest"),
            "bundle graph digest does not match the manifest",
        )
        refusals.check(
            graph.get("graph_id") == manifest.get("graph_id"),
            "bundle graph id does not match the manifest",
        )
        refusals.check(
            graph.get("status") == "related",
            f"bundle graph status is {graph.get('status')!r}, not related",
        )
        try:
            _no_temp_paths(graph, "bundle graph")
        except GraphError as exc:
            refusals.add(str(exc))
    if accepted is not None and isinstance(accepted, dict):
        core = {k: v for k, v in accepted.items() if k != "proof"}
        refusals.check(
            lib.sha256_hex(lib.canonical_bytes(core))
            == manifest.get("accepted_graph_digest"),
            "accepted graph bytes do not match the manifest binding",
        )
        refusals.check(
            accepted.get("proof", {}).get("digest") == manifest.get("proof_digest"),
            "accepted graph does not carry this proof digest",
        )
        refusals.check(
            accepted.get("proof", {}).get("ref") == "proof.json",
            "accepted graph proof ref is not proof.json",
        )
        if graph is not None:
            related_core = {
                k: v for k, v in graph.items() if k not in ("status", "acceptance")
            }
            accepted_core = {
                k: v for k, v in core.items() if k not in ("status", "acceptance")
            }
            refusals.check(
                related_core == accepted_core,
                "accepted graph diverges from the evaluated graph",
            )
            refusals.check(
                accepted.get("acceptance") == manifest.get("acceptance"),
                "accepted graph acceptance record differs from the proof",
            )
        try:
            _no_temp_paths(accepted, "accepted graph")
        except GraphError as exc:
            refusals.add(str(exc))
    try:
        _no_temp_paths(manifest, "proof manifest")
    except GraphError as exc:
        refusals.add(str(exc))

    members: list[dict] = []
    if graph is not None:
        members = graph.get("members", [])
        if manifest.get("subject_members") != [
            {
                "name": m.get("name"),
                "repository": m.get("repository"),
                "commit": m.get("commit"),
            }
            for m in members
        ]:
            refusals.add("manifest subject members do not match the graph")
        for member in members:
            for field in ("name", "repository", "commit"):
                if not member.get(field):
                    refusals.add(f"graph member is missing {field}")
            try:
                _sha40(member.get("commit"), f"member {member.get('name')}")
            except GraphError as exc:
                refusals.add(str(exc))

    # 5. Boundary: digest, template anchor, graph declaration.
    template_raw: bytes | None = None
    if boundary is not None:
        ref = _artifact_ref(by_ref, "materialized-boundary", refusals)
        if ref is not None:
            actual = _digest_file(proof_dir / ref)
            refusals.check(
                actual == manifest.get("boundary", {}).get("digest"),
                "materialized boundary digest does not match the manifest",
            )
        refusals.check(
            boundary.get("schema_version") == "mncs-promotion-boundary/0.1",
            "boundary schema_version is not the promotion boundary contract",
        )
        refusals.check(
            boundary.get("subject_repository") == "mncs-family/graph",
            "boundary is not a family graph boundary",
        )
        refusals.check(
            boundary.get("boundary_id")
            == manifest.get("boundary", {}).get("boundary_id"),
            "boundary id does not match the manifest",
        )
        declared = boundary.get("graph", {})
        refusals.check(
            declared.get("digest") == manifest.get("graph_digest"),
            "boundary declares a different graph digest",
        )
        declared_pairs = {
            (m.get("repository"), m.get("commit"))
            for m in declared.get("members", [])
            if isinstance(m, dict)
        }
        member_pairs = {(m.get("repository"), m.get("commit")) for m in members}
        if declared_pairs != member_pairs:
            refusals.add("boundary graph members do not match the graph members")
        # Template anchor: the operator-supplied template is the trust
        # anchor; the bundle copy must equal it, and the materialized
        # boundary must equal the template plus the graph declaration.
        try:
            operator_template = json.loads(
                Path(args.boundary_template).read_bytes().decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            refusals.add(f"operator boundary template unreadable: {exc}")
            operator_template = None
        if template_bundled is not None and operator_template is not None:
            refusals.check(
                template_bundled == operator_template,
                "bundle boundary template differs from the operator template",
            )
            template_raw = Path(args.boundary_template).read_bytes()
            refusals.check(
                _digest_bytes(template_raw)
                == manifest.get("boundary", {}).get("template_digest"),
                "template digest does not match the manifest",
            )
            stripped = {k: v for k, v in boundary.items() if k != "graph"}
            template_core = {k: v for k, v in operator_template.items() if k != "graph"}
            refusals.check(
                stripped == template_core,
                "materialized boundary is not the template plus graph",
            )

    # 6. Authority map linkage (document level; evaluator rerun enforces
    # provider binding end to end).
    if authority_doc is not None and boundary is not None and claim is not None:
        map_ref = _artifact_ref(by_ref, "authority-map", refusals)
        if map_ref is not None:
            actual = _digest_file(proof_dir / map_ref)
            refusals.check(
                actual == manifest.get("authority_map_digest"),
                "authority map digest does not match the manifest",
            )
        claim_refs = {
            r["kind"]: r.get("digest", "")
            for r in claim.get("references", [])
            if isinstance(r, dict) and isinstance(r.get("kind"), str)
        }
        refusals.check(
            claim_refs.get("authority-map") == manifest.get("authority_map_digest"),
            "claim does not reference this authority map",
        )
        bindings = authority_doc.get("authorities", {})
        known = {
            e.get("check_id")
            for group in ("required_evidence", "optional_evidence")
            for e in boundary.get(group, [])
            if isinstance(e, dict)
        }
        for check_id, binding in bindings.items():
            refusals.check(
                check_id in known,
                f"authority map binds unexpected check id {check_id!r}",
            )
        for group in ("required_evidence", "optional_evidence"):
            for entry in boundary.get(group, []):
                if not isinstance(entry, dict):
                    continue
                check_id = entry.get("check_id")
                binding = bindings.get(check_id, {})
                refusals.check(
                    binding.get("authority") == entry.get("authority"),
                    f"authority binding for {check_id!r} disagrees with boundary",
                )

    # 7. Checks: schema validation (owner check-result contract via lib),
    # digest binding, evidence cross-check.
    checks: dict[str, dict] = {}
    check_files: dict[str, Path] = {}
    for entry in manifest.get("artifacts", []):
        if not isinstance(entry, dict) or entry.get("role") != CHECK_ROLE:
            continue
        path = _resolve(proof_dir, entry.get("ref", ""), refusals)
        if path is None:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            refusals.add(f"check unreadable: {entry.get('ref')}: {exc}")
            continue
        errors = lib.validate_check_result(doc)
        if errors:
            refusals.extend(f"check {entry.get('ref')} invalid", errors)
            continue
        check_id = doc.get("id", path.stem)
        if check_id in checks:
            refusals.add(f"duplicate check id: {check_id}")
            continue
        checks[check_id] = doc
        check_files[check_id] = path
    if graph is not None:
        for item in graph.get("evidence", []):
            ref = f"checks/{item.get('path', '')}"
            path = _resolve(proof_dir, ref, refusals)
            if path is None:
                continue
            # Graph evidence digests are bare hex; bundle digests are
            # sha256:-prefixed. Compare the digest bytes, not the spelling.
            actual = _digest_file(path).removeprefix("sha256:")
            expected = str(item.get("digest", "")).removeprefix("sha256:")
            refusals.check(
                actual == expected,
                f"evidence digest mismatch: {ref}",
            )

    # 8. Obligations: structural read (full lifecycle semantics are
    # re-checked by the evaluator rerun below).
    obligations: list[dict] = []
    for entry in manifest.get("artifacts", []):
        if not isinstance(entry, dict) or entry.get("role") != OBLIGATION_ROLE:
            continue
        path = _resolve(proof_dir, entry.get("ref", ""), refusals)
        if path is None:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            refusals.add(f"obligation unreadable: {entry.get('ref')}: {exc}")
            continue
        if not isinstance(doc, dict):
            refusals.add(f"obligation not an object: {entry.get('ref')}")
            continue
        subject = doc.get("subject", {})
        if (
            not isinstance(subject, dict)
            or not subject.get("repository")
            or not isinstance(subject.get("commit"), str)
            or len(subject["commit"]) != 40
        ):
            refusals.add(f"obligation lacks an exact subject: {entry.get('ref')}")
            continue
        if doc.get("status") not in ("open", "resolved", "rejected"):
            refusals.add(f"obligation has unknown status: {entry.get('ref')}")
            continue
        obligations.append(doc)

    # 9. Coherence report binding (rerun further below when checkouts exist).
    if coherence is not None:
        coh_ref = _artifact_ref(by_ref, "coherence-report", refusals)
        if coh_ref is not None:
            refusals.check(
                _digest_file(proof_dir / coh_ref)
                == manifest.get("coherence", {}).get("digest"),
                "coherence digest does not match the manifest",
            )
        refusals.check(
            coherence.get("graph_digest") == manifest.get("graph_digest"),
            "coherence report is for a different graph",
        )
        refusals.check(
            not coherence.get("blockers"),
            f"coherence report carries blockers: {coherence.get('blockers', [])[:2]}",
        )
        unmet = [
            m
            for m in coherence.get("movements", [])
            if m.get("classification") == "UNKNOWN" and not m.get("satisfied")
        ]
        refusals.check(not unmet, f"coherence has unsatisfied movements: {unmet[:2]}")
        refusals.check(
            not coherence.get("cycles", {}).get("unsafe"),
            "coherence cycle audit is unsafe",
        )
        bad_floors = [
            f.get("id")
            for f in coherence.get("floors", [])
            if f.get("status") != "satisfied"
        ]
        refusals.check(not bad_floors, f"capability floors unsatisfied: {bad_floors}")
        refusals.check(
            len(coherence.get("movements", []))
            == manifest.get("coherence", {}).get("movements"),
            "coherence movement count does not match the manifest",
        )

    # 10. Promotion claim linkage.
    if claim is not None and boundary is not None:
        claim_ref = _artifact_ref(by_ref, "promotion-claim", refusals)
        if claim_ref is not None:
            refusals.check(
                _digest_file(proof_dir / claim_ref)
                == manifest.get("promotion", {}).get("digest"),
                "promotion claim digest does not match the manifest",
            )
        if graph is not None:
            graph_promotion = str(
                graph.get("promotion", {}).get("digest", "")
            ).removeprefix("sha256:")
            manifest_promotion = str(
                manifest.get("promotion", {}).get("digest", "")
            ).removeprefix("sha256:")
            refusals.check(
                graph_promotion == manifest_promotion,
                "graph promotion reference does not match the manifest",
            )
            refusals.check(
                graph.get("promotion", {}).get("verdict")
                == manifest.get("promotion", {}).get("verdict"),
                "graph promotion verdict does not match the manifest",
            )
        refusals.check(
            claim.get("verdict") == "PASS"
            and manifest.get("promotion", {}).get("verdict") == "PASS",
            f"promotion verdict is {claim.get('verdict')!r}, not PASS",
        )
        refusals.check(
            claim.get("promotion", {}).get("graph_digest")
            == manifest.get("graph_digest"),
            "promotion claim binds a different graph digest",
        )
        refusals.check(
            claim.get("promotion", {}).get("boundary_id")
            == boundary.get("boundary_id"),
            "promotion claim boundary differs from the verified boundary",
        )
        claim_refs = {
            r["kind"]: r.get("digest", "")
            for r in claim.get("references", [])
            if isinstance(r, dict) and isinstance(r.get("kind"), str)
        }
        refusals.check(
            claim_refs.get("promotion-boundary")
            == manifest.get("boundary", {}).get("digest"),
            "promotion claim was evaluated under a different boundary",
        )
        errors = lib.validate_promotion_claim(
            claim,
            boundary_id=str(boundary.get("boundary_id", "")),
            subject_repository="mncs-family/graph",
            subject_commit=f"graph:{manifest.get('graph_digest')}",
        )
        refusals.extend("promotion claim transport", errors)

    # 11. Tool digests: every tool replay invokes must match the bundle.
    tool_digests = manifest.get("generators", {}).get("tool_digests", {})
    here = Path(__file__).resolve().parent
    invoked = {
        "mncs_actions_lib": here / ".." / "lib" / "mncs_actions.py",
        "family_graph": here / "family_graph.py",
    }
    evaluator = Path(args.evaluator)
    invoked["promotion_evaluator"] = evaluator
    commons_root = Path(args.commons_root)
    invoked["commons_validation"] = (
        commons_root / "src" / "mncs_commons" / "validation.py"
    )
    invoked["commons_family"] = commons_root / "src" / "mncs_commons" / "family.py"
    if getattr(args, "checkouts_root", ""):
        invoked["family_coherence"] = Path(args.coherence_path)
    for key, path in invoked.items():
        expected = tool_digests.get(key, "")
        if not expected:
            refusals.add(f"proof records no digest for tool {key}")
            continue
        _tool_bytes_ok(key, path, expected, refusals)

    # 12. Generator revisions: well-formed, cross-bound to claim/members.
    generators = manifest.get("generators", {})
    for role in ("orchestrator", "evaluator", "commons_validator"):
        entry = generators.get(role, {})
        for field in ("repository", "commit"):
            refusals.check(
                bool(entry.get(field)), f"generator {role} is missing {field}"
            )
        try:
            _sha40(entry.get("commit"), f"generator {role} commit")
        except GraphError as exc:
            refusals.add(str(exc))
    if claim is not None:
        refusals.check(
            claim.get("producer_revision")
            == generators.get("evaluator", {}).get("commit"),
            "claim producer revision is not the recorded evaluator revision",
        )
        refusals.check(
            generators.get("evaluator", {}).get("contract_revision")
            == claim.get("contract_revision"),
            "recorded evaluator contract differs from the claim",
        )

    # 13. Commons record: owner validation + linkage.
    if commons_record is not None:
        sys.path.insert(0, str(commons_root / "src"))
        try:
            from mncs_commons.validation import validate_record
        except ImportError as exc:
            refusals.add(f"cannot load owner Commons validator: {exc}")
            validate_record = None  # type: ignore[assignment]
        finally:
            try:
                sys.path.remove(str(commons_root / "src"))
            except ValueError:
                pass
        if validate_record is not None:
            report = validate_record(commons_record)
            if not report.valid:
                refusals.extend(
                    "commons record invalid",
                    [str(d) for d in report.diagnostics[:3]],
                )
            details = commons_record.get("details", {})
            based = {
                (r.get("repository"), r.get("commit"))
                for r in details.get("baseRevisions", [])
            }
            member_pairs = {(m.get("repository"), m.get("commit")) for m in members}
            refusals.check(
                based == member_pairs,
                "commons base revisions do not cover the graph members",
            )
            promotes = [
                i for i in details.get("references", []) if i.get("group") == "promotes"
            ]
            refusals.check(
                len(promotes) == 1,
                "commons record must carry exactly one promotes edge",
            )
            if len(promotes) == 1:
                refusals.check(
                    promotes[0]["reference"].get("contentDigest")
                    == manifest.get("promotion", {}).get("digest"),
                    "commons promotes edge does not bind the promotion bytes",
                )
            refusals.check(
                details.get("predecessorGraph")
                == manifest.get("predecessor", {}).get("graph_digest"),
                "commons record does not chain the accepted predecessor",
            )
            refusals.check(
                details.get("changesetId")
                == manifest.get("commons", {}).get("changeset_id"),
                "commons changeset id does not match the manifest",
            )
            refusals.check(
                commons_record.get("contentDigest")
                == manifest.get("commons", {}).get("content_digest"),
                "commons content digest does not match the manifest",
            )

    # 14. Contracts transition: exact member mapping, before matches base.
    if contracts_before is not None and contracts_after is not None:
        refusals.check(
            lib.sha256_hex(lib.canonical_bytes(contracts_before))
            == manifest.get("contracts", {}).get("before_digest"),
            "contracts-before digest does not match the manifest",
        )
        refusals.check(
            lib.sha256_hex(lib.canonical_bytes(contracts_after))
            == manifest.get("contracts", {}).get("after_digest"),
            "contracts-after digest does not match the manifest",
        )
        if graph is not None:
            refusals.check(
                lib.sha256_hex(lib.canonical_bytes(contracts_before))
                == graph.get("base", {}).get("contract_digest"),
                "contracts-before is not the graph base",
            )
        after_commits = {
            e.get("name"): e.get("revision")
            for e in contracts_after.get("repositories", [])
        }
        member_commits = {m.get("name"): m.get("commit") for m in members}
        member_commits.pop("mncs-actions", None)
        refusals.check(
            after_commits == member_commits,
            "contracts-after does not map exactly to the graph subjects",
        )

    # 15. Acceptance tier recomputation (same pure function as advance).
    if claim is not None and boundary is not None:
        recomputed = family_graph.acceptance_record(
            claim=claim,
            boundary=boundary,
            checks=checks,
            obligations=obligations,
        )
        refusals.check(
            recomputed == manifest.get("acceptance"),
            "acceptance record does not recompute from the closure",
        )
        refusals.check(
            recomputed.get("tier") in ("core", "full"),
            "acceptance tier is not core or full",
        )

    # 16. Predecessor linkage (+ deep check with a previous bundle).
    predecessor = manifest.get("predecessor", {})
    if graph is not None:
        refusals.check(
            graph.get("base", {}).get("digest") == predecessor.get("graph_digest"),
            "graph base does not match the manifest predecessor",
        )
        refusals.check(
            graph.get("base", {}).get("graph_id") == predecessor.get("graph_id"),
            "graph base id does not match the manifest predecessor",
        )
    if (
        not getattr(args, "previous_proof", "")
        and predecessor.get("proof_digest") is None
    ):
        refusals.check(
            isinstance(predecessor.get("note"), str) and predecessor["note"].strip(),
            "null predecessor link carries no documented reason",
        )
    if getattr(args, "previous_proof", ""):
        try:
            previous = json.loads(
                (Path(args.previous_proof) / "proof.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            refusals.add(f"previous proof unreadable: {exc}")
            previous = None
        if isinstance(previous, dict):
            refusals.check(
                previous.get("graph_digest") == predecessor.get("graph_digest"),
                "previous proof is for a different predecessor graph",
            )
            refusals.check(
                previous.get("proof_digest") == predecessor.get("proof_digest"),
                "previous proof digest does not match the predecessor link",
            )

    # 17. Evaluator rerun (owner-native verdict, bundle bytes only).
    if (
        claim is not None
        and boundary is not None
        and not refusals.items
        and evaluator.is_file()
    ):
        rerun_out = proof_dir / ".replay-promotion.json"
        try:
            rerun = _run_evaluator(
                evaluator,
                boundary=proof_dir
                / (_artifact_ref(by_ref, "materialized-boundary", refusals) or ""),
                authority_map=proof_dir
                / (_artifact_ref(by_ref, "authority-map", refusals) or ""),
                checks=[check_files[c] for c in sorted(check_files)],
                obligations=[
                    proof_dir / e["ref"]
                    for e in manifest.get("artifacts", [])
                    if isinstance(e, dict) and e.get("role") == OBLIGATION_ROLE
                ],
                graph=proof_dir
                / (_artifact_ref(by_ref, "graph-related", refusals) or ""),
                claim=claim,
                out=rerun_out,
                refusals=refusals,
            )
        finally:
            try:
                rerun_out.unlink()
            except OSError:
                pass
        if rerun is not None:
            refusals.check(
                rerun.get("verdict") == "PASS",
                f"evaluator rerun verdict is {rerun.get('verdict')!r}, not PASS",
            )
            if rerun != claim:
                refusals.add("evaluator rerun claim differs from the recorded claim")

    # 18. Coherence rerun + revision-anchored tool bytes (opt-in deep path).
    checkouts_root = getattr(args, "checkouts_root", "")
    if checkouts_root and graph is not None and not refusals.items:
        _rerun_coherence(
            Path(args.coherence_path),
            proof_dir=proof_dir,
            manifest=manifest,
            graph=graph,
            checkouts_root=Path(checkouts_root),
            refusals=refusals,
        )
        if not refusals.items:
            _anchor_revisions(Path(checkouts_root), manifest, invoked, refusals)

    if refusals.items:
        print("replay REFUSED:")
        for refusal in refusals.items:
            print(f"  - {refusal}")
        return 2
    print(
        f"replay PASS: {manifest.get('graph_id')} "
        f"tier={manifest.get('acceptance', {}).get('tier')} "
        f"proof={str(manifest.get('proof_digest', ''))[:12]}"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build-proof", help="assemble the durable acceptance proof bundle"
    )
    build_parser.add_argument("--graph", required=True)
    build_parser.add_argument("--accepted-graph", required=True)
    build_parser.add_argument("--coherence", required=True)
    build_parser.add_argument("--boundary", required=True)
    build_parser.add_argument("--boundary-template", required=True)
    build_parser.add_argument("--authority-map", required=True)
    build_parser.add_argument("--capability", required=True)
    build_parser.add_argument("--descriptors", required=True)
    build_parser.add_argument("--checks-dir", required=True)
    build_parser.add_argument("--obligations-dir", required=True)
    build_parser.add_argument("--promotion-result", required=True)
    build_parser.add_argument("--commons-record", required=True)
    build_parser.add_argument("--fixed", required=True)
    build_parser.add_argument("--proposed-contracts", required=True)
    build_parser.add_argument("--orchestrator-revision", required=True)
    build_parser.add_argument("--evaluator-path", required=True)
    build_parser.add_argument("--commons-root", required=True)
    build_parser.add_argument("--previous-proof", default="")
    build_parser.add_argument("--output-dir", required=True)

    publish_parser = subparsers.add_parser(
        "publish-commons", help="stage a bundle ChangeSet for Commons publication"
    )
    publish_parser.add_argument("--proof", required=True)
    publish_parser.add_argument("--commons-checkout", required=True)
    replay_parser = subparsers.add_parser(
        "verify-accepted", help="independently replay an acceptance proof bundle"
    )
    replay_parser.add_argument("--proof", required=True)
    replay_parser.add_argument("--boundary-template", required=True)
    replay_parser.add_argument("--evaluator", required=True)
    replay_parser.add_argument("--commons-root", required=True)
    replay_parser.add_argument("--lib-dir", default="")
    replay_parser.add_argument("--coherence-path", default="")
    replay_parser.add_argument("--checkouts-root", default="")
    replay_parser.add_argument("--previous-proof", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "lib_dir", ""):
        args.lib_dir = str(Path(__file__).resolve().parent / ".." / "lib")
    if not getattr(args, "coherence_path", ""):
        args.coherence_path = str(
            Path(__file__).resolve().parent / "family_coherence.py"
        )
    try:
        if args.command == "build-proof":
            return cmd_build_proof(args)
        if args.command == "verify-accepted":
            return cmd_verify_accepted(args)
        if args.command == "publish-commons":
            return cmd_publish_commons(args)
    except GraphError as exc:
        print(f"family proof REFUSED: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unknown command: {args.command}")


def _anchor_revisions(
    checkouts_root: Path,
    manifest: dict,
    invoked: dict[str, Path],
    refusals: _Refusals,
) -> None:
    """Prove recorded generator revisions contain the invoked tool bytes."""
    generators = manifest.get("generators", {})
    tool_digests = generators.get("tool_digests", {})
    anchors = [
        (
            "mncs-standard",
            generators.get("evaluator", {}).get("commit", ""),
            "scripts/mncs_promotion_evaluate.py",
            "promotion_evaluator",
        ),
        (
            "commons",
            generators.get("commons_validator", {}).get("commit", ""),
            "src/mncs_commons/validation.py",
            "commons_validation",
        ),
        (
            "commons",
            generators.get("commons_validator", {}).get("commit", ""),
            "src/mncs_commons/family.py",
            "commons_family",
        ),
        (
            "mncs-actions",
            generators.get("orchestrator", {}).get("commit", ""),
            "lib/mncs_actions.py",
            "mncs_actions_lib",
        ),
        (
            "mncs-actions",
            generators.get("orchestrator", {}).get("commit", ""),
            "scripts/family_graph.py",
            "family_graph",
        ),
        (
            "mncs-actions",
            generators.get("orchestrator", {}).get("commit", ""),
            "scripts/family_coherence.py",
            "family_coherence",
        ),
    ]
    for member, rev, rel, key in anchors:
        if not rev:
            refusals.add(f"no recorded revision for {key}")
            continue
        raw = _git_bytes(checkouts_root, member, rev, rel)
        if raw is None:
            refusals.add(
                f"cannot resolve {member}@{rev[:12]}:{rel} in replay checkouts"
            )
            continue
        if _digest_bytes(raw) != tool_digests.get(key, ""):
            refusals.add(f"recorded {key} bytes differ at {member}@{rev[:12]}:{rel}")
            continue
        invoked_path = invoked.get(key)
        if (
            invoked_path is not None
            and invoked_path.is_file()
            and _digest_file(invoked_path) != _digest_bytes(raw)
        ):
            refusals.add(f"invoked {key} differs from {member}@{rev[:12]}:{rel}")


if __name__ == "__main__":
    raise SystemExit(main())
