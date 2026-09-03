#!/usr/bin/env python3
"""Adapt the mncs-language ``source-study`` record to check-result/1.

The compiler owns language and compiler semantics.  This adapter only checks
that the published study envelope is machine-readable, preserves its stage
observations, and projects an unresolved compiler study to UNKNOWN.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mncs_actions import (  # noqa: E402
    CHECK_RESULT_SCHEMA_VERSION,
    sha256_hex,
    validate_check_result,
)

CONTRACT_ID = "mncs:language:compilation-study-result:0.1"
SCHEMA_VERSION = "0.1"
REQUIRED_STAGES = (
    "source",
    "lexical_tokens",
    "concrete_syntax_tree",
    "abstract_syntax_tree",
    "semantic",
    "semantic_graph",
    "identity_map",
    "validation",
)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read compiler study: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("compiler study must be a JSON object")
    return value


def build_check(
    study: dict[str, Any],
    *,
    source_path: str,
    check_id: str = "mncs-language-study",
    producer_revision: str = "",
    required_module: str = "",
) -> dict[str, Any]:
    if study.get("contract_id") != CONTRACT_ID:
        raise ValueError(f"compiler study contract_id must be {CONTRACT_ID}")
    if study.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"compiler study schema_version must be {SCHEMA_VERSION}")
    for field in ("identity", "compilation_status", "interpretation"):
        if not isinstance(study.get(field), str) or not study[field]:
            raise ValueError(f"compiler study {field} must be non-empty text")
    stages = study.get("stage_fingerprints")
    if not isinstance(stages, dict):
        raise ValueError("compiler study stage_fingerprints must be an object")
    missing_stages = [stage for stage in REQUIRED_STAGES if not isinstance(stages.get(stage), str)]
    if missing_stages:
        raise ValueError(f"compiler study is missing stage fingerprints: {', '.join(missing_stages)}")
    diagnostics = study.get("diagnostics", [])
    if not isinstance(diagnostics, list) or not all(isinstance(item, dict) for item in diagnostics):
        raise ValueError("compiler study diagnostics must be an array of objects")
    unresolved_obligations = study.get("unresolved_obligations", [])
    if not isinstance(unresolved_obligations, list) or not all(
        isinstance(item, str) and item for item in unresolved_obligations
    ):
        raise ValueError("compiler study unresolved_obligations must be a string array")
    modules = study.get("module_resolutions", [])
    if not isinstance(modules, list) or not all(isinstance(item, dict) for item in modules):
        raise ValueError("compiler study module_resolutions must be an array of objects")
    if required_module and not any(
        item.get("declared_module") == required_module or item.get("requested_module") == required_module
        for item in modules
    ):
        raise ValueError(f"compiler study did not resolve required module: {required_module}")

    semantic_errors = [
        item
        for item in diagnostics
        if str(item.get("kind", "")).lower() in {"error", "semantic_invalidity", "parse_error"}
    ]
    status = "FAIL" if semantic_errors else "UNKNOWN" if unresolved_obligations else "PASS"
    unresolved: list[str] = []
    if unresolved_obligations:
        unresolved.extend(f"language unresolved obligation: {item}" for item in unresolved_obligations)
    for diagnostic in semantic_errors:
        unresolved.append(f"language diagnostic: {diagnostic.get('message', diagnostic)}")
    if not unresolved and status == "UNKNOWN":
        unresolved.append(f"language compilation status remains {study['compilation_status']}")
    raw_digest = sha256_hex(json.dumps(study, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    reference: dict[str, Any] = {
        "kind": "language-compilation-study",
        "producer": "mncs-language",
        "contract_revision": CONTRACT_ID,
        "path": source_path,
        "digest": raw_digest,
    }
    check: dict[str, Any] = {
        "schema_version": CHECK_RESULT_SCHEMA_VERSION,
        "id": check_id,
        "provider": "mncs-language",
        "verdict": status,
        "scope": "source-study/compiler-pipeline",
        "claim": "language study stages were observed; no conformance or assurance claim",
        "summary": (
            f"mncs-language source study {study['compilation_status']} with "
            f"{len(stages)} stage fingerprint(s); interpretation={study['interpretation']}"
        ),
        "references": [reference],
        "unresolved": unresolved,
        "digest": raw_digest,
    }
    if producer_revision:
        check["producer_revision"] = producer_revision
        reference["producer_revision"] = producer_revision
    errors = validate_check_result(check)
    if errors:
        raise ValueError("invalid adapted compiler check: " + "; ".join(errors))
    return check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-path", default="pressure/source.mncs")
    parser.add_argument("--check-id", default="mncs-language-study")
    parser.add_argument("--producer-revision", default="")
    parser.add_argument("--required-module", default="mncs.core.status.v1")
    args = parser.parse_args()
    try:
        check = build_check(
            _load(args.input),
            source_path=args.source_path,
            check_id=args.check_id,
            producer_revision=args.producer_revision,
            required_module=args.required_module,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"mncs-language study -> {check['verdict']}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
