"""Semantic-conformance action: MNCS gate policy, host mirror, and provider.

`pressure/semantic-conformance.mncs` expresses the pure gate
(`gate` + `has_followups`) over bounded tallies; `lib/mncs_actions.py::
classify_conformance_report` mirrors it arm-for-arm for check projection;
`scripts/semantic_conformance.py` is transport only. These tests pin the
three together: every MNCS arm must exist with the same verdict the host
produces, and live provider runs (skipped without MNCS_BIN) must accept the
smoke contract and reject its mutant.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
PRESSURE = REPO / "pressure" / "semantic-conformance.mncs"
SCRIPT = REPO / "scripts" / "semantic_conformance.py"
FIXTURES = REPO / "tests" / "fixtures"
MNCS_BIN = Path(
    os.environ.get(
        "MNCS_BIN", "/home/epi13/Documents/Projects/mncs-language/target/debug/mncs"
    )
)


def needs_mncs_bin():
    if not MNCS_BIN.is_file():
        pytest.skip("mncs compiler binary unavailable (set MNCS_BIN)")


def report(summary, predicates=("p",), status="tested"):
    return {
        "schema_version": "mncs.conformance-report/1",
        "summary": dict(summary),
        "predicates": [
            {"predicate": name, "status": status} for name in predicates
        ],
    }


def base_summary(**overrides):
    summary = {"pass": 10, "fail": 0, "unknown": 0, "unsupported": 0}
    summary.update(overrides)
    return summary


def test_gate_arms_present_in_mncs():
    text = PRESSURE.read_text(encoding="utf-8")
    # Failure dominance.
    assert re.search(r"tally\.failed > 0\s*\{\s*return Status\.FAIL;", text)
    # Zero tested predicates is FAIL, never PASS.
    assert re.search(r"tally\.tested == 0\s*\{\s*return Status\.FAIL;", text)
    # Unknown execution outcomes are UNKNOWN.
    assert re.search(r"tally\.unknown > 0\s*\{\s*return Status\.UNKNOWN;", text)
    # Clean runs pass; unsupported surfaces only as follow-ups.
    assert re.search(r"return Status\.PASS;", text)
    assert re.search(r"fn has_followups.*tally\.unsupported > 0", text, re.DOTALL)
    # No arm may turn FAIL into PASS or UNKNOWN into PASS.
    assert "FAIL => Status.PASS" not in text
    assert "UNKNOWN => Status.PASS" not in text


def test_host_mirror_matches_mncs_arms():
    verdict, _, error = lib.classify_conformance_report(report(base_summary()))
    assert error is None and verdict == "PASS"
    verdict, _, error = lib.classify_conformance_report(report(base_summary(fail=2)))
    assert error is None and verdict == "FAIL"
    empty = base_summary()
    empty["pass"] = 0
    verdict, unresolved, error = lib.classify_conformance_report(
        report(empty, predicates=())
    )
    assert error is None and verdict == "FAIL" and unresolved
    verdict, unresolved, error = lib.classify_conformance_report(
        report(base_summary(unknown=3))
    )
    assert error is None and verdict == "UNKNOWN" and unresolved
    # Unsupported alone never downgrades PASS, but stays visible.
    verdict, unresolved, error = lib.classify_conformance_report(
        report(base_summary(unsupported=5))
    )
    assert error is None and verdict == "PASS" and unresolved
    # Malformed reports establish no claim.
    for bad in (
        [],
        {"schema_version": "wrong", "summary": base_summary(), "predicates": []},
        {"schema_version": "mncs.conformance-report/1", "predicates": []},
        {
            "schema_version": "mncs.conformance-report/1",
            "summary": base_summary(fail=True),
            "predicates": [],
        },
    ):
        verdict, _, error = lib.classify_conformance_report(bad)
        assert verdict is None and error, bad


def test_pressure_source_lexes_and_parses():
    needs_mncs_bin()
    proc = subprocess.run(
        [str(MNCS_BIN), "source-study", str(PRESSURE)],
        capture_output=True,
        text=True,
    )
    document = json.loads(proc.stdout)
    diags = [
        d for d in document.get("diagnostics", []) if d.get("stage") != "elaboration"
    ]
    assert diags == [], diags
    assert document.get("ast"), "pressure source has no AST"


def test_action_shape():
    action = yaml.safe_load((REPO / "actions" / "semantic-conformance" / "action.yml").read_text(encoding="utf-8"))
    for key in ("program", "backends", "seed", "cases", "working-directory", "result-file", "evidence-directory", "fail-on-unknown", "fail-on-fail"):
        assert key in action["inputs"], key
    for key in ("verdict", "claim-status", "evidence-path", "result-path", "execution-receipt-path", "manifest-digest", "command-exit-code"):
        assert key in action["outputs"], key


def run_provider(program: str, tmp_path: Path) -> dict:
    result_file = tmp_path / "result.json"
    report_file = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--program",
            program,
            "--backends",
            "mncs-portable-wasm-mvp",
            "--cases",
            "2",
            "--mncs-bin",
            str(MNCS_BIN),
            "--report-file",
            str(report_file),
            "--result-file",
            str(result_file),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    assert report_file.is_file(), "provider keeps the conformance report as evidence"
    return json.loads(result_file.read_text(encoding="utf-8"))


def test_provider_accepts_smoke_contract(tmp_path):
    needs_mncs_bin()
    result = run_provider(str(FIXTURES / "semantic-smoke.mncs"), tmp_path)
    assert result["schema_version"] == "mncs.check-result/1"
    assert result["id"] == "semantic-conformance"
    assert result["verdict"] == "PASS", result


def test_provider_rejects_mutant(tmp_path):
    needs_mncs_bin()
    result = run_provider(str(FIXTURES / "semantic-smoke-mutant.mncs"), tmp_path)
    assert result["verdict"] == "FAIL", result
