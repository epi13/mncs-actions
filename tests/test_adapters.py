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
