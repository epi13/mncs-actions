#!/usr/bin/env bash
# Invoke the independent mncs-test provider with explicit paths. This script
# owns only automation plumbing: optional cargo build, argument construction,
# and preserving a valid check-result when the provider itself cannot start.
set -u -o pipefail

mncs_test_bin="${MNCS_TEST_BIN:-mncs-test}"
mncs_bin="${MNCS_BIN:-mncs}"
mncs_source="${MNCS_SOURCE:-}"
manifest="${MNCS_MANIFEST:-}"
library_path="${MNCS_LIBRARY_PATH_INPUT:-}"
embed_library="${MNCS_EMBED_LIBRARY_INPUT:-}"
test_filter="${MNCS_TEST_FILTER:-}"
verification_plan="${MNCS_VERIFICATION_PLAN:-}"
result_file="${MNCS_RESULT_FILE:-.mncs/mncs-test-check.json}"
test_result_file="${MNCS_TEST_RESULT_FILE:-.mncs/mncs-test-result.json}"
artifacts_dir="${MNCS_ARTIFACTS_DIRECTORY:-.mncs/mncs-test-artifacts}"
timeout_seconds="${MNCS_TIMEOUT_SECONDS:-}"
step_budget="${MNCS_STEP_BUDGET:-}"
allow_unsupported="${MNCS_ALLOW_UNSUPPORTED:-false}"

emit_output() {
  local key="$1"
  local value="$2"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    value="${value//$'\n'/ }"
    value="${value//$'\r'/ }"
    echo "$key=$value" >> "$GITHUB_OUTPUT"
  fi
}

write_start_failure() {
  local message="$1"
  mkdir -p "$(dirname "$result_file")" "$(dirname "$test_result_file")" "$artifacts_dir"
  python3 - "$result_file" "$test_result_file" "$message" <<'PY'
import json
import sys
from pathlib import Path

check_path, result_path, message = map(Path, sys.argv[1:])
result = {
    "schema_version": "mncs.test-result/1",
    "protocol_version": 1,
    "id": "mncs-test",
    "provider": "mncs-test",
    "verdict": "UNKNOWN",
    "classification": "infrastructure_failure",
    "failure_class": "infrastructure_failure",
    "exit_code": 3,
    "run_id": "0" * 64,
    "scope": {"manifest": None},
    "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "unsupported": 0, "authority": "adapter"},
    "tests": [],
    "suite": None,
    "native_suite_summary": None,
    "provenance": {"runner": {"name": "mncs-test", "version": "unknown"}},
    "artifacts": [],
    "reproduction": {"command": "mncs-test run --manifest <manifest>", "run_id": "0" * 64},
    "failure": {"class": "infrastructure_failure", "message": message},
}
result_path.parent.mkdir(parents=True, exist_ok=True)
result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
check = {
    "schema_version": "mncs.check-result/1",
    "id": "mncs-test",
    "provider": "mncs-test",
    "verdict": "UNKNOWN",
    "scope": "mncs-test",
    "claim": "mncs-test could not be started",
    "summary": message,
    "contract_revision": "mncs.test-result/1",
    "classification": "infrastructure_failure",
    "failure_class": "infrastructure_failure",
    "unresolved": [message],
}
check_path.parent.mkdir(parents=True, exist_ok=True)
check_path.write_text(json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  emit_output "failure-class" "infrastructure_failure"
  emit_output "test-result-path" "$(cd "$(dirname "$test_result_file")" && pwd)/$(basename "$test_result_file")"
}

if [[ -z "$manifest" ]]; then
  write_start_failure "manifest input is required"
  exit 2
fi

if [[ -n "$verification_plan" && ! -f "$verification_plan" ]]; then
  write_start_failure "verification plan is unavailable: $verification_plan"
  exit 2
fi

if [[ -n "$verification_plan" && -n "$test_filter" ]]; then
  write_start_failure "test-filter cannot be combined with verification-plan; the plan owns exact selection"
  exit 2
fi

if [[ "${MNCS_BUILD:-false}" == "true" ]]; then
  if [[ -z "$mncs_source" ]]; then
    write_start_failure "build-mncs is enabled but mncs-source is empty"
    exit 3
  fi
  build_log="$artifacts_dir/toolchain-build"
  mkdir -p "$build_log"
  cargo build --locked --manifest-path "$mncs_source/Cargo.toml" --bin mncs >"$build_log/stdout.log" 2>"$build_log/stderr.log"
  build_status=$?
  if [[ "$build_status" != "0" ]]; then
    write_start_failure "mncs-language toolchain build failed with exit $build_status"
    exit 3
  fi
  cargo build --locked --manifest-path "$mncs_source/Cargo.toml" -p mncs-embed >>"$build_log/stdout.log" 2>>"$build_log/stderr.log"
  build_status=$?
  if [[ "$build_status" != "0" ]]; then
    write_start_failure "mncs-embed build failed with exit $build_status"
    exit 3
  fi
  mncs_bin="$mncs_source/target/debug/mncs"
fi

provider=("$mncs_test_bin" run --manifest "$manifest" --mncs "$mncs_bin" --result "$test_result_file" --check-result "$result_file" --artifacts "$artifacts_dir")
if [[ -n "$library_path" ]]; then
  IFS=':' read -r -a libraries <<< "$library_path"
  for library in "${libraries[@]}"; do
    [[ -n "$library" ]] && provider+=(--library "$library")
  done
fi
if [[ -n "$embed_library" ]]; then
  provider+=(--embed-library "$embed_library")
fi
if [[ -n "$test_filter" ]]; then
  normalized_filters="${test_filter//,/ }"
  read -r -a filters <<< "$normalized_filters"
  for filter in "${filters[@]}"; do
    [[ -n "$filter" ]] && provider+=(--filter "$filter")
  done
fi
if [[ -n "$verification_plan" ]]; then
  provider+=(--verification-plan "$verification_plan")
fi
[[ -n "$timeout_seconds" ]] && provider+=(--timeout-seconds "$timeout_seconds")
[[ -n "$step_budget" ]] && provider+=(--step-budget "$step_budget")
[[ "$allow_unsupported" == "true" ]] && provider+=(--allow-unsupported)

"${provider[@]}"
status=$?

if [[ -f "$test_result_file" ]]; then
  failure_class="$(python3 - "$test_result_file" <<'PY'
import json
import sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    value = {}
print(value.get("failure_class", "unknown"))
PY
)"
  emit_output "failure-class" "$failure_class"
  emit_output "test-result-path" "$(cd "$(dirname "$test_result_file")" && pwd)/$(basename "$test_result_file")"
else
  write_start_failure "mncs-test did not produce a detailed result"
fi
exit "$status"
