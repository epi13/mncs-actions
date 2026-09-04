#!/usr/bin/env python3
"""Structural replay-evaluator stub. TEST PLUMBING ONLY.

Speaks the owner evaluator CLI surface (same flags) and re-derives a
claim from the *given* bytes: required checks present with PASS
verdicts, every check/obligation subject bound to a declared graph
member, provider wiring fixed to the family promotion provider. It
implements NO promotion semantics; semantic proof comes from the real
end-to-end run against the exact MNCS revision. Its only job is to let
replay tests exercise argument wiring and claim comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROVIDER = "mncs-promotion-boundary"
OWN_CHECK = "promotion-boundary"


def _load(path):
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--authority-map", required=True)
    parser.add_argument("--checks", nargs="*", default=[])
    parser.add_argument("--obligations", nargs="*", default=[])
    parser.add_argument("--subject-graph", required=True)
    parser.add_argument("--check-id", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--contract-revision", required=True)
    parser.add_argument("--producer-revision", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    boundary = _load(args.boundary)
    graph = _load(args.subject_graph)
    members = {
        (m["repository"], m["commit"]) for m in graph.get("members", [])
    }
    if boundary.get("graph", {}).get("digest") != graph.get("digest"):
        print("error: boundary declares a different graph", file=sys.stderr)
        return 2
    if args.provider != PROVIDER:
        print("error: wrong provider", file=sys.stderr)
        return 2
    checks = {}
    for path in args.checks:
        doc = _load(path)
        if doc.get("id") in checks:
            print(f"error: duplicate check id {doc.get('id')}", file=sys.stderr)
            return 2
        subject = doc.get("subject", {})
        if (subject.get("repository"), subject.get("commit")) not in members:
            print(
                f"error: check {doc.get('id')} subject is not a graph member",
                file=sys.stderr,
            )
            return 2
        checks[doc.get("id")] = doc
    for path in args.obligations:
        doc = _load(path)
        subject = doc.get("subject", {})
        if (subject.get("repository"), subject.get("commit")) not in members:
            print("error: obligation subject is not a graph member", file=sys.stderr)
            return 2
    required = [
        e["check_id"]
        for e in boundary.get("required_evidence", [])
        if e["check_id"] != OWN_CHECK
    ]
    blockers = [
        f"required check {check_id} is not PASS"
        for check_id in required
        if checks.get(check_id, {}).get("verdict") != "PASS"
    ]
    verdict = "PASS" if not blockers else "FAIL"
    boundary_raw = Path(args.boundary).read_bytes()
    map_raw = Path(args.authority_map).read_bytes()
    claim = {
        "schema_version": "mncs.check-result/1",
        "id": args.check_id,
        "provider": args.provider,
        "contract_revision": args.contract_revision,
        "producer_revision": args.producer_revision,
        "verdict": verdict,
        "subject": {
            "repository": "mncs-family/graph",
            "commit": f"graph:{graph['digest']}",
        },
        "summary": "replay stub claim",
        "references": [
            {
                "kind": "promotion-boundary",
                "boundary_id": boundary.get("boundary_id"),
                "digest": "sha256:" + hashlib.sha256(boundary_raw).hexdigest(),
            },
            {
                "kind": "authority-map",
                "contract_revision": "mncs-authority-map/0.1",
                "digest": "sha256:" + hashlib.sha256(map_raw).hexdigest(),
            },
        ],
        "promotion": {
            "subject": {
                "repository": "mncs-family/graph",
                "commit": f"graph:{graph['digest']}",
            },
            "boundary_id": boundary.get("boundary_id"),
            "boundary_revision": "mncs-promotion-boundary/0.1",
            "graph_digest": graph["digest"],
            "required_total": len(required),
            "required_passed": len(required) - len(blockers),
            "blockers": blockers,
        },
    }
    Path(args.output).write_text(
        json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"boundary {boundary.get('boundary_id')} -> {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
