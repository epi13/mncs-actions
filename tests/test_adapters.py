"""Adapter behavior: rights/provenance and MNCS validator mappings."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
RIGHTS_ADAPTER = REPO / "adapters" / "rights_adapter.py"
VALIDATOR_ADAPTER = REPO / "adapters" / "validator_adapter.py"
RIGHTS_FIX = REPO / "tests" / "fixtures" / "rights"
VALIDATOR_FIX = REPO / "tests" / "fixtures" / "validator"


def test_rights_mapping_table():
    assert lib.map_rights_outcome("pass") == "PASS"
    assert lib.map_rights_outcome("blocked") == "FAIL"
    assert lib.map_rights_outcome("invalid") == "FAIL"
    assert lib.map_rights_outcome("pass-with-findings") == "UNKNOWN"
    assert lib.map_rights_outcome("review-required") == "UNKNOWN"
    assert lib.map_rights_outcome("unknown") == "UNKNOWN"
    # Unknown vocabulary never becomes PASS.
    assert lib.map_rights_outcome("future-outcome") == "UNKNOWN"
    assert lib.map_rights_outcome("") == "UNKNOWN"


def test_classify_rights_report_table():
    verdict, _, error = lib.classify_rights_report({"outcome": "pass"})
    assert (verdict, error) == ("PASS", None)
    verdict, _, error = lib.classify_rights_report(
        {"outcome": "blocked", "structural_valid": True}
    )
    assert (verdict, error) == ("FAIL", None)
    verdict, _, error = lib.classify_rights_report(
        {"outcome": "invalid", "structural_valid": False}
    )
    assert (verdict, error) == ("FAIL", None)
    for outcome in ("pass-with-findings", "review-required", "unknown"):
        verdict, _, error = lib.classify_rights_report({"outcome": outcome})
        assert (verdict, error) == ("UNKNOWN", None), outcome


def test_classify_missing_outcome_is_no_claim():
    for bad in ({}, {"outcome": ""}, {"outcome": None}, {"outcome": 3}, "not-a-dict", None):
        verdict, _, error = lib.classify_rights_report(bad)
        assert verdict is None and error, bad


def test_classify_contradictory_reports_are_no_claim():
    verdict, _, error = lib.classify_rights_report(
        {"outcome": "pass", "structural_valid": False}
    )
    assert verdict is None and error
    verdict, _, error = lib.classify_rights_report(
        {"outcome": "invalid", "structural_valid": True}
    )
    assert verdict is None and error


def test_classify_identity_mismatch_downgrades_pass_to_fail():
    verdict, unresolved, error = lib.classify_rights_report(
        {"outcome": "pass", "manifest_identity_matches": False}
    )
    assert (verdict, error) == ("FAIL", None)
    assert any("identity mismatch" in item for item in unresolved)


def test_classify_identity_mismatch_keeps_unknown_visible():
    verdict, unresolved, error = lib.classify_rights_report(
        {"outcome": "review-required", "manifest_identity_matches": False}
    )
    assert (verdict, error) == ("UNKNOWN", None)
    assert any("identity mismatch" in item for item in unresolved)


def test_classify_unrecognized_outcome_is_unknown_with_drift_note():
    verdict, unresolved, error = lib.classify_rights_report({"outcome": "future-outcome"})
    assert (verdict, error) == ("UNKNOWN", None)
    assert any("drift" in item for item in unresolved)


def test_validator_mapping_table():
    verdict, _ = lib.map_validator_computed_status(valid=True, computed_status="PASS")
    assert verdict == "PASS"
    verdict, _ = lib.map_validator_computed_status(valid=True, computed_status="FAIL")
    assert verdict == "FAIL"
    verdict, _ = lib.map_validator_computed_status(valid=True, computed_status="UNKNOWN")
    assert verdict == "UNKNOWN"
    verdict, _ = lib.map_validator_computed_status(valid=False, computed_status="FAIL")
    assert verdict == "FAIL"
    verdict, notes = lib.map_validator_computed_status(valid=True, computed_status="WEIRD")
    assert verdict == "UNKNOWN"
    assert notes


def run_adapter(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, cwd=str(REPO),
    )


def test_rights_adapter_pass(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(RIGHTS_FIX / "pass.json"), "--output", str(out),
        "--check-id", "rights-provenance", "--provider", "mncs-rights-provenance",
        "--contract-revision", "0.3.0",
    ])
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "PASS"
    assert lib.validate_check_result(doc) == []


def test_rights_adapter_review_is_unknown_not_pass(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(RIGHTS_FIX / "review-required.json"), "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "UNKNOWN"
    assert any("human review" in item for item in doc["unresolved"])
    assert lib.validate_check_result(doc) == []


def test_rights_adapter_blocked_is_fail(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(RIGHTS_FIX / "blocked.json"), "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "FAIL"


def test_rights_adapter_invalid_is_fail(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(RIGHTS_FIX / "invalid.json"), "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "FAIL"


def test_rights_adapter_malformed_input_fails(tmp_path):
    out = tmp_path / "check.json"
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(bad), "--output", str(out),
    ])
    assert proc.returncode == 2
    assert not out.exists()


def test_rights_adapter_missing_outcome_is_no_claim(tmp_path):
    out = tmp_path / "check.json"
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"severity": "none"}), encoding="utf-8")
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(report), "--output", str(out),
    ])
    assert proc.returncode == 2
    assert not out.exists()


def test_rights_adapter_contradictory_pass_is_no_claim(tmp_path):
    out = tmp_path / "check.json"
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps({"outcome": "pass", "structural_valid": False}), encoding="utf-8"
    )
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(report), "--output", str(out),
    ])
    assert proc.returncode == 2
    assert not out.exists()


def test_rights_adapter_identity_mismatch_downgrades_pass(tmp_path):
    out = tmp_path / "check.json"
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps({"outcome": "pass", "manifest_identity_matches": False}),
        encoding="utf-8",
    )
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(report), "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "FAIL"
    assert any("identity mismatch" in item for item in doc["unresolved"])
    assert lib.validate_check_result(doc) == []


def test_rights_adapter_unrecognized_outcome_is_unknown(tmp_path):
    out = tmp_path / "check.json"
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"outcome": "future-outcome"}), encoding="utf-8")
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(report), "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "UNKNOWN"
    assert any("drift" in item for item in doc["unresolved"])
    assert lib.validate_check_result(doc) == []


def test_rights_adapter_rejects_malformed_binding(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(RIGHTS_ADAPTER, [
        "--input", str(RIGHTS_FIX / "pass.json"), "--output", str(out),
        "--manifest-digest", "not-a-digest",
    ])
    assert proc.returncode == 2
    assert not out.exists()


def test_validator_adapter_pass(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(VALIDATOR_ADAPTER, [
        "--input", str(VALIDATOR_FIX / "valid-pass.json"), "--output", str(out),
        "--contract-revision", "0.2",
    ])
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "PASS"
    assert lib.validate_check_result(doc) == []


def test_validator_adapter_unknown(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(VALIDATOR_ADAPTER, [
        "--input", str(VALIDATOR_FIX / "valid-unknown.json"), "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "UNKNOWN"


def test_validator_adapter_invalid_is_fail(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(VALIDATOR_ADAPTER, [
        "--input", str(VALIDATOR_FIX / "invalid-fail.json"), "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "FAIL"
    assert any("hash mismatch" in item for item in doc["unresolved"])


def test_validator_adapter_malformed_fails(tmp_path):
    out = tmp_path / "check.json"
    proc = run_adapter(VALIDATOR_ADAPTER, [
        "--input", str(VALIDATOR_FIX / "malformed.txt"), "--output", str(out),
    ])
    assert proc.returncode == 2
