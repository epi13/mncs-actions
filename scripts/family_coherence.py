#!/usr/bin/env python3
"""Cross-repository dependency coherence for a candidate family graph.

Orchestration analysis only: reads pins declared in member checkouts,
classifies required pin movements against the candidate revisions, and
audits authority edges for unsafe cycles. Emits blockers; decides no
semantics.

Edge classes: transport/evaluator/contract pins (classified
REQUIRED/OPTIONAL/NOT_REQUIRED/UNKNOWN/CURRENT), compat references
(informational), attribution (provenance only, not analyzed).

An owner repository may override its capability rows with
promotion/capability-map.json in its own tree
({"capabilities": {<repo>: [{paths, impact}]}}); the override wins.

Usage:
  family_coherence.py --graph GRAPH.json --checkout name=path ...
      --capability family-capability.json --descriptors DESC
      [--authority-map MAP --boundary B.json --check-id ID]
      --output COHERENCE.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

KNOWN_AUTHORITIES = {
    "machine-native-complexity-standard",
    "machine-native-complexity-development-specification",
    "mncs-rights-provenance",
    "mncs-language",
    "MNCS-Commons",
    "mncs-forge-mcp",
}


class CoherenceError(RuntimeError):
    """Coherence analysis failed."""


def _read(path: Path, label: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoherenceError(f"{label} {path}: cannot read: {exc}") from exc
    if not isinstance(doc, dict):
        raise CoherenceError(f"{label} {path}: must be a JSON object")
    return doc


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CoherenceError(f"git failed in {repo}: {exc}") from exc
    if result.returncode != 0:
        raise CoherenceError(
            f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()[:200]}"
        )
    return result.stdout.strip()


def _ensure_objects(repo: Path, slug: str, *shas: str) -> None:
    for sha in shas:
        if _missing(repo, sha):
            _git(repo, "fetch", "-q", "origin", sha, "--depth", "1")


def _missing(repo: Path, sha: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", sha],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode != 0


def _diff_paths(repo: Path, slug: str, old: str, new: str) -> list[str] | None:
    """Changed paths between two revisions, or None when unresolvable."""
    try:
        _ensure_objects(repo, slug, old, new)
        out = _git(repo, "diff", "--name-only", old, new)
        return [line for line in out.splitlines() if line.strip()]
    except CoherenceError:
        return None


def _impact(paths: list[str], rows: list[dict], impacts: dict) -> str:
    """Classify a path set; unmapped paths force UNKNOWN (fail closed)."""
    if not paths:
        return "CURRENT"
    top = "NOT_REQUIRED"
    order = {"NOT_REQUIRED": 0, "OPTIONAL": 1, "REQUIRED": 2, "UNKNOWN": 3}
    for path in paths:
        impact = None
        for row in rows:
            if any(
                path == prefix or path.startswith(prefix) for prefix in row["paths"]
            ):
                impact = row["impact"]
                break
        if impact is None:
            return "UNKNOWN"
        classification = impacts.get(impact, "UNKNOWN")
        if order[classification] > order[top]:
            top = classification
    return top


def _extract_pins(checkout: Path, spec: dict) -> set[str]:
    found: set[str] = set()
    for rel in spec.get("files", []):
        path = checkout / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in spec.get("patterns", []):
            for match in re.finditer(pattern, text):
                found.add(match.group("sha"))
    return found


def cmd_coherence(args: argparse.Namespace) -> int:
    graph = _read(Path(args.graph), "graph")
    capability = _read(Path(args.capability), "capability")
    impacts = capability.get("impacts", {})
    checkouts: dict[str, Path] = {}
    for item in args.checkout:
        name, _, path = item.partition("=")
        if not name or not path:
            raise CoherenceError(f"--checkout must be name=path: {item}")
        checkouts[name] = Path(path)
    members = {m["name"]: m for m in graph["members"]}
    for name in members:
        if name not in checkouts:
            raise CoherenceError(f"no checkout for member {name}")
    repo_of = {m["repository"]: m["name"] for m in graph["members"]}

    edges: list[dict] = []
    movements: list[dict] = []
    blockers: list[str] = []

    pin_specs: dict[str, list[dict]] = capability.get("pins", {})
    for consumer_repo, specs in pin_specs.items():
        consumer = repo_of.get(consumer_repo)
        if consumer is None or consumer not in checkouts:
            continue
        for spec in specs:
            target_repo = spec["target"]
            target = repo_of.get(target_repo)
            if target is None:
                blockers.append(
                    f"{consumer}: pin target {target_repo} is not a graph member"
                )
                continue
            rows = list(capability.get("capabilities", {}).get(target_repo, []))
            target_checkout = checkouts.get(target)
            if target_checkout is not None:
                override_path = target_checkout / "promotion" / "capability-map.json"
                if override_path.is_file():
                    override = _read(override_path, "owner capability override")
                    rows = override.get("capabilities", {}).get(target_repo, rows)
            pins = _extract_pins(checkouts[consumer], spec)
            if not pins:
                blockers.append(
                    f"{consumer}: no {spec['edge']} pin found for {target_repo}"
                )
                continue
            if len(pins) > 1:
                blockers.append(
                    f"{consumer}: divergent {spec['edge']} pins for {target_repo}: "
                    + ", ".join(sorted(pins))
                )
                continue
            pinned = next(iter(pins))
            member_commit = members[target]["commit"]
            target_checkout = checkouts.get(target)
            if pinned == member_commit:
                classification, satisfied, paths = "CURRENT", True, []
            elif target_checkout is None:
                classification, satisfied, paths = "UNKNOWN", False, []
                blockers.append(
                    f"{consumer}: cannot classify {spec['edge']} delta "
                    f"{pinned[:8]}..{member_commit[:8]} without a {target} checkout"
                )
            else:
                paths = _diff_paths(target_checkout, target_repo, pinned, member_commit)
                if paths is None:
                    classification, satisfied = "UNKNOWN", False
                    paths = []
                    blockers.append(
                        f"{consumer}: unresolvable {spec['edge']} delta "
                        f"{pinned[:8]}..{member_commit[:8]}"
                    )
                else:
                    classification = _impact(paths, rows, impacts)
                    satisfied = classification in ("NOT_REQUIRED", "OPTIONAL")
                    if classification == "UNKNOWN":
                        blockers.append(
                            f"{consumer}: {spec['edge']} pin delta "
                            f"{pinned[:8]}..{member_commit[:8]} is unclassifiable"
                        )
                    # REQUIRED movements are ordered follow-up pressure (pin PRs),
                    # not refusals: the graph run consumes candidate revisions
                    # directly, never member stored pins.
            edge = {
                "from": consumer,
                "to": target,
                "edge": spec["edge"],
                "target": pinned,
            }
            edges.append(edge)
            movements.append(
                {
                    **edge,
                    "member_commit": member_commit,
                    "classification": classification,
                    "satisfied": satisfied,
                    "changed_paths": paths[:25],
                }
            )

    cycles = _audit_cycles(args, graph, members)
    floor_report = _check_floors(args, graph, members, checkouts, repo_of)

    report = {
        "schema_version": "mncs-actions.family-coherence/1",
        "graph_id": graph["graph_id"],
        "graph_digest": graph["digest"],
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"], e["edge"])),
        "movements": sorted(movements, key=lambda m: (m["from"], m["to"], m["edge"])),
        "cycles": cycles,
        "floors": floor_report["floors"],
        "blockers": sorted(
            set(blockers + cycles.get("unsafe_findings", []) + floor_report["blockers"])
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    required = sum(
        1 for m in movements if m["classification"] == "REQUIRED" and not m["satisfied"]
    )
    unknown = sum(1 for m in movements if m["classification"] == "UNKNOWN")
    print(
        f"coherence: {len(movements)} movements ({required} required, {unknown} unknown), "
        f"{len(report['blockers'])} blockers, cycles safe={not cycles.get('unsafe')}"
    )
    return 0


def _check_floors(
    args: argparse.Namespace,
    graph: dict,
    members: dict,
    checkouts: dict[str, Path],
    repo_of: dict[str, str],
) -> dict:
    """Verify candidate members satisfy capability floors.

    A floor names the minimum member revision carrying a capability the
    graph run needs (graph-mode evaluator, obligation CLI). The member
    revision must equal the floor or descend from it; anything else is a
    blocker. Floors never move automatically.
    """
    capability = _read(Path(args.capability), "capability")
    floors_out: list[dict] = []
    blockers: list[str] = []
    for floor in capability.get("floors", []):
        name = repo_of.get(floor["repo"])
        if name is None:
            blockers.append(f"floor {floor['id']} targets non-member {floor['repo']}")
            continue
        member_commit = members[name]["commit"]
        checkout = checkouts.get(name)
        status = "UNKNOWN"
        if checkout is not None:
            try:
                _ensure_objects(
                    checkout, floor["repo"], floor["min_revision"], member_commit
                )
                ancestor = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(checkout),
                        "merge-base",
                        "--is-ancestor",
                        floor["min_revision"],
                        member_commit,
                    ],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                status = "satisfied" if ancestor.returncode == 0 else "violated"
            except CoherenceError:
                status = "UNKNOWN"
        floors_out.append(
            {
                "id": floor["id"],
                "repo": floor["repo"],
                "status": status,
                "reason": floor.get("reason", ""),
            }
        )
        if status != "satisfied":
            blockers.append(
                f"member {name} does not satisfy floor {floor['id']}: {floor.get('reason', '')}"
            )
    return {"floors": floors_out, "blockers": blockers}


def _audit_cycles(args: argparse.Namespace, graph: dict, members: dict) -> dict:
    """Prove semantic authority edges do not collapse into self-approval.

    Transport execution is never authority. Unsafe patterns: the boundary
    requires its own output as the SOLE required evidence (no independent
    check decides); an authority binding names an unknown authority or a
    provider outside the producer descriptors.
    """
    findings: list[str] = []
    unsafe: list[str] = []
    if args.boundary:
        boundary = _read(Path(args.boundary), "boundary")
        required = [e["check_id"] for e in boundary.get("required_evidence", [])]
        independent = [c for c in required if c != args.check_id]
        if not independent:
            unsafe.append("boundary requires its own output as the sole evidence")
        else:
            findings.append(
                f"independent evidence present: {', '.join(sorted(independent))}"
            )
    if args.authority_map and args.descriptors:
        authority_map = _read(Path(args.authority_map), "authority map")
        descriptors = _read(Path(args.descriptors), "descriptors")
        providers = {
            o.get("provider")
            for d in descriptors.get("descriptors", [])
            for o in d.get("outputs", [])
        }
        for check_id, binding in authority_map.get("authorities", {}).items():
            if binding.get("authority") not in KNOWN_AUTHORITIES:
                unsafe.append(
                    f"{check_id} binds unknown authority {binding.get('authority')}"
                )
            if binding.get("provider") not in providers:
                unsafe.append(
                    f"{check_id} binds undescribed provider {binding.get('provider')}"
                )
        if not unsafe:
            findings.append("authority closure holds over described providers")
    findings.append(
        "transport executes evidence; no transport provider holds semantic authority"
    )
    return {"unsafe": bool(unsafe), "unsafe_findings": unsafe, "findings": findings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Family dependency coherence analysis."
    )
    parser.add_argument("--graph", required=True)
    parser.add_argument("--checkout", action="append", default=[])
    parser.add_argument("--capability", required=True)
    parser.add_argument("--descriptors", required=True)
    parser.add_argument("--authority-map", default="")
    parser.add_argument("--boundary", default="")
    parser.add_argument("--check-id", default="promotion-boundary")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        return cmd_coherence(args)
    except CoherenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
