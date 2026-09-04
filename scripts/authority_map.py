#!/usr/bin/env python3
"""Derive an MNCS authority map from pinned family producer descriptors.

Owns no authority semantics. The mapping is a mechanical projection of
``family-producer-descriptors.json``: each declared output check id maps
to its provider string and semantic authority, plus the producer
repository for attribution. The promotion evaluator consumes the derived
map; transport never invents bindings.

Duplicate ``check_id`` policy (mechanical, owns nothing):

- a repeated declaration that is field-identical (provider, authority,
  and repository attribution, including its absence) is deduplicated
  deterministically: first declaration wins, repeats are no-ops;
- any repeated declaration that differs in *any* field -- provider,
  semantic authority, or repository attribution (changed, added, or
  removed) -- is rejected with exit 2. Repository attribution can
  neither silently change nor silently disappear through deduplication.

Usage:
  authority_map.py --descriptors family-producer-descriptors.json
      --output authority-map.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = "mncs-authority-map/0.1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive an MNCS authority map.")
    parser.add_argument("--descriptors", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    try:
        document = json.loads(Path(args.descriptors).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read descriptors: {exc}", file=sys.stderr)
        return 2
    if not isinstance(document, dict) or not isinstance(
        document.get("descriptors"), list
    ):
        print("error: descriptors must hold a descriptor array", file=sys.stderr)
        return 2

    authorities: dict[str, dict[str, str]] = {}
    for descriptor in document["descriptors"]:
        if not isinstance(descriptor, dict):
            print("error: descriptor must be an object", file=sys.stderr)
            return 2
        repository = descriptor.get("repository", "")
        for output in descriptor.get("outputs", []) or []:
            if not isinstance(output, dict):
                print("error: descriptor output must be an object", file=sys.stderr)
                return 2
            check_id = output.get("check_id")
            provider = output.get("provider")
            roles = output.get("roles", {})
            authority = roles.get("semantic_authority") if isinstance(roles, dict) else None
            if (
                not isinstance(check_id, str)
                or not check_id
                or not isinstance(provider, str)
                or not provider
                or not isinstance(authority, str)
                or not authority
            ):
                print(
                    f"error: output needs check_id, provider, and roles.semantic_authority: {output!r}",
                    file=sys.stderr,
                )
                return 2
            entry: dict[str, str] = {"provider": provider, "authority": authority}
            if isinstance(repository, str) and repository:
                entry["repository"] = repository
            if check_id in authorities:
                if authorities[check_id] == entry:
                    continue
                existing = authorities[check_id]
                if existing.get("provider") != provider:
                    reason = "conflicting provider"
                elif existing.get("authority") != authority:
                    reason = "conflicting semantic authority"
                else:
                    reason = "conflicting repository attribution"
                print(
                    f"error: {reason} for duplicate check_id {check_id}: "
                    f"{existing!r} vs {entry!r}",
                    file=sys.stderr,
                )
                return 2
            authorities[check_id] = entry

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "authorities": authorities},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"authority map ({len(authorities)} checks) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
