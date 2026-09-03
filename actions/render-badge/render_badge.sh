#!/usr/bin/env bash
# MNCS deterministic badge renderer. The badge is presentation only; the
# sidecar carries optional bindings back to aggregate evidence.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../lib" && pwd)"

MNCS_LIB_DIR="$LIB_DIR" \
python3 - <<'PY'
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.environ["MNCS_LIB_DIR"])
from mncs_actions import (  # noqa: E402
    BADGE_INVALID,
    BADGE_LABEL_DEFAULT,
    build_badge_doc,
    canonical_bytes,
    check_revision_token,
    is_safe_relative_path,
    project_badge_state,
    render_badge_svg,
    resolve_implementation_revision,
    sha256_hex,
    validate_badge,
    validate_badge_label,
)


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"::error::{name} is required", file=sys.stderr)
        raise SystemExit(2)
    return value


def optional_digest(name: str) -> str:
    value = os.environ.get(name, "")
    if value and not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", value):
        print(f"::error::{name} must be hex64 or sha256:hex64", file=sys.stderr)
        raise SystemExit(2)
    return value


def output_path(value: str, name: str) -> Path:
    if not value:
        print(f"::error::{name} is required", file=sys.stderr)
        raise SystemExit(2)
    candidate = Path(value)
    # Absolute paths are useful for local callers and are already explicit;
    # relative paths are confined to the action's working directory.
    if not candidate.is_absolute() and not is_safe_relative_path(value):
        print(f"::error::{name} must be a safe relative path", file=sys.stderr)
        raise SystemExit(2)
    return candidate


verdict = required_env("MNCS_BADGE_VERDICT").strip()
state = project_badge_state(verdict)
if state is None:
    print("::error::verdict must be PASS, FAIL, UNKNOWN, or INVALID", file=sys.stderr)
    raise SystemExit(2)

label = os.environ.get("MNCS_BADGE_LABEL", "") or BADGE_LABEL_DEFAULT
label_errors = validate_badge_label(label)
if label_errors:
    for error in label_errors:
        print(f"::error::{error}", file=sys.stderr)
    raise SystemExit(2)

implementation_revision, warnings, revision_error = resolve_implementation_revision(
    os.environ.get("MNCS_IMPLEMENTATION_REVISION", ""),
    Path(os.environ["MNCS_LIB_DIR"]).parent,
)
if revision_error:
    print(f"::error::{revision_error}", file=sys.stderr)
    raise SystemExit(2)
for warning in warnings:
    print(f"::warning::{warning}", file=sys.stderr)

carrier_revision = os.environ.get("MNCS_CARRIER_REVISION", "")
if carrier_revision:
    token_error = check_revision_token("carrier_revision", carrier_revision)
    if token_error:
        print(f"::error::{token_error}", file=sys.stderr)
        raise SystemExit(2)

aggregate_digest = optional_digest("MNCS_BADGE_AGGREGATE_DIGEST")
manifest_digest = optional_digest("MNCS_BADGE_MANIFEST_DIGEST")
badge_path = output_path(os.environ.get("MNCS_BADGE_OUTPUT_FILE", ""), "output-file")
sidecar_path = output_path(os.environ.get("MNCS_BADGE_SIDECAR_FILE", ""), "sidecar-file")
if badge_path.resolve() == sidecar_path.resolve():
    print("::error::output-file and sidecar-file must be different", file=sys.stderr)
    raise SystemExit(2)

badge = render_badge_svg(label, state)
sidecar = build_badge_doc(
    label=label,
    verdict=state,
    repository=os.environ.get("MNCS_BADGE_REPOSITORY", ""),
    subject_commit=os.environ.get("MNCS_BADGE_SUBJECT_COMMIT", ""),
    boundary=os.environ.get("MNCS_BADGE_BOUNDARY", ""),
    aggregate_digest=aggregate_digest,
    manifest_digest=manifest_digest,
    carrier_revision=carrier_revision,
    implementation_revision=implementation_revision or "",
)
errors = validate_badge(sidecar)
if errors:
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    raise SystemExit(2)

badge_path.parent.mkdir(parents=True, exist_ok=True)
sidecar_path.parent.mkdir(parents=True, exist_ok=True)
badge_path.write_text(badge, encoding="utf-8")
sidecar_path.write_bytes(canonical_bytes(sidecar) + b"\n")


def append_output(key: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        safe = value.replace("\n", " ").replace("\r", " ")
        handle.write(f"{key}={safe}\n")


append_output("state", state)
append_output("badge-path", str(badge_path.resolve()))
append_output("sidecar-path", str(sidecar_path.resolve()))
append_output("badge-digest", sha256_hex(badge.encode("utf-8")))
append_output("sidecar-digest", sha256_hex(canonical_bytes(sidecar)))
print(f"MNCS badge state: {state}")
if state == BADGE_INVALID:
    print("::warning::Badge represents an unestablished claim.", file=sys.stderr)
PY
