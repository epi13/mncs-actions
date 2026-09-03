#!/usr/bin/env bash
set -u -o pipefail

result_file=""
evidence_dir=""
command_exit_code="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --result-file)
      result_file="$2"
      shift 2
      ;;
    --evidence-dir)
      evidence_dir="$2"
      shift 2
      ;;
    --command-exit-code)
      command_exit_code="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

if [[ -z "$result_file" || -z "$evidence_dir" ]]; then
  echo "Usage: verify.sh --result-file PATH --evidence-dir PATH --command-exit-code CODE" >&2
  exit 64
fi

python3 - "$result_file" "$evidence_dir" "$command_exit_code" <<'PY'
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

result_path = Path(sys.argv[1])
evidence_dir = Path(sys.argv[2])
try:
    command_exit_code = int(sys.argv[3])
except ValueError:
    print("::error::The verifier exit code was not an integer.", file=sys.stderr)
    raise SystemExit(2)

errors = []
result = None

if not result_path.is_file():
    errors.append(f"verification result does not exist: {result_path}")
else:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"verification result is not valid UTF-8 JSON: {exc}")

if not isinstance(result, dict):
    errors.append("verification result must be a JSON object")
else:
    if result.get("schema_version") != "mncs.verification-result/1":
        errors.append("schema_version must be mncs.verification-result/1")

    verdict = result.get("verdict")
    if verdict not in {"PASS", "FAIL", "UNKNOWN"}:
        errors.append("verdict must be PASS, FAIL, or UNKNOWN")

    checks = result.get("checks")
    if not isinstance(checks, list):
        errors.append("checks must be an array")
    else:
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                errors.append(f"checks[{index}] must be an object")
                continue
            if not isinstance(check.get("id"), str) or not check["id"]:
                errors.append(f"checks[{index}].id must be a non-empty string")
            if check.get("verdict") not in {"PASS", "FAIL", "UNKNOWN"}:
                errors.append(
                    f"checks[{index}].verdict must be PASS, FAIL, or UNKNOWN"
                )

        if verdict == "PASS" and any(
            isinstance(check, dict) and check.get("verdict") == "FAIL"
            for check in checks
        ):
            errors.append("top-level PASS cannot contain a failing check")

if errors:
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        line_sep = chr(10)
        with open(output_path, "a", encoding="utf-8") as output:
            output.write("verdict=UNKNOWN" + line_sep)
            output.write("command-exit-code=" + str(command_exit_code) + line_sep)
    raise SystemExit(2)

verdict = result["verdict"]
evidence_dir.mkdir(parents=True, exist_ok=True)
copied_result = evidence_dir / "verification-result.json"
manifest_path = evidence_dir / "evidence-manifest.json"
shutil.copyfile(result_path, copied_result)

def env(name):
    return os.environ.get(name, "")

provenance = {
    "repository": env("GITHUB_REPOSITORY"),
    "ref": env("GITHUB_REF"),
    "commit": env("GITHUB_SHA"),
    "workflow": env("GITHUB_WORKFLOW"),
    "run_id": env("GITHUB_RUN_ID"),
    "actor": env("GITHUB_ACTOR"),
    "event": env("GITHUB_EVENT_NAME"),
    "runner": env("RUNNER_NAME"),
}

manifest = {
    "schema_version": "mncs.evidence-manifest/1",
    "kind": "verification",
    "verdict": verdict,
    "result": {
        "path": "verification-result.json",
        "sha256": hashlib.sha256(copied_result.read_bytes()).hexdigest(),
    },
    "provenance": provenance,
    "command_exit_code": command_exit_code,
    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}

canonical_manifest = json.dumps(
    manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode("utf-8")
manifest_path.write_text(
    json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + chr(10),
    encoding="utf-8",
)
digest = hashlib.sha256(canonical_manifest).hexdigest()

output_path = os.environ.get("GITHUB_OUTPUT")
if output_path:
    line_sep = chr(10)
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"verdict={verdict}" + line_sep)
        output.write(f"evidence-path={manifest_path.resolve()}" + line_sep)
        output.write(f"result-path={copied_result.resolve()}" + line_sep)
        output.write(f"provenance-digest={digest}" + line_sep)
        output.write(f"command-exit-code={command_exit_code}" + line_sep)

summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
if summary_path:
    line_sep = chr(10)
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("## MNCS verification" + line_sep + line_sep)
        summary.write(f"- Verdict: {verdict}" + line_sep)
        summary.write(f"- Command exit code: {command_exit_code}" + line_sep)
        summary.write(f"- Evidence digest: {digest}" + line_sep)
        summary.write(f"- Evidence manifest: {manifest_path.resolve()}" + line_sep)

print(f"MNCS verification verdict: {verdict}")
print(f"Evidence manifest: {manifest_path.resolve()}")
print(f"Evidence digest: {digest}")
PY
