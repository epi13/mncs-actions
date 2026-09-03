#!/usr/bin/env bash
# MNCS aggregate: compose validated check-results into one aggregate verdict.
# Dominance: required FAIL -> FAIL; else required UNKNOWN/missing -> UNKNOWN;
# else PASS.  Optional UNKNOWN/FAIL stays visible in unresolved.
set -u -o pipefail

checks=""
required=""
optional=""
evidence_dir=""
working_dir="."
boundary=""
implementation_revision=""
carrier_revision=""
strict_membership="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --checks) checks="$2"; shift 2 ;;
    --required) required="$2"; shift 2 ;;
    --optional) optional="$2"; shift 2 ;;
    --evidence-dir) evidence_dir="$2"; shift 2 ;;
    --working-dir) working_dir="$2"; shift 2 ;;
    --boundary) boundary="$2"; shift 2 ;;
    --implementation-revision) implementation_revision="$2"; shift 2 ;;
    --carrier-revision) carrier_revision="$2"; shift 2 ;;
    --strict-membership) strict_membership="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 64 ;;
  esac
done

if [[ -z "$checks" || -z "$required" || -z "$evidence_dir" ]]; then
  echo "Usage: aggregate.sh --checks FILELIST --required CSV --evidence-dir DIR [--optional CSV] [--working-dir DIR] [--boundary NAME]" >&2
  exit 64
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../lib" && pwd)"

MNCS_CHECKS="$checks" \
MNCS_REQUIRED="$required" \
MNCS_OPTIONAL="$optional" \
MNCS_EVIDENCE_DIR="$evidence_dir" \
MNCS_WORKING_DIR="$working_dir" \
MNCS_BOUNDARY="$boundary" \
MNCS_IMPLEMENTATION_REVISION="$implementation_revision" \
MNCS_CARRIER_REVISION="$carrier_revision" \
MNCS_STRICT_MEMBERSHIP="$strict_membership" \
MNCS_LIB_DIR="$LIB_DIR" \
python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("MNCS_LIB_DIR", "lib"))
from mncs_actions import (  # noqa: E402
    AGGREGATE_RESULT_SCHEMA_VERSION,
    CARRIER_REVISION_KEY,
    CLAIM_ESTABLISHED,
    CLAIM_NOT_ESTABLISHED,
    IMPLEMENTATION_REVISION_KEY,
    aggregate_verdict,
    build_evidence_manifest,
    build_execution_receipt,
    canonical_bytes,
    check_revision_token,
    github_provenance,
    resolve_implementation_revision,
    sha256_hex,
    validate_aggregate_declarations,
    validate_aggregate_result,
    validate_check_result,
)


def parse_csv(value: str) -> list[str]:
    if not value.strip():
        return []
    return [item.strip() for item in value.split(",")]


def parse_file_list(value: str) -> list[str]:
    items: list[str] = []
    for token in value.replace("\n", " ").replace(",", " ").split():
        token = token.strip()
        if token:
            items.append(token)
    return items


checks_raw = os.environ.get("MNCS_CHECKS", "")
required = parse_csv(os.environ.get("MNCS_REQUIRED", ""))
optional = parse_csv(os.environ.get("MNCS_OPTIONAL", ""))
evidence_dir = Path(os.environ["MNCS_EVIDENCE_DIR"])
working_dir = Path(os.environ.get("MNCS_WORKING_DIR", "."))
boundary = os.environ.get("MNCS_BOUNDARY", "")

# Revision binding: the executing implementation reports its own revision
# from the checkout's revision-binding.json unless an explicit revision is
# asserted (explicit and file disagreeing is a hard error); the carrier
# revision is caller-asserted passthrough and only recorded when valid.
lib_dir = Path(os.environ.get("MNCS_LIB_DIR", "lib"))
checkout_root = lib_dir.parent
implementation_revision, revision_warnings, revision_error = (
    resolve_implementation_revision(
        os.environ.get("MNCS_IMPLEMENTATION_REVISION", ""),
        checkout_root,
    )
)
if revision_error is not None:
    print(f"::error::{revision_error}", file=sys.stderr)
    raise SystemExit(2)
for warning in revision_warnings:
    print(f"::warning::{warning}", file=sys.stderr)
carrier_revision = os.environ.get("MNCS_CARRIER_REVISION", "")
strict_membership = os.environ.get("MNCS_STRICT_MEMBERSHIP", "false") == "true"
if carrier_revision:
    token_error = check_revision_token("carrier_revision", carrier_revision)
    if token_error is not None:
        print(f"::error::{token_error}", file=sys.stderr)
        raise SystemExit(2)


def revision_inputs() -> dict:
    fields: dict = {}
    if implementation_revision:
        fields[IMPLEMENTATION_REVISION_KEY] = implementation_revision
    if carrier_revision:
        fields[CARRIER_REVISION_KEY] = carrier_revision
    return fields

evidence_dir.mkdir(parents=True, exist_ok=True)
aggregate_path = evidence_dir / "aggregate-result.json"
receipt_path = evidence_dir / "execution-receipt.json"
manifest_path = evidence_dir / "evidence-manifest.json"
provenance = github_provenance()


def write_json(path: Path, obj) -> None:
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_output(lines) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in lines:
            safe = str(value).replace("\n", " ").replace("\r", " ")
            handle.write(f"{key}={safe}\n")


def append_summary(lines) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("## MNCS aggregate\n\n")
        for line in lines:
            handle.write(f"- {line}\n")


check_files = parse_file_list(checks_raw)
errors: list[str] = validate_aggregate_declarations(required, optional)
loaded: dict[str, dict] = {}
loaded_meta: dict[str, dict] = {}
# Empty file list is NOT an error here: it means "no checks supplied".
# Missing required coverage then becomes UNKNOWN via aggregate_verdict.
# An explicitly listed but absent/unreadable file IS an error -> INVALID.
for rel in check_files:
    candidate = (working_dir / rel) if not Path(rel).is_absolute() else Path(rel)
    # Confinement: the composite action already entered working-directory,
    # so working_dir is "." (single resolution). Reject anything escaping
    # the confinement root, including absolute paths outside it.
    try:
        work_resolved = working_dir.resolve()
        # Resolve without requiring existence first for traversal check.
        candidate_resolved = (work_resolved / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
        if candidate_resolved != work_resolved and work_resolved not in candidate_resolved.parents:
            errors.append(f"check path escapes working directory: {rel}")
            continue
    except OSError as exc:
        errors.append(f"check path not resolvable {rel}: {exc}")
        continue
    if not candidate.is_file():
        errors.append(f"check file does not exist: {rel}")
        continue
    try:
        raw = candidate.read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"check file {rel} is not valid UTF-8 JSON: {exc}")
        continue
    struct_errors = validate_check_result(parsed)
    if struct_errors:
        for err in struct_errors:
            errors.append(f"{rel}: {err}")
        continue
    check_id = parsed["id"]
    if check_id in loaded:
        errors.append(f"duplicate check id: {check_id} ({rel})")
        continue
    loaded[check_id] = parsed
    loaded_meta[check_id] = {"rel": rel, "sha256": sha256_hex(raw)}

if strict_membership:
    declared = set(required).union(optional)
    for check_id in sorted(set(loaded) - declared):
        errors.append(
            f"check id is not declared required or optional: {check_id}"
        )

if errors:
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    receipt = build_execution_receipt(
        command=f"aggregate boundary={boundary or 'default'}",
        command_exit_code=0,
        claim_status=CLAIM_NOT_ESTABLISHED,
        result_path="",
        result_present=False,
        result_valid=False,
        result_errors=errors,
        produced_files=[],
        inputs={
            "required": ",".join(required),
            "optional": ",".join(optional),
            "boundary": boundary,
            **revision_inputs(),
        },
        error_code="AGGREGATE_NOT_ESTABLISHED",
        provenance=provenance,
    )
    write_json(receipt_path, receipt)
    append_output(
        [
            ("verdict", "INVALID"),
            ("claim-status", CLAIM_NOT_ESTABLISHED),
            ("execution-receipt-path", str(receipt_path.resolve())),
            ("command-exit-code", "0"),
        ]
    )
    append_summary(
        [
            "Claim: NOT ESTABLISHED (invalid check inputs)",
            f"Execution receipt: {receipt_path.resolve()}",
        ]
    )
    print("MNCS aggregate claim: NOT ESTABLISHED")
    raise SystemExit(2)

verdicts = {check_id: doc["verdict"] for check_id, doc in loaded.items()}
verdict, unresolved = aggregate_verdict(verdicts, required, optional)

# Component summary preserves provider identity and content digest so later
# provenance traversal can bind the aggregate to exact check bytes without
# interpreting domain semantics (mncs-actions carries, owners define).
checks_out: list[dict] = []
for check_id in sorted(loaded):
    doc = loaded[check_id]
    meta = loaded_meta.get(check_id, {})
    checks_out.append(
        {
            "id": check_id,
            "verdict": doc["verdict"],
            "required": check_id in required,
            "provider": doc.get("provider", ""),
            "scope": doc.get("scope", doc.get("claim", "")),
            "digest": meta.get("sha256", ""),
            "path": meta.get("rel", ""),
        }
    )
for missing in sorted(set(required) - set(loaded)):
    checks_out.append({"id": missing, "verdict": "UNKNOWN", "required": True, "provider": "", "scope": "missing"})

aggregate_doc = {
    "schema_version": AGGREGATE_RESULT_SCHEMA_VERSION,
    "verdict": verdict,
    "summary": f"Aggregate {verdict} over {len(required)} required check(s); boundary={boundary or 'default'}.",
    "required": required,
    "optional": optional,
    "checks": checks_out,
    "unresolved": unresolved,
    "boundary": {"name": boundary or "default"},
}
struct_errors = validate_aggregate_result(aggregate_doc)
if struct_errors:
    for error in struct_errors:
        print(f"::error::{error}", file=sys.stderr)
    raise SystemExit(2)
write_json(aggregate_path, aggregate_doc)
aggregate_sha = sha256_hex(aggregate_path.read_bytes())

receipt = build_execution_receipt(
    command=f"aggregate boundary={boundary or 'default'}",
    command_exit_code=0,
    claim_status=CLAIM_ESTABLISHED,
    result_path=str(aggregate_path),
    result_present=True,
    result_valid=True,
    claim_verdict=verdict,
    produced_files=[{"path": aggregate_path.name, "sha256": aggregate_sha}],
    inputs={
        "required": ",".join(required),
        "optional": ",".join(optional),
        "boundary": boundary,
        **revision_inputs(),
    },
    provenance=provenance,
)
write_json(receipt_path, receipt)
receipt_sha = sha256_hex(receipt_path.read_bytes())

manifest = build_evidence_manifest(
    verdict=verdict,
    result_sha256=aggregate_sha,
    result_filename=aggregate_path.name,
    command_exit_code=0,
    kind="aggregation",
    receipt_ref={"path": receipt_path.name, "sha256": receipt_sha},
    references=[
        {
            "kind": "check-result",
            "producer": loaded[check_id].get("provider", ""),
            "path": loaded_meta[check_id].get("rel", ""),
            "digest": loaded_meta[check_id].get("sha256", ""),
        }
        for check_id in sorted(loaded)
    ] or None,
    unresolved=unresolved or None,
    boundary={
        "name": boundary or "default",
        "required": required,
        "optional": optional,
        **revision_inputs(),
    },
    provenance=provenance,
)
write_json(manifest_path, manifest)
digest = sha256_hex(canonical_bytes(manifest))
append_output(
    [
        ("verdict", verdict),
        ("claim-status", CLAIM_ESTABLISHED),
        ("evidence-path", str(manifest_path.resolve())),
        ("aggregate-path", str(aggregate_path.resolve())),
        ("aggregate-digest", aggregate_sha),
        ("execution-receipt-path", str(receipt_path.resolve())),
        ("manifest-digest", digest),
        ("provenance-digest", digest),
        ("command-exit-code", "0"),
    ]
)
append_summary(
    [
        f"Verdict: {verdict}",
        f"Required: {', '.join(required) or '(none)'}",
        f"Optional: {', '.join(optional) or '(none)'}",
        f"Unresolved: {'; '.join(unresolved) or '(none)'}",
        f"Manifest digest: {digest}",
    ]
)
print(f"MNCS aggregate verdict: {verdict}")
if unresolved:
    print("Unresolved:")
    for item in unresolved:
        print(f"  - {item}")
PY
