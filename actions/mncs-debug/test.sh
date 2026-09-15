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
max_values="${MNCS_DEBUG_MAX_VALUES:-1024}"
max_value_bytes="${MNCS_DEBUG_MAX_VALUE_BYTES:-4096}"
selected_operations="${MNCS_DEBUG_SELECTED_OPERATIONS:-}"
timeout_seconds="${MNCS_DEBUG_TIMEOUT_SECONDS:-30}"
diagnostic_depth="${MNCS_DEBUG_DIAGNOSTIC_DEPTH:-minimal}"
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
  failure-only|selected|bounded|diagnostic|events) ;;
  *) write_unknown "invalid_invocation" "unsupported capture policy: $capture_policy"; exit 0 ;;
esac

case "$diagnostic_depth" in
  minimal|standard|deep) ;;
  *) write_unknown "invalid_invocation" "diagnostic-depth must be minimal, standard, or deep"; exit 0 ;;
esac

if ! [[ "$max_events" =~ ^[0-9]+$ ]] || (( max_events < 1 || max_events > 512 )); then
  write_unknown "invalid_invocation" "max-events must be an integer between 1 and 512"
  exit 0
fi
if ! [[ "$max_values" =~ ^[0-9]+$ ]] || (( max_values > 2048 )); then
  write_unknown "invalid_invocation" "max-values must be an integer between 0 and 2048"
  exit 0
fi
if ! [[ "$max_value_bytes" =~ ^[0-9]+$ ]] || (( max_value_bytes > 65536 )); then
  write_unknown "invalid_invocation" "max-value-bytes must be an integer between 0 and 65536"
  exit 0
fi
if [[ "$capture_policy" == "selected" && -z "$selected_operations" ]]; then
  write_unknown "invalid_invocation" "selected capture requires at least one operation identity"
  exit 0
fi

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
provider+=(--mncs "$mncs_bin" --cwd "$working_directory" --capture "$capture_policy" --max-events "$max_events" --max-values "$max_values" --max-value-bytes "$max_value_bytes" --timeout "$timeout_seconds" --output "$witness_file")
if [[ "$capture_policy" == "selected" ]]; then
  while IFS= read -r operation; do
    [[ -n "$operation" ]] && provider+=(--operation "$operation")
  done <<< "$selected_operations"
fi
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
sufficiency_file="$artifacts_dir/queries/sufficiency.json"
sufficiency_after_file="$artifacts_dir/queries/sufficiency-after.json"
diagnosis_file="$artifacts_dir/queries/diagnosis.json"
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
if [[ "$diagnostic_depth" == "standard" || "$diagnostic_depth" == "deep" ]]; then
  "$debug_bin" trace "$witness_file" --limit "$max_events" --output "$trace_file" >/dev/null 2>&1 || true
  "$debug_bin" why "$witness_file" --output "$provenance_file" >/dev/null 2>&1 || true
fi
if [[ "$diagnostic_depth" == "deep" ]]; then
  # replay is a non-executing projection of the captured witness; it never
  # reruns the originating test or debug provider.
  "$debug_bin" replay "$witness_file" --mode trace --output "$replay_file" >/dev/null 2>&1 || true
fi

# Debug owns the bounded evidence loop. Actions supplies any explicitly
# requested projections as reusable typed evidence and never infers a next
# operation from an operation name or maintains a second sufficiency policy.
diagnose_command=("$debug_bin" diagnose "$witness_file" --inspection "$inspection_file" --mncs "$mncs_bin" --max-steps 4)
for evidence in "$trace_file" "$provenance_file" "$replay_file"; do
  if [[ -f "$evidence" ]]; then
    diagnose_command+=(--evidence-artifact "$evidence")
  fi
done
"${diagnose_command[@]}" --output "$diagnosis_file" >/dev/null 2>&1 || true
python3 - "$diagnosis_file" "$sufficiency_file" <<'PY'
import json
import sys
try:
    diagnosis = json.loads(open(sys.argv[1], encoding="utf-8").read())
except (OSError, ValueError):
    diagnosis = {}
sufficiency = diagnosis.get("final_sufficiency")
if isinstance(sufficiency, dict):
    with open(sys.argv[2], "w", encoding="utf-8") as handle:
        json.dump(sufficiency, handle, indent=2, sort_keys=True)
PY
adaptive_operation="$(python3 - "$diagnosis_file" <<'PY'
import json
import sys
try:
    diagnosis = json.loads(open(sys.argv[1], encoding="utf-8").read())
except (OSError, ValueError):
    diagnosis = {}
projections = diagnosis.get("requested_projections") if isinstance(diagnosis.get("requested_projections"), list) else []
if projections and isinstance(projections[0], dict):
    print(projections[0].get("operation", ""))
PY
  )"

python3 - "$check_file" "$witness_file" "$validation_file" "$test_result_file" "$inspection_file" "$sufficiency_file" "$sufficiency_after_file" "$trace_file" "$provenance_file" "$replay_file" "$artifacts_dir/queries/trace-adaptive.json" "$artifacts_dir/queries/provenance-adaptive.json" "$artifacts_dir/queries/replay-adaptive.json" "$artifacts_dir/queries/minimization-adaptive.json" "$debug_status" "$diagnostic_depth" "$adaptive_operation" "$diagnosis_file" <<'PY'
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
sufficiency_path = Path(sys.argv[6])
sufficiency_after_path = Path(sys.argv[7])
trace_path = Path(sys.argv[8])
provenance_path = Path(sys.argv[9])
replay_path = Path(sys.argv[10])
trace_adaptive_path = Path(sys.argv[11])
provenance_adaptive_path = Path(sys.argv[12])
replay_adaptive_path = Path(sys.argv[13])
minimization_adaptive_path = Path(sys.argv[14])
debug_status = sys.argv[15]
diagnostic_depth = sys.argv[16]
adaptive_operation = sys.argv[17]
diagnosis_path = Path(sys.argv[18])
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
    ("mncs-debug-sufficiency", sufficiency_path, "mncs.debug-sufficiency/1"),
    ("mncs-debug-sufficiency-after", sufficiency_after_path, "mncs.debug-sufficiency/1"),
    ("mncs-debug-trace", trace_path, "mncs.debug-trace/1"),
    ("mncs-debug-provenance", provenance_path, "mncs.debug-provenance/1"),
    ("mncs-debug-replay", replay_path, "mncs.debug-replay/1"),
    ("mncs-debug-diagnosis", diagnosis_path, "mncs.debug-diagnosis/1"),
    ("mncs-debug-trace-adaptive", trace_adaptive_path, "mncs.debug-trace/1"),
    ("mncs-debug-provenance-adaptive", provenance_adaptive_path, "mncs.debug-provenance/1"),
    ("mncs-debug-replay-adaptive", replay_adaptive_path, "mncs.debug-replay/1"),
    ("mncs-debug-minimization-adaptive", minimization_adaptive_path, "mncs.debug-minimization/1"),
):
    path = Path(value)
    if path.is_file():
        references.append(ref(kind, path, schema))
if test_result_path:
    path = Path(test_result_path)
    if path.is_file():
        references.append(ref("mncs-test-result", path, "mncs.test-result/1"))
integration = witness.get("integration") if isinstance(witness.get("integration"), dict) else {}
runtime = witness.get("runtime") if isinstance(witness.get("runtime"), dict) else {}
observation = runtime.get("observation") if isinstance(runtime.get("observation"), dict) else {}
source = witness.get("static", {}).get("source", {}) if isinstance(witness.get("static"), dict) else {}
source_map = source.get("source_map") if isinstance(source, dict) and isinstance(source.get("source_map"), dict) else {}
observation_metadata = {
    "schema_revision": observation.get("schema_version"),
    "identity": runtime.get("observation_identity") or observation.get("identity"),
    "execution_identity": observation.get("execution_identity"),
    "completeness": observation.get("completeness", {}),
    "event_count": len(observation.get("events", [])) if isinstance(observation.get("events"), list) else 0,
    "value_count": len(observation.get("values", [])) if isinstance(observation.get("values"), list) else 0,
    "frame_count": len(observation.get("frames", [])) if isinstance(observation.get("frames"), list) else 0,
    "effect_count": len(observation.get("effects", [])) if isinstance(observation.get("effects"), list) else 0,
}
source_map_metadata = {
    "schema_revision": source.get("source_map_schema") if isinstance(source, dict) else None,
    "identity": source.get("source_map_identity") if isinstance(source, dict) else None,
    "source_identity": source_map.get("source_identity"),
}
diagnosis = (
    json.loads(diagnosis_path.read_text(encoding="utf-8"))
    if diagnosis_path.is_file()
    else {}
)
final_sufficiency = diagnosis.get("final_sufficiency") if isinstance(diagnosis, dict) else None
diagnosis_sufficient = (
    isinstance(diagnosis, dict)
    and diagnosis.get("status") == "sufficient"
    and diagnosis.get("sufficient") is True
    and isinstance(final_sufficiency, dict)
    and final_sufficiency.get("sufficient") is True
)
document = {
    "schema_version": "mncs.check-result/1",
    "id": "mncs-debug",
    "provider": "mncs-debug",
    "verdict": "PASS" if diagnosis_sufficient else "UNKNOWN",
    "scope": "mncs-debug",
    "claim": "bounded MNCS debug evidence was produced; native sufficiency remains " + ("established" if diagnosis_sufficient else "UNKNOWN"),
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
        "diagnostic": {
            "depth": diagnostic_depth,
            "escalation_reason": (
                "explicit_user_request" if diagnostic_depth != "minimal"
                else "insufficient_diagnostic_evidence" if adaptive_operation else None
            ),
            "operations": [
                name for name, path in (
                    ("inspect", inspection_path),
                    ("diagnose", diagnosis_path),
                    ("trace", trace_path),
                    ("why", provenance_path),
                    ("replay", replay_path),
                    ("trace-adaptive", trace_adaptive_path),
                    ("provenance-adaptive", provenance_adaptive_path),
                    ("replay-adaptive", replay_adaptive_path),
                    ("minimization-adaptive", minimization_adaptive_path),
                ) if path.is_file()
            ],
            "sufficiency": final_sufficiency,
            "evidence_requested": (
                {
                    "operation": adaptive_operation,
                    "evidence_gap": json.loads(sufficiency_path.read_text(encoding="utf-8")).get("evidence_gap"),
                    "reused": ["witness", "validation", "inspection"],
                    "sufficiency_after": str(sufficiency_after_path) if sufficiency_after_path.is_file() else None,
                }
                if adaptive_operation and sufficiency_path.is_file()
                else None
            ),
            "diagnosis": diagnosis if diagnosis else None,
        },
        "capture_policy": witness.get("trace", {}).get("capture_policy"),
        "observation": observation_metadata,
        "source_map": source_map_metadata,
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
