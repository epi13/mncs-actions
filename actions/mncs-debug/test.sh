#!/usr/bin/env bash
# Transport-only mncs-debug provider. Debug semantics remain in mncs-debug;
# this script selects files, bounds execution, preserves artifacts, and emits
# a composable mncs.check-result/1 claim.
set -u -o pipefail

debug_bin="${MNCS_DEBUG_BIN:-mncs-debug}"
mncs_bin="${MNCS_BIN:-mncs}"
library_path="${MNCS_LIBRARY_PATH_INPUT:-}"
test_result_file="${MNCS_DEBUG_TEST_RESULT_FILE:-}"
test_id="${MNCS_DEBUG_TEST_ID:-}"
program_file="${MNCS_DEBUG_PROGRAM_FILE:-}"
request_file="${MNCS_DEBUG_REQUEST_FILE:-}"
working_directory="${MNCS_DEBUG_WORKING_DIRECTORY:-.}"
capture_policy="${MNCS_DEBUG_CAPTURE_POLICY:-failure-only}"
max_events="${MNCS_DEBUG_MAX_EVENTS:-256}"
timeout_seconds="${MNCS_DEBUG_TIMEOUT_SECONDS:-30}"
check_file="${MNCS_DEBUG_CHECK_FILE:-.mncs/mncs-debug-check.json}"
witness_file="${MNCS_DEBUG_WITNESS_FILE:-.mncs/mncs-debug-witness.json}"
artifacts_dir="${MNCS_DEBUG_ARTIFACTS_DIRECTORY:-.mncs/mncs-debug-artifacts}"

emit_output() {
  local key="$1"
  local value="$2"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    value="${value//$'\n'/ }"
    value="${value//$'\r'/ }"
    echo "$key=$value" >> "$GITHUB_OUTPUT"
  fi
}

mkdir -p "$(dirname "$check_file")" "$(dirname "$witness_file")" "$artifacts_dir"

write_unknown() {
  local classification="$1"
  local message="$2"
  python3 - "$check_file" "$classification" "$message" "$test_result_file" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

check_path = Path(sys.argv[1])
classification = sys.argv[2]
message = sys.argv[3]
test_result = Path(sys.argv[4])
root = Path.cwd().resolve()
references = []
if test_result.is_file():
    try:
        shown_path = test_result.resolve().relative_to(root).as_posix()
    except ValueError:
        shown_path = test_result.resolve().as_posix()
    references.append({
        "kind": "mncs-test-result",
        "path": shown_path,
        "digest": hashlib.sha256(test_result.read_bytes()).hexdigest(),
        "authority": "mncs-test",
    })
document = {
    "schema_version": "mncs.check-result/1",
    "id": "mncs-debug",
    "provider": "mncs-debug",
    "verdict": "UNKNOWN",
    "scope": "mncs-debug",
    "claim": "bounded debug evidence was not requested or was unavailable",
    "summary": message,
    "contract_revision": "mncs.debug-provider/1",
    "producer_revision": "mncs-debug-action-transport",
    "classification": classification,
    "failure_class": classification,
    "unresolved": [message],
    "references": references,
}
check_path.parent.mkdir(parents=True, exist_ok=True)
check_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  emit_output "failure-class" "$classification"
  emit_output "witness-path" ""
}

if [[ "$capture_policy" == "disabled" ]]; then
  write_unknown "capture_disabled" "debug capture policy disabled"
  exit 0
fi
case "$capture_policy" in
  failure-only|bounded|events) ;;
  *) write_unknown "invalid_invocation" "unsupported capture policy: $capture_policy"; exit 0 ;;
esac

if [[ -n "$test_result_file" && ! -f "$test_result_file" ]]; then
  write_unknown "infrastructure_failure" "mncs.test-result/1 file is unavailable"
  exit 0
fi
if [[ -z "$test_result_file" && ( -z "$program_file" || -z "$request_file" ) ]]; then
  write_unknown "invalid_invocation" "provide test-result-file or both program-file and request-file"
  exit 0
fi

if [[ -n "$test_result_file" && "$capture_policy" == "failure-only" ]]; then
  test_verdict="$(python3 - "$test_result_file" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle)
except (OSError, ValueError):
    value = {}
print(value.get("verdict", "UNKNOWN"))
PY
)"
  if [[ "$test_verdict" != "FAIL" ]]; then
    write_unknown "not_requested" "failure-only policy did not select a non-PASS test result"
    exit 0
  fi
fi

mkdir -p "$artifacts_dir/queries"
debug_status=0
provider=()
if [[ -n "$test_result_file" ]]; then
  provider=("$debug_bin" import-test "$test_result_file")
  [[ -n "$test_id" ]] && provider+=(--test-id "$test_id")
else
  provider=("$debug_bin" record "$program_file" "$request_file")
fi
provider+=(--mncs "$mncs_bin" --cwd "$working_directory" --capture "$capture_policy" --max-events "$max_events" --timeout "$timeout_seconds" --output "$witness_file")
if [[ -n "$library_path" ]]; then
  IFS=':' read -r -a libraries <<< "$library_path"
  for library in "${libraries[@]}"; do
    [[ -n "$library" ]] && provider+=(--library "$library")
  done
fi
"${provider[@]}"
debug_status=$?

if [[ ! -f "$witness_file" ]]; then
  write_unknown "infrastructure_failure" "mncs-debug did not produce a witness"
  exit 0
fi

validation_file="$artifacts_dir/queries/validation.json"
inspection_file="$artifacts_dir/queries/inspection.json"
trace_file="$artifacts_dir/queries/trace.json"
provenance_file="$artifacts_dir/queries/provenance.json"
replay_file="$artifacts_dir/queries/replay.json"

"$debug_bin" validate "$witness_file" --output "$validation_file" >/dev/null 2>&1
validation_status=$?
if [[ "$validation_status" != "0" ]]; then
  echo "::error::mncs-debug witness validation failed; no evidence claim will be established." >&2
  rm -f "$check_file"
  exit 0
fi
"$debug_bin" inspect "$witness_file" --output "$inspection_file" >/dev/null 2>&1 || true
"$debug_bin" trace "$witness_file" --limit "$max_events" --output "$trace_file" >/dev/null 2>&1 || true
"$debug_bin" why "$witness_file" --output "$provenance_file" >/dev/null 2>&1 || true
"$debug_bin" replay "$witness_file" --mode trace --output "$replay_file" >/dev/null 2>&1 || true

python3 - "$check_file" "$witness_file" "$validation_file" "$test_result_file" "$inspection_file" "$trace_file" "$provenance_file" "$replay_file" "$debug_status" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

check_path = Path(sys.argv[1])
witness_path = Path(sys.argv[2])
validation_path = Path(sys.argv[3])
test_result_path = sys.argv[4]
inspection_path = Path(sys.argv[5])
trace_path = Path(sys.argv[6])
provenance_path = Path(sys.argv[7])
replay_path = Path(sys.argv[8])
debug_status = sys.argv[9]
root = Path(os.environ.get("MNCS_DEBUG_WORKING_DIRECTORY", ".")).resolve()

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()

def ref(kind: str, path: Path, schema: str | None = None) -> dict:
    value = {"kind": kind, "path": relative(path), "digest": digest(path)}
    if schema:
        value["schema_revision"] = schema
    return value

validation = json.loads(Path(validation_path).read_text(encoding="utf-8"))
witness = json.loads(witness_path.read_text(encoding="utf-8"))
if validation.get("valid") is not True or witness.get("schema_version") != "mncs.debug-witness/1":
    raise SystemExit("validated witness does not establish mncs.debug-witness/1")
references = [ref("mncs-debug-witness", witness_path, "mncs.debug-witness/1")]
for kind, value, schema in (
    ("mncs-debug-validation", validation_path, "mncs.debug-validation/1"),
    ("mncs-debug-inspection", inspection_path, "mncs.debug-inspection/1"),
    ("mncs-debug-trace", trace_path, "mncs.debug-trace/1"),
    ("mncs-debug-provenance", provenance_path, "mncs.debug-provenance/1"),
    ("mncs-debug-replay", replay_path, "mncs.debug-replay/1"),
):
    path = Path(value)
    if path.is_file():
        references.append(ref(kind, path, schema))
if test_result_path:
    path = Path(test_result_path)
    if path.is_file():
        references.append(ref("mncs-test-result", path, "mncs.test-result/1"))
integration = witness.get("integration") if isinstance(witness.get("integration"), dict) else {}
document = {
    "schema_version": "mncs.check-result/1",
    "id": "mncs-debug",
    "provider": "mncs-debug",
    "verdict": "PASS",
    "scope": "mncs-debug",
    "claim": "bounded MNCS debug evidence was produced and witness validation succeeded",
    "summary": f"debug witness {witness.get('witness_id')} ({len(references)} retained references)",
    "contract_revision": "mncs.debug-provider/1",
    "producer_revision": "mncs-debug-action-transport",
    "digest": digest(witness_path),
    "classification": "debug_evidence",
    "failure_class": "none",
    "references": references,
    "debug": {
        "witness_id": witness.get("witness_id"),
        "execution_identity": witness.get("execution_identity"),
        "outcome": witness.get("outcome"),
        "integration": integration,
        "provider_exit_code": int(debug_status),
        "capture_policy": witness.get("trace", {}).get("capture_policy"),
    },
}
check_path.parent.mkdir(parents=True, exist_ok=True)
check_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
status=$?
if [[ "$status" != "0" ]]; then
  rm -f "$check_file"
  exit 0
fi
emit_output "witness-path" "$(cd "$(dirname "$witness_file")" && pwd)/$(basename "$witness_file")"
emit_output "failure-class" "none"
exit 0
