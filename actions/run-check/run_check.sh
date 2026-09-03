#!/usr/bin/env bash
# MNCS run-check packaging: validate one provider check-result, always emit
# an execution receipt, and emit a check manifest only when the claim is
# ESTABLISHED.  Unlike verify, a FAIL/UNKNOWN check claim still exits 0 so
# aggregation can decide the boundary; only NOT_ESTABLISHED exits 2.
set -u -o pipefail

result_file=""
evidence_dir=""
command_exit_code="0"
command_label=""
expected_id=""
expected_provider=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --result-file) result_file="$2"; shift 2 ;;
    --evidence-dir) evidence_dir="$2"; shift 2 ;;
    --command-exit-code) command_exit_code="$2"; shift 2 ;;
    --command) command_label="$2"; shift 2 ;;
    --expected-id) expected_id="$2"; shift 2 ;;
    --expected-provider) expected_provider="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 64 ;;
  esac
done

if [[ -z "$result_file" || -z "$evidence_dir" ]]; then
  echo "Usage: run_check.sh --result-file PATH --evidence-dir PATH --command-exit-code CODE [--command LABEL] [--expected-id ID] [--expected-provider P]" >&2
  exit 64
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../lib" && pwd)"

MNCS_RESULT_FILE="$result_file" \
MNCS_EVIDENCE_DIR="$evidence_dir" \
MNCS_COMMAND_EXIT_CODE="$command_exit_code" \
MNCS_COMMAND_LABEL="$command_label" \
MNCS_EXPECTED_ID="$expected_id" \
MNCS_EXPECTED_PROVIDER="$expected_provider" \
MNCS_LIB_DIR="$LIB_DIR" \
python3 - <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("MNCS_LIB_DIR", "lib"))
from mncs_actions import (  # noqa: E402
    CLAIM_ESTABLISHED,
    CLAIM_NOT_ESTABLISHED,
    build_evidence_manifest,
    build_execution_receipt,
    canonical_bytes,
    github_provenance,
    load_result_file,
    sha256_hex,
    validate_check_result,
)

result_path = Path(os.environ["MNCS_RESULT_FILE"])
evidence_dir = Path(os.environ["MNCS_EVIDENCE_DIR"])
command_label = os.environ.get("MNCS_COMMAND_LABEL", "")
expected_id = os.environ.get("MNCS_EXPECTED_ID", "")
expected_provider = os.environ.get("MNCS_EXPECTED_PROVIDER", "")
try:
    command_exit_code = int(os.environ.get("MNCS_COMMAND_EXIT_CODE", "0"))
except ValueError:
    print("::error::The provider exit code was not an integer.", file=sys.stderr)
    raise SystemExit(2)

evidence_dir.mkdir(parents=True, exist_ok=True)
receipt_path = evidence_dir / "execution-receipt.json"
manifest_path = evidence_dir / "evidence-manifest.json"
copied_check = evidence_dir / "check-result.json"
observed_path = evidence_dir / "observed-check.json"
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
        handle.write("## MNCS check\n\n")
        for line in lines:
            handle.write(f"- {line}\n")


parsed, _raw_digest, load_errors = load_result_file(result_path)
result_present = result_path.is_file()
errors: list[str] = list(load_errors)
verdict = ""
if not errors:
    struct_errors = validate_check_result(parsed)
    if struct_errors:
        errors.extend(struct_errors)
    else:
        if expected_id and parsed.get("id") != expected_id:
            errors.append(
                f'check id mismatch: expected {expected_id!r} got {parsed.get("id")!r}'
            )
        if expected_provider and parsed.get("provider") != expected_provider:
            errors.append(
                f'check provider mismatch: expected {expected_provider!r} got {parsed.get("provider")!r}'
            )
        if not errors:
            verdict = parsed["verdict"]

if errors:
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    produced = []
    try:
        if result_present and result_path.is_file():
            shutil.copyfile(result_path, observed_path)
            produced.append(
                {"path": observed_path.name, "sha256": sha256_hex(observed_path.read_bytes())}
            )
    except OSError as exc:
        print(f"::warning::Could not preserve observed check: {exc}", file=sys.stderr)
    receipt = build_execution_receipt(
        command=command_label,
        command_exit_code=command_exit_code,
        claim_status=CLAIM_NOT_ESTABLISHED,
        result_path=str(result_path),
        result_present=result_present,
        result_valid=False,
        result_errors=errors,
        produced_files=produced,
        inputs={"result_file": str(result_path), "evidence_dir": str(evidence_dir)},
        error_code="CHECK_NOT_ESTABLISHED",
        provenance=provenance,
    )
    write_json(receipt_path, receipt)
    append_output(
        [
            ("verdict", "INVALID"),
            ("claim-status", CLAIM_NOT_ESTABLISHED),
            ("execution-receipt-path", str(receipt_path.resolve())),
            ("command-exit-code", str(command_exit_code)),
        ]
    )
    append_summary(
        [
            "Claim: NOT ESTABLISHED (missing/malformed/invalid check)",
            f"Command exit code: {command_exit_code}",
            f"Execution receipt: {receipt_path.resolve()}",
        ]
    )
    print("MNCS check claim: NOT ESTABLISHED")
    raise SystemExit(2)

shutil.copyfile(result_path, copied_check)
check_sha = sha256_hex(copied_check.read_bytes())
receipt = build_execution_receipt(
    command=command_label,
    command_exit_code=command_exit_code,
    claim_status=CLAIM_ESTABLISHED,
    result_path=str(result_path),
    result_present=True,
    result_valid=True,
    claim_verdict=verdict,
    produced_files=[{"path": copied_check.name, "sha256": check_sha}],
    inputs={"result_file": str(result_path), "evidence_dir": str(evidence_dir)},
    provenance=provenance,
)
write_json(receipt_path, receipt)
receipt_sha = sha256_hex(receipt_path.read_bytes())
manifest = build_evidence_manifest(
    verdict=verdict,
    result_sha256=check_sha,
    result_filename=copied_check.name,
    command_exit_code=command_exit_code,
    kind="check",
    receipt_ref={"path": receipt_path.name, "sha256": receipt_sha},
    references=parsed.get("references"),
    unresolved=parsed.get("unresolved"),
    boundary={"check_id": parsed.get("id"), "provider": parsed.get("provider")},
    provenance=provenance,
)
write_json(manifest_path, manifest)
digest = sha256_hex(canonical_bytes(manifest))
append_output(
    [
        ("verdict", verdict),
        ("claim-status", CLAIM_ESTABLISHED),
        ("evidence-path", str(manifest_path.resolve())),
        ("check-path", str(copied_check.resolve())),
        ("execution-receipt-path", str(receipt_path.resolve())),
        ("manifest-digest", digest),
        ("provenance-digest", digest),
        ("command-exit-code", str(command_exit_code)),
    ]
)
append_summary(
    [
        f"Check {parsed.get('id')} ({parsed.get('provider')}): {verdict}",
        f"Manifest digest: {digest}",
    ]
)
print(f"MNCS check {parsed.get('id')}: {verdict}")
PY
