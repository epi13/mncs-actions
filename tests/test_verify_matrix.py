"""Phase 1 matrix: hardened verify behavior via lib + verify.sh.

Covers: PASS, FAIL, UNKNOWN, missing file, malformed JSON, structurally
invalid, invalid verdict, malformed nested check, contradictory PASS,
extra fields, evidence packaging (positive), receipt-only (negative),
nonzero verifier exit with valid result, and repeated invocation.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
VERIFY_SH = REPO / "actions" / "verify" / "verify.sh"
FIX = REPO / "tests" / "fixtures" / "verify"


def run_verify(result_src: Path | None, evidence_dir: Path, exit_code: int = 0, raw_text: str | None = None):
    """Invoke verify.sh with an isolated env; return (rc, outputs, evidence_dir)."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if raw_text is not None:
        result_file = evidence_dir.parent / "input-result.json"
        result_file.write_text(raw_text, encoding="utf-8")
    elif result_src is not None:
        result_file = evidence_dir.parent / "input-result.json"
        if result_src.suffix == ".txt":
            shutil.copyfile(result_src, result_file)
        else:
            shutil.copyfile(result_src, result_file)
    else:
        result_file = evidence_dir.parent / "does-not-exist.json"
        if result_file.exists():
            result_file.unlink()
    out_file = evidence_dir.parent / "github-output.txt"
    summary_file = evidence_dir.parent / "step-summary.md"
    out_file.write_text("", encoding="utf-8")
    summary_file.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["GITHUB_OUTPUT"] = str(out_file)
    env["GITHUB_STEP_SUMMARY"] = str(summary_file)
    env["GITHUB_REPOSITORY"] = "epi13/mncs-actions"
    env["GITHUB_REF"] = "refs/heads/test"
    env["GITHUB_SHA"] = "abc123"
    env["GITHUB_WORKFLOW"] = "test"
    env["GITHUB_RUN_ID"] = "1"
    env["GITHUB_ACTOR"] = "tester"
    env["GITHUB_EVENT_NAME"] = "push"
    env["RUNNER_NAME"] = "github-hosted"
    proc = subprocess.run(
        [str(VERIFY_SH), "--result-file", str(result_file),
         "--evidence-dir", str(evidence_dir),
         "--command-exit-code", str(exit_code),
         "--command", "fixture-command"],
        capture_output=True, text=True, env=env,
    )
    outputs: dict[str, str] = {}
    for line in out_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return proc, outputs, result_file


def test_lib_pass_fixture_valid():
    doc = json.loads((FIX / "pass.json").read_text(encoding="utf-8"))
    assert lib.validate_verification_result(doc) == []


def test_lib_fail_fixture_valid():
    doc = json.loads((FIX / "fail.json").read_text(encoding="utf-8"))
    assert lib.validate_verification_result(doc) == []


def test_lib_unknown_fixture_valid():
    doc = json.loads((FIX / "unknown.json").read_text(encoding="utf-8"))
    assert lib.validate_verification_result(doc) == []


def test_lib_contradictory_pass_rejected():
    doc = json.loads((FIX / "contradictory-pass-with-fail.json").read_text(encoding="utf-8"))
    errors = lib.validate_verification_result(doc)
    assert any("cannot contain a failing check" in e for e in errors)


def test_lib_invalid_verdict_rejected():
    doc = json.loads((FIX / "invalid-verdict.json").read_text(encoding="utf-8"))
    assert any("verdict" in e for e in lib.validate_verification_result(doc))


def test_lib_malformed_nested_check_rejected():
    doc = json.loads((FIX / "malformed-nested-check.json").read_text(encoding="utf-8"))
    errors = lib.validate_verification_result(doc)
    assert len(errors) >= 2


def test_lib_structurally_invalid_rejected():
    doc = json.loads((FIX / "structurally-invalid.json").read_text(encoding="utf-8"))
    assert any("checks" in e for e in lib.validate_verification_result(doc))


def test_lib_wrong_schema_rejected():
    doc = json.loads((FIX / "wrong-schema-version.json").read_text(encoding="utf-8"))
    assert any("schema_version" in e for e in lib.validate_verification_result(doc))


def test_lib_extra_fields_accepted_and_ignored():
    doc = json.loads((FIX / "extra-fields.json").read_text(encoding="utf-8"))
    assert lib.validate_verification_result(doc) == []
    assert doc["verdict"] == "PASS"


def test_verify_sh_pass_packages_evidence(tmp_path):
    proc, outputs, _ = run_verify(FIX / "pass.json", tmp_path / "evidence", 0)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "PASS"
    assert outputs["claim-status"] == "ESTABLISHED"
    assert outputs["manifest-digest"] == outputs["provenance-digest"]
    assert len(outputs["manifest-digest"]) == 64
    manifest = json.loads((tmp_path / "evidence" / "evidence-manifest.json").read_text(encoding="utf-8"))
    receipt = json.loads((tmp_path / "evidence" / "execution-receipt.json").read_text(encoding="utf-8"))
    assert manifest["verdict"] == "PASS"
    assert manifest["claim_status"] == "ESTABLISHED"
    assert manifest["receipt"]["path"] == "execution-receipt.json"
    assert receipt["claim_status"] == "ESTABLISHED"
    assert receipt["claim"]["verdict"] == "PASS"
    assert receipt["command_exit_code"] == 0
    assert (tmp_path / "evidence" / "verification-result.json").is_file()
    assert lib.validate_execution_receipt(receipt) == []


def test_verify_sh_fail_is_valid_claim(tmp_path):
    proc, outputs, _ = run_verify(FIX / "fail.json", tmp_path / "evidence", 0)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "FAIL"
    assert outputs["claim-status"] == "ESTABLISHED"
    assert (tmp_path / "evidence" / "evidence-manifest.json").is_file()
    assert (tmp_path / "evidence" / "execution-receipt.json").is_file()


def test_verify_sh_unknown_is_valid_claim(tmp_path):
    proc, outputs, _ = run_verify(FIX / "unknown.json", tmp_path / "evidence", 0)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "UNKNOWN"
    assert outputs["claim-status"] == "ESTABLISHED"


def test_verify_sh_missing_file_is_not_established(tmp_path):
    proc, outputs, _ = run_verify(None, tmp_path / "evidence", 0)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"
    assert outputs["claim-status"] == "NOT_ESTABLISHED"
    assert "evidence-path" not in outputs
    receipt = json.loads((tmp_path / "evidence" / "execution-receipt.json").read_text(encoding="utf-8"))
    assert receipt["claim_status"] == "NOT_ESTABLISHED"
    assert receipt["result"]["present"] is False
    assert not (tmp_path / "evidence" / "evidence-manifest.json").exists()
    assert lib.validate_execution_receipt(receipt) == []


def test_verify_sh_malformed_json_is_not_unknown(tmp_path):
    proc, outputs, _ = run_verify(FIX / "malformed.txt", tmp_path / "evidence", 0)
    assert proc.returncode == 2
    # Must never fabricate UNKNOWN for malformed data.
    assert outputs["verdict"] == "INVALID"
    assert outputs["claim-status"] == "NOT_ESTABLISHED"
    assert (tmp_path / "evidence" / "observed-result.json").is_file()
    assert not (tmp_path / "evidence" / "evidence-manifest.json").exists()


def test_verify_sh_structurally_invalid_is_not_established(tmp_path):
    proc, outputs, _ = run_verify(FIX / "structurally-invalid.json", tmp_path / "evidence", 0)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"


def test_verify_sh_invalid_verdict_is_not_established(tmp_path):
    proc, outputs, _ = run_verify(FIX / "invalid-verdict.json", tmp_path / "evidence", 0)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"


def test_verify_sh_malformed_nested_check_rejected(tmp_path):
    proc, outputs, _ = run_verify(FIX / "malformed-nested-check.json", tmp_path / "evidence", 0)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"


def test_verify_sh_contradictory_pass_rejected(tmp_path):
    proc, outputs, _ = run_verify(FIX / "contradictory-pass-with-fail.json", tmp_path / "evidence", 0)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"


def test_verify_sh_extra_fields_accepted(tmp_path):
    proc, outputs, _ = run_verify(FIX / "extra-fields.json", tmp_path / "evidence", 0)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "PASS"


def test_verify_sh_nonzero_exit_preserves_valid_claim(tmp_path):
    # Verifier exits nonzero but emits a valid result: claim is established
    # (receipt records the exit), the workflow gate must still fail.
    proc, outputs, _ = run_verify(FIX / "pass.json", tmp_path / "evidence", 2)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "PASS"
    assert outputs["claim-status"] == "ESTABLISHED"
    assert outputs["command-exit-code"] == "2"
    receipt = json.loads((tmp_path / "evidence" / "execution-receipt.json").read_text(encoding="utf-8"))
    assert receipt["command_exit_code"] == 2
    manifest = json.loads((tmp_path / "evidence" / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert manifest["command_exit_code"] == 2


def test_verify_sh_nonzero_exit_with_malformed_result(tmp_path):
    proc, outputs, _ = run_verify(FIX / "malformed.txt", tmp_path / "evidence", 2)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"
    receipt = json.loads((tmp_path / "evidence" / "execution-receipt.json").read_text(encoding="utf-8"))
    assert receipt["command_exit_code"] == 2
    assert receipt["claim_status"] == "NOT_ESTABLISHED"


def test_verify_sh_repeated_use_isolated(tmp_path):
    # Two sequential uses in the same workflow must not cross-contaminate.
    proc1, out1, _ = run_verify(FIX / "pass.json", tmp_path / "ev1", 0)
    proc2, out2, _ = run_verify(FIX / "fail.json", tmp_path / "ev2", 0)
    assert proc1.returncode == 0 and proc2.returncode == 0
    assert out1["verdict"] == "PASS" and out2["verdict"] == "FAIL"
    assert out1["manifest-digest"] != out2["manifest-digest"]
    assert (tmp_path / "ev1" / "verification-result.json").read_text() != (
        tmp_path / "ev2" / "verification-result.json"
    ).read_text()


def test_manifest_digest_is_stable_for_same_result(tmp_path):
    _, out1, _ = run_verify(FIX / "pass.json", tmp_path / "a", 0)
    _, out2, _ = run_verify(FIX / "pass.json", tmp_path / "b", 0)
    # Timestamps differ, but the result digest inside the manifest is stable.
    m1 = json.loads((tmp_path / "a" / "evidence-manifest.json").read_text(encoding="utf-8"))
    m2 = json.loads((tmp_path / "b" / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert m1["result"]["sha256"] == m2["result"]["sha256"]
    assert out1["result-path"] != out2["result-path"]
