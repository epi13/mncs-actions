#!/usr/bin/env bash
# Sync internal mncs-actions pins in the reusable family workflow.
#
# The reusable workflow cannot resolve its own pinned revision dynamically
# (GitHub Actions requires literal `uses: <repo>/<path>@<ref>` and
# `./actions/...` inside a reusable workflow resolves to the CALLER repo).
# So the workflow pins its own actions with immutable full-length SHAs that
# are kept in sync on release.
#
# Usage: scripts/sync-pins.sh <40-hex-sha>
#
# On release: merge the release, note the release commit SHA, run this
# script with that SHA, commit the result, and tag the release. At any
# tagged release X, workflow X executes exactly action X.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <40-hex-commit-sha>" >&2
  exit 64
fi

PIN="$1"
if [[ ! "$PIN" =~ ^[0-9a-f]{40}$ ]]; then
  echo "error: pin must be a full 40-char lowercase hex commit SHA, got: $PIN" >&2
  exit 64
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$ROOT/.github/workflows/mncs-family-verify.yml"

if [[ ! -f "$WORKFLOW" ]]; then
  echo "error: workflow not found: $WORKFLOW" >&2
  exit 1
fi

# Rewrite only mncs-actions self-references on `uses:` lines; third-party
# pins (actions/*) are owned by their own release lines and left untouched.
# Doc comments use the placeholder `@<pinned-sha>` and are never rewritten.
python3 - "$WORKFLOW" "$PIN" <<'PY'
import re
import sys

path, pin = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
count = 0
for index, line in enumerate(lines):
    stripped = line.lstrip()
    if not stripped.startswith("uses:"):
        continue
    updated, n = re.subn(
        r"(epi13/mncs-actions/actions/[^@\s]+@)[^\s\"']+",
        lambda match: match.group(1) + pin,
        line,
    )
    if n:
        lines[index] = updated
        count += n
if count == 0:
    print("error: no mncs-actions self-references found to pin", file=sys.stderr)
    raise SystemExit(1)
open(path, "w", encoding="utf-8").write("".join(lines))
print(f"pinned {count} mncs-actions self-reference(s) to {pin}")
PY
