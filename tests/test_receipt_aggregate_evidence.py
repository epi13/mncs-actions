"""Receipt/claim separation, check composition, aggregation, evidence."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
RUN_CHECK = REPO / "actions" / "run-check" / "run_check.sh"
AGGREGATE = REPO / "actions" / "aggregate" / "aggregate.sh"
CHECK_FIX = REPO / "tests" / "fixtures" / "check"


def isolated_env(tmp_path: Path):
    out = tmp_path / "github-output.txt"
    summary = tmp_path / "step-summary.md"
    out.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["GITHUB_OUTPUT"] = str(out)
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    for key, value in {
        "GITHUB_REPOSITORY": "epi13/mncs-actions",
        "GITHUB_REF": "refs/heads/test",
        "GITHUB_SHA": "rev123",
        "GITHUB_WORKFLOW": "test",
        "GITHUB_RUN_ID": "9",
        "GITHUB_ACTOR": "tester",
        "GITHUB_EVENT_NAME": "push",
        "RUNNER_NAME": "github-hosted",
    }.items():
        env[key] = value
    return env, out


def read_outputs(out_path: Path) -> dict:
    outputs: dict[str, str] = {}
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs


def test_receipt_always_valid_even_without_claim():
    receipt = lib.build_execution_receipt(
        command="fixture",
        command_exit_code=2,
        claim_status=lib.CLAIM_NOT_ESTABLISHED,
        result_present=False,
        result_valid=False,
        result_errors=["missing"],
        error_code="RESULT_NOT_ESTABLISHED",
    )
    assert lib.validate_execution_receipt(receipt) == []
    assert receipt["claim_status"] == "NOT_ESTABLISHED"
    assert receipt["claim"]["verdict"] == "NONE"


def test_aggregate_dominance_required_fail(tmp_path):
    verdict, unresolved = lib.aggregate_verdict(
        {"a": "PASS", "b": "FAIL", "c": "PASS"}, ["a", "b"]
    )
    assert verdict == "FAIL"


def test_aggregate_dominance_required_unknown(tmp_path):
    verdict, unresolved = lib.aggregate_verdict(
        {"a": "PASS", "b": "UNKNOWN"}, ["a", "b"]
    )
    assert verdict == "UNKNOWN"
    assert any("b" in item for item in unresolved)


def test_aggregate_all_pass(tmp_path):
    verdict, unresolved = lib.aggregate_verdict(
        {"a": "PASS", "b": "PASS"}, ["a", "b"]
    )
    assert verdict == "PASS"


def test_aggregate_missing_required_is_unknown_not_pass():
    verdict, _ = lib.aggregate_verdict({"a": "PASS"}, ["a", "missing"])
    assert verdict == "UNKNOWN"


def test_aggregate_optional_unknown_stays_visible_but_passes():
    verdict, unresolved = lib.aggregate_verdict(
        {"req": "PASS", "opt": "UNKNOWN"}, ["req"], ["opt"]
    )
    assert verdict == "PASS"
    assert any("opt" in item for item in unresolved)


def test_run_check_packages_valid_check(tmp_path):
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    ev = work / "evidence"
    src = CHECK_FIX / "pass.json"
    dst = work / "check.json"
    shutil.copyfile(src, dst)
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(RUN_CHECK), "--result-file", str(dst), "--evidence-dir", str(ev),
         "--command-exit-code", "0", "--command", "fixture",
         "--expected-id", "mncs-validation", "--expected-provider", "mncs-validator-rs"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    outputs = read_outputs(out_path)
    assert outputs["verdict"] == "PASS"
    assert outputs["claim-status"] == "ESTABLISHED"
    manifest = json.loads((ev / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "check"
    assert manifest["verdict"] == "PASS"


def test_run_check_rejects_id_mismatch(tmp_path):
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    ev = work / "evidence"
    dst = work / "check.json"
    shutil.copyfile(CHECK_FIX / "pass.json", dst)
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(RUN_CHECK), "--result-file", str(dst), "--evidence-dir", str(ev),
         "--command-exit-code", "0", "--command", "fixture",
         "--expected-id", "wrong-id", "--expected-provider", ""],
        capture_output=True, text=True, env=env, cwd=work,
    )
    assert proc.returncode == 2
    assert read_outputs(out_path)["verdict"] == "INVALID"


def test_aggregate_script_composes_mixed_boundary(tmp_path):
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    for name in ("pass.json", "unknown.json"):
        shutil.copyfile(CHECK_FIX / name, work / name)
    # Rename to stable check ids is already in fixtures; use required that
    # includes an UNKNOWN to prove UNKNOWN propagation.
    ev = work / "agg-evidence"
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(AGGREGATE), "--checks", "pass.json unknown.json",
         "--required", "mncs-validation,ebpf-backend",
         "--optional", "wasm-backend",
         "--evidence-dir", str(ev), "--working-dir", str(work),
         "--boundary", "test-boundary"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    # ebpf-backend is UNKNOWN in unknown.json? pass.json id is
    # mncs-validation (PASS), unknown.json id is ebpf-backend (UNKNOWN).
    outputs = read_outputs(out_path)
    assert outputs["verdict"] == "UNKNOWN"
    agg = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    assert agg["verdict"] == "UNKNOWN"
    assert any("ebpf-backend" in item for item in agg["unresolved"])
    assert lib.validate_aggregate_result(agg) == []


def test_aggregate_optional_unknown_does_not_block(tmp_path):
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    shutil.copyfile(CHECK_FIX / "pass.json", work / "a.json")
    shutil.copyfile(CHECK_FIX / "optional-unknown.json", work / "b.json")
    ev = work / "agg"
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(AGGREGATE), "--checks", "a.json b.json",
         "--required", "mncs-validation",
         "--optional", "wasm-backend",
         "--evidence-dir", str(ev), "--working-dir", str(work),
         "--boundary", "demo"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    outputs = read_outputs(out_path)
    assert outputs["verdict"] == "PASS"
    agg = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    assert agg["verdict"] == "PASS"
    assert any("wasm-backend" in item for item in agg["unresolved"])


def test_aggregate_records_revision_bindings_and_document_digest(tmp_path):
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    shutil.copyfile(CHECK_FIX / "pass.json", work / "check.json")
    ev = work / "agg"
    env, out_path = isolated_env(tmp_path)
    implementation = json.loads(
        (REPO / "revision-binding.json").read_text(encoding="utf-8")
    )["implementation_revision"]
    proc = subprocess.run(
        [str(AGGREGATE), "--checks", "check.json", "--required", "mncs-validation",
         "--evidence-dir", str(ev), "--working-dir", str(work),
         "--implementation-revision", implementation,
         "--carrier-revision", "carrier-ref"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    result = read_outputs(out_path)
    aggregate = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    receipt = json.loads((ev / "execution-receipt.json").read_text(encoding="utf-8"))
    manifest = json.loads((ev / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert result["aggregate-digest"] == lib.sha256_hex((ev / "aggregate-result.json").read_bytes())
    assert receipt["inputs"]["implementation_revision"] == implementation
    assert receipt["inputs"]["carrier_revision"] == "carrier-ref"
    assert manifest["boundary"]["implementation_revision"] == implementation
    assert manifest["boundary"]["carrier_revision"] == "carrier-ref"


def test_aggregate_rejects_invalid_input(tmp_path):
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    shutil.copyfile(CHECK_FIX / "invalid-missing-id.json", work / "bad.json")
    ev = work / "agg"
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(AGGREGATE), "--checks", "bad.json",
         "--required", "whatever",
         "--evidence-dir", str(ev), "--working-dir", str(work),
         "--boundary", "demo"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    assert proc.returncode == 2
    assert read_outputs(out_path)["verdict"] == "INVALID"


def test_evidence_reference_validation():
    assert lib.validate_evidence_reference({"kind": "rights-manifest"}) == []
    assert lib.validate_evidence_reference({}) != []
    assert lib.validate_evidence_reference({"kind": "x", "path": "../escape"}) != []
    assert lib.validate_evidence_reference({"kind": "x", "digest": "not-a-digest"}) != []
    assert lib.validate_evidence_reference(
        {"kind": "contract-snapshot", "path": "bundle/manifest.json",
         "digest": "a" * 64}
    ) == []


def test_safe_relative_paths():
    assert lib.is_safe_relative_path("bundle/manifest.json")
    assert not lib.is_safe_relative_path("/absolute/path")
    assert not lib.is_safe_relative_path("../escape")
    assert not lib.is_safe_relative_path("a/../b")
    assert not lib.is_safe_relative_path("a\\b")
    assert not lib.is_safe_relative_path("")


def test_canonical_bytes_stable():
    a = {"b": 1, "a": [3, 2, 1]}
    assert lib.canonical_bytes(a) == lib.canonical_bytes({"a": [3, 2, 1], "b": 1})
    assert lib.sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
