#!/usr/bin/env python3
"""Deterministic promotion-gate driver (test tooling, not semantics).

Runs the owner-native MNCS promotion evaluator over a bounded fixture
universe and asserts the exact expected outcome per vector:

- the PASS universe must evaluate to exactly PASS (the job fails otherwise);
- negative vectors must yield their specified FAIL / UNKNOWN / no-claim;
- every digest bound in a PASS promotion result must recompute from the
  exact consumed bytes (forgery/tamper detection at the transport edge).

Verdict semantics belong to the evaluator; this driver only asserts the
contract. Exit nonzero on any deviation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PASS_SUBJECT = ("epi13/mncs-actions", "a" * 40)


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _evaluate(
    evaluator: str,
    boundary: Path,
    authority_map: Path,
    checks: list[Path],
    obligations: list[Path],
    output: Path,
) -> tuple[int, str]:
    command = [
        sys.executable,
        evaluator,
        "--boundary",
        str(boundary),
        "--authority-map",
        str(authority_map),
        "--subject-repository",
        PASS_SUBJECT[0],
        "--subject-commit",
        PASS_SUBJECT[1],
        "--output",
        str(output),
    ]
    if checks:
        command += ["--checks", *[str(path) for path in checks]]
    if obligations:
        command += ["--obligations", *[str(path) for path in obligations]]
    process = subprocess.run(command, capture_output=True, text=True, timeout=120)
    return process.returncode, process.stderr


def _verify_rebinding(
    result: dict, files: dict[str, Path], problems: list[str]
) -> None:
    """Every bound digest must recompute from the exact consumed bytes."""
    for ref in result.get("references", []):
        kind = ref.get("kind")
        if kind == "check-result":
            key = ("check", ref.get("check_id"))
        elif kind == "mncds-obligation-record":
            key = ("obligation", ref.get("obligation_key"))
        elif kind == "promotion-boundary":
            key = ("boundary", ref.get("boundary_id"))
        elif kind == "authority-map":
            key = ("authority-map", "")
        else:
            problems.append(f"unexpected reference kind: {kind}")
            continue
        path = files.get(key)
        if path is None:
            problems.append(f"reference binds an unconsumed file: {key}")
            continue
        if ref.get("digest") != _digest(path):
            problems.append(f"digest mismatch for {key}: evidence was altered")


def main() -> int:
    parser = argparse.ArgumentParser(description="Assert the promotion-gate contract.")
    parser.add_argument("--evaluator", required=True)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--lib-dir", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--summary", default="")
    args = parser.parse_args()

    sys.path.insert(0, args.lib_dir)
    import mncs_actions as lib

    root = args.fixtures
    boundary = root / "boundary.json"
    authority_map = root / "authority-map.json"
    checks = root / "checks"
    obligations = root / "obligations"
    negatives = root / "negatives"
    outdir = args.output_dir
    outdir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    lines: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        lines.append(f"- {name}: {'PASS' if condition else 'FAIL'}{(' ' + detail) if detail else ''}")
        if not condition:
            failures.append(name)

    pass_checks = [checks / "mncs-pass.json", checks / "mncds-pass.json", checks / "obligations-pass.json"]
    pass_obligations = [obligations / "resolved.json"]
    pass_output = outdir / "promotion-pass.json"
    code, stderr = _evaluate(
        args.evaluator, boundary, authority_map, pass_checks, pass_obligations, pass_output
    )
    result = json.loads(pass_output.read_text(encoding="utf-8")) if pass_output.is_file() else None
    check("pass-universe-exit-0", code == 0, stderr.strip().splitlines()[-1] if code else "")
    check(
        "pass-universe-verdict-PASS",
        result is not None and result.get("verdict") == "PASS",
        (result or {}).get("summary", "no result") if isinstance(result, dict) else "",
    )
    if result is not None:
        claim_errors = lib.validate_promotion_claim(
            result,
            boundary_id="family-promotion-gate",
            subject_repository=PASS_SUBJECT[0],
            subject_commit=PASS_SUBJECT[1],
        )
        check("pass-universe-transport-valid", claim_errors == [], "; ".join(claim_errors))
        rebind_problems: list[str] = []
        _verify_rebinding(
            result,
            {
                ("check", "mncs-validation"): checks / "mncs-pass.json",
                ("check", "mncds-development-record"): checks / "mncds-pass.json",
                ("check", "mncds-obligations"): checks / "obligations-pass.json",
                ("obligation", "pressure.gate.resolved-gap"): obligations / "resolved.json",
                ("boundary", "family-promotion-gate"): boundary,
                ("authority-map", ""): authority_map,
            },
            rebind_problems,
        )
        check("pass-universe-evidence-rebound", rebind_problems == [], "; ".join(rebind_problems))
    else:
        check("pass-universe-transport-valid", False, "no result")
        check("pass-universe-evidence-rebound", False, "no result")

    vectors = [
        ("required-fail-is-FAIL", "FAIL", [negatives / "check-fail.json", checks / "mncds-pass.json", checks / "obligations-pass.json"], [obligations / "resolved.json"]),
        ("required-unknown-is-UNKNOWN", "UNKNOWN", [checks / "mncs-pass.json", negatives / "check-unknown.json", checks / "obligations-pass.json"], [obligations / "resolved.json"]),
        ("missing-revision-is-UNKNOWN", "UNKNOWN", [checks / "mncs-pass.json", negatives / "check-missing-revision.json", checks / "obligations-pass.json"], [obligations / "resolved.json"]),
        ("open-obligation-is-UNKNOWN", "UNKNOWN", pass_checks, [negatives / "obligation-open.json"]),
        ("rejected-obligation-is-FAIL", "FAIL", pass_checks, [negatives / "obligation-rejected.json"]),
    ]
    for index, (name, expected, vec_checks, vec_obligations) in enumerate(vectors):
        output = outdir / f"promotion-{name}.json"
        code, _ = _evaluate(args.evaluator, boundary, authority_map, vec_checks, vec_obligations, output)
        got = None
        if output.is_file():
            got = json.loads(output.read_text(encoding="utf-8")).get("verdict")
        check(name, code == 0 and got == expected, f"got {got} (exit {code})")

    no_claim_vectors = [
        ("wrong-authority-no-claim", [negatives / "check-wrong-provider.json", checks / "mncds-pass.json", checks / "obligations-pass.json"], [obligations / "resolved.json"]),
        ("wrong-subject-no-claim", [checks / "mncs-pass.json", negatives / "check-wrong-subject.json", checks / "obligations-pass.json"], [obligations / "resolved.json"]),
        ("duplicate-checks-no-claim", [checks / "mncs-pass.json", checks / "mncs-pass.json", checks / "obligations-pass.json"], [obligations / "resolved.json"]),
        ("duplicate-obligations-no-claim", pass_checks, [obligations / "resolved.json", obligations / "resolved.json"]),
    ]
    for index, (name, vec_checks, vec_obligations) in enumerate(no_claim_vectors):
        output = outdir / f"promotion-{name}.json"
        code, _ = _evaluate(args.evaluator, boundary, authority_map, vec_checks, vec_obligations, output)
        check(name, code == 2 and not output.is_file(), f"exit {code}")

    if result is not None:
        forged = json.loads(json.dumps(result))
        forged["references"][0]["digest"] = "sha256:" + "f" * 64
        rebind_problems = []
        _verify_rebinding(
            forged,
            {
                ("check", "mncs-validation"): checks / "mncs-pass.json",
                ("check", "mncds-development-record"): checks / "mncds-pass.json",
                ("check", "mncds-obligations"): checks / "obligations-pass.json",
                ("obligation", "pressure.gate.resolved-gap"): obligations / "resolved.json",
                ("boundary", "family-promotion-gate"): boundary,
                ("authority-map", ""): authority_map,
            },
            rebind_problems,
        )
        # Shape validation cannot recompute digests against files it never
        # sees; rebinding verification (above) is the forgery detector
        # wherever consumed bytes are at hand. Transport also binds the
        # consumed promotion file itself via aggregate checks[].digest.
        check("forged-digest-detected", bool(rebind_problems))
    else:
        check("forged-digest-detected", False, "no PASS result")

    summary = ["## Promotion gate", *lines]
    if args.summary:
        Path(args.summary).write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
