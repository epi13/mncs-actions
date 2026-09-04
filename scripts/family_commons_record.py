#!/usr/bin/env python3
"""Relate a candidate family graph through a Commons ChangeSet record.

Orchestration glue (no semantics): reads the graph document, the produced
member evidence, member obligations, and the graph promotion claim, then
builds a ChangeSet with base revisions covering every graph member,
supports edges for member evidence and obligations (correlation-scoped),
and exactly one promotes edge for the graph promotion result. The record
chains the accepted predecessor via details.predecessorGraph and is
validated with the owner-native Commons validator from the candidate
Commons checkout.

Usage:
  family_commons_record.py --graph GRAPH.json --checks-dir DIR
      --promotion-result PROM.json [--obligations-dir DIR]
      --commons-root DIR --output RECORD.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _read(path: Path, label: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: {label} {path}: cannot read: {exc}")
    if not isinstance(doc, dict):
        raise SystemExit(f"error: {label} {path}: must be a JSON object")
    return doc


def _digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relate a family graph in Commons.")
    parser.add_argument("--graph", required=True)
    parser.add_argument("--checks-dir", required=True)
    parser.add_argument("--promotion-result", required=True)
    parser.add_argument("--obligations-dir", default="")
    parser.add_argument("--commons-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    graph = _read(Path(args.graph), "graph")
    sys.path.insert(0, str(Path(args.commons_root) / "src"))
    from mncs_commons.family import make_changeset_record
    from mncs_commons.validation import validate_record

    checks_dir = Path(args.checks_dir)
    supports = []
    for entry in graph.get("evidence", []):
        path = checks_dir / entry["path"]
        if not path.is_file():
            print(f"error: evidence file missing: {entry['path']}", file=sys.stderr)
            return 2
        raw = path.read_bytes()
        check = json.loads(raw.decode("utf-8"))
        subject = check.get("subject") or {}
        reference: dict = {
            "producer": check.get("provider", ""),
            "recordKind": "check-result",
            "recordVersion": check.get("contract_revision", "0.1"),
            "stableId": f"mncs://check-result/{entry['check_id']}",
            "contentDigest": _digest_bytes(raw),
        }
        if subject.get("repository") and subject.get("commit"):
            reference["scope"] = {
                "repository": subject["repository"],
                "commit": subject["commit"],
            }
        supports.append(reference)

    if args.obligations_dir:
        for path in sorted(Path(args.obligations_dir).glob("*.json")):
            raw = path.read_bytes()
            record = json.loads(raw.decode("utf-8"))
            subject = record.get("subject") or {}
            supports.append(
                {
                    "producer": "mncds",
                    "recordKind": "obligation-record",
                    "recordVersion": "0.2",
                    "stableId": f"mncds://obligation/{record.get('obligation_key', path.stem)}",
                    "contentDigest": _digest_bytes(raw),
                    "scope": {
                        "repository": subject.get("repository", ""),
                        "commit": subject.get("commit", ""),
                    },
                }
            )

    claim_raw = Path(args.promotion_result).read_bytes()
    claim = json.loads(claim_raw.decode("utf-8"))
    promotes = [
        {
            "producer": "mncs-promotion-boundary",
            "recordKind": "check-result",
            "recordVersion": "0.1",
            "stableId": "mncs://check-result/family-advancement",
            "contentDigest": _digest_bytes(claim_raw),
        }
    ]

    record = make_changeset_record(
        changeset_id=f"changeset.{graph['graph_id']}",
        created_at=graph["provenance"]["generated_at"],
        base_revisions=[
            {"repository": m["repository"], "commit": m["commit"]}
            for m in graph["members"]
        ],
        supports=supports,
        promotes=promotes,
        summary=(
            f"Family graph {graph['graph_id']} ({graph['digest'][:12]}): "
            f"promotion {claim.get('verdict', 'UNKNOWN')} over "
            f"{len(graph['members'])} exact member revisions; related, not decided, by Commons"
        ),
    )
    record["details"]["predecessorGraph"] = graph["base"]["digest"]

    report = validate_record(record)
    if not report.valid:
        print(
            f"error: commons record invalid: {report.diagnostics[:2]}", file=sys.stderr
        )
        return 2
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"commons {record['kind']} {record['details']['changesetId']} relates graph {graph['digest'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
