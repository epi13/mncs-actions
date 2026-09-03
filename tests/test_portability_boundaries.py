"""Portability and workflow-boundary regression tests.

These tests exist because the previous family-self-test executed from
within mncs-actions and masked three real defects:

- reusable workflow used ./actions/... (caller-relative, broken cross-repo)
- omitted providers were still listed as aggregate inputs (-> INVALID)
- working-directory was applied twice (src/src/...)

Each test below pins one of those boundaries.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
AGGREGATE_SH = REPO / "actions" / "aggregate" / "aggregate.sh"
RUN_CHECK_SH = REPO / "actions" / "run-check" / "run_check.sh"
CHECK_FIX = REPO / "tests" / "fixtures" / "check"
REUSABLE = REPO / ".github" / "workflows" / "mncs-family-verify.yml"
AGGREGATE_ACTION = REPO / "actions" / "aggregate" / "action.yml"


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


def run_aggregate(work: Path, checks: str, required: str, optional: str, ev_name: str, env, out_path: Path):
    work.mkdir(parents=True, exist_ok=True)
    ev = work / ev_name
    proc = subprocess.run(
        [str(AGGREGATE_SH), "--checks", checks,
         "--required", required, "--optional", optional,
         "--evidence-dir", str(ev), "--working-dir", ".",
         "--boundary", "test"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    return proc, read_outputs(out_path), ev


# ---- 1. Portability: reusable workflow must not use caller-relative paths ----

def test_reusable_workflow_uses_pinned_self_repo_actions():
    text = REUSABLE.read_text(encoding="utf-8")
    assert "./actions/run-check" not in text, "caller-relative run-check breaks cross-repo callers"
    assert "./actions/aggregate" not in text, "caller-relative aggregate breaks cross-repo callers"
    assert "epi13/mncs-actions/actions/run-check@" in text
    assert "epi13/mncs-actions/actions/aggregate@" in text


def test_reusable_workflow_lists_only_ran_providers():
    # Omitted providers must not be passed as explicit aggregate inputs.
    text = REUSABLE.read_text(encoding="utf-8")
    assert "inputs.mncs-command" in text and "inputs.mncs-result-file" in text
    # Conditional inclusion form: command non-empty -> file, else empty string.
    assert "inputs.mncs-command != ''" in text
    assert "inputs.rights-command != ''" in text
    assert "inputs.project-command != ''" in text
    doc = yaml.safe_load(text)
    agg_step = [s for s in doc["jobs"]["family-verify"]["steps"] if s.get("id") == "aggregate"][0]
    checks_expr = agg_step["with"]["checks"]
    # Each line must be conditional, not a bare file path.
    assert "inputs.mncs-result-file" in checks_expr
    assert "|| ''" in checks_expr


def test_aggregate_action_single_resolution_invariant():
    text = AGGREGATE_ACTION.read_text(encoding="utf-8")
    # Composite already enters working-directory; implementation root is ".".
    assert "MNCS_WORKING_DIRECTORY: ." in text or "MNCS_WORKING_DIRECTORY: '.'" in text
    assert "MNCS_WORKING_DIRECTORY: ${{ inputs.working-directory }}" not in text


# ---- 2. Omission vs missing: the core semantic distinction ----

def test_omitted_optional_provider_stays_pass(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "mncs.json")
    _shutil.copyfile(CHECK_FIX / "rights-pass.json", work / "rights.json")
    env, out_path = isolated_env(tmp_path)
    # project check omitted from the input list entirely (not applicable).
    proc, outputs, ev = run_aggregate(work, "mncs.json rights.json",
                                      "mncs-validation,rights-provenance",
                                      "project-tests", "agg", env, out_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "PASS"
    agg = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    assert agg["verdict"] == "PASS"


def test_absent_required_provider_is_unknown_not_invalid(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "mncs.json")
    _shutil.copyfile(CHECK_FIX / "rights-pass.json", work / "rights.json")
    env, out_path = isolated_env(tmp_path)
    # project-tests required but intentionally absent from inputs.
    proc, outputs, ev = run_aggregate(work, "mncs.json rights.json",
                                      "mncs-validation,rights-provenance,project-tests",
                                      "", "agg", env, out_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "UNKNOWN"
    assert outputs["claim-status"] == "ESTABLISHED"
    agg = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    assert agg["verdict"] == "UNKNOWN"
    assert any("project-tests" in item for item in agg["unresolved"])


def test_explicit_missing_file_is_invalid(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "real.json").write_text((CHECK_FIX / "pass.json").read_text(), encoding="utf-8")
    env, out_path = isolated_env(tmp_path)
    proc, outputs, ev = run_aggregate(work, "real.json does-not-exist.json",
                                      "mncs-validation", "", "agg", env, out_path)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"
    assert outputs["claim-status"] == "NOT_ESTABLISHED"


def test_empty_checks_with_required_is_unknown_not_invalid(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(AGGREGATE_SH), "--checks", "   ",
         "--required", "mncs-validation",
         "--evidence-dir", str(work / "agg"), "--working-dir", ".",
         "--boundary", "test"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    outputs = read_outputs(out_path)
    # Whitespace-only input parses as "no checks supplied": missing required
    # becomes UNKNOWN (ESTABLISHED), never INVALID. This is what lets the
    # family workflow omit not-applicable providers without breaking.
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "UNKNOWN"
    assert outputs["claim-status"] == "ESTABLISHED"


def test_required_fail_dominates_unknown(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "fail.json", work / "fail.json")
    _shutil.copyfile(CHECK_FIX / "unknown.json", work / "unk.json")
    env, out_path = isolated_env(tmp_path)
    proc, outputs, ev = run_aggregate(work, "fail.json unk.json",
                                      "project-tests,ebpf-backend", "", "agg", env, out_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "FAIL"


def test_required_unknown_without_fail_is_unknown(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "a.json")
    _shutil.copyfile(CHECK_FIX / "unknown.json", work / "b.json")
    env, out_path = isolated_env(tmp_path)
    proc, outputs, ev = run_aggregate(work, "a.json b.json",
                                      "mncs-validation,ebpf-backend", "", "agg", env, out_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "UNKNOWN"


def test_optional_fail_stays_visible_but_passes(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "a.json")
    fail_opt = json.loads((CHECK_FIX / "fail.json").read_text(encoding="utf-8"))
    fail_opt["id"] = "experimental-backend"
    (work / "opt.json").write_text(json.dumps(fail_opt), encoding="utf-8")
    env, out_path = isolated_env(tmp_path)
    proc, outputs, ev = run_aggregate(work, "a.json opt.json",
                                      "mncs-validation", "experimental-backend",
                                      "agg", env, out_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs["verdict"] == "PASS"
    agg = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    assert any("experimental-backend" in item and "FAIL" in item for item in agg["unresolved"])


# ---- 3. Working directory single resolution ----

def test_nested_working_directory_resolves_once(tmp_path):
    import shutil as _shutil

    # Simulate caller working-directory: src (composite enters it, impl uses ".").
    root = tmp_path / "caller"
    src = root / "src"
    src.mkdir(parents=True)
    _shutil.copyfile(CHECK_FIX / "pass.json", src / "mncs.json")
    _shutil.copyfile(CHECK_FIX / "rights-pass.json", src / "rights.json")
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(AGGREGATE_SH), "--checks", "mncs.json rights.json",
         "--required", "mncs-validation,rights-provenance",
         "--evidence-dir", "agg-ev", "--working-dir", ".",
         "--boundary", "nested-test"],
        capture_output=True, text=True, env=env, cwd=src,
    )
    outputs = read_outputs(out_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert outputs["verdict"] == "PASS"
    assert (src / "agg-ev" / "aggregate-result.json").is_file()
    # Must NOT have created a doubled src/src path.
    assert not (src / "src").exists()


def test_run_check_nested_directory(tmp_path):
    import shutil as _shutil

    root = tmp_path / "caller2"
    src = root / "src"
    src.mkdir(parents=True)
    _shutil.copyfile(CHECK_FIX / "pass.json", src / "check.json")
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(RUN_CHECK_SH), "--result-file", "check.json",
         "--evidence-dir", "ev", "--command-exit-code", "0",
         "--command", "fixture", "--expected-id", "mncs-validation",
         "--expected-provider", "mncs-validator-rs"],
        capture_output=True, text=True, env=env, cwd=src,
    )
    assert proc.returncode == 0, proc.stderr
    assert read_outputs(out_path)["verdict"] == "PASS"
    assert (src / "ev" / "check-result.json").is_file()


# ---- 4. Cross-repository independence ----

def test_scripts_work_from_bare_caller_without_local_actions(tmp_path):
    # Fake caller repo: no actions/ directory at all. Scripts are invoked by
    # absolute path (as the portable reusable workflow does via
    # epi13/mncs-actions/...@rev), proving caller contents don't matter.
    import shutil as _shutil

    caller = tmp_path / "fake-caller"
    caller.mkdir()
    assert not (caller / "actions").exists()
    _shutil.copyfile(CHECK_FIX / "pass.json", caller / "mncs.json")
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(AGGREGATE_SH), "--checks", "mncs.json",
         "--required", "mncs-validation",
         "--evidence-dir", "agg", "--working-dir", ".",
         "--boundary", "xrepo"],
        capture_output=True, text=True, env=env, cwd=caller,
    )
    assert proc.returncode == 0, proc.stderr
    assert read_outputs(out_path)["verdict"] == "PASS"


# ---- 5. Evidence integrity ----

def test_aggregate_manifest_carries_component_references(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "a.json")
    _shutil.copyfile(CHECK_FIX / "rights-pass.json", work / "b.json")
    env, out_path = isolated_env(tmp_path)
    proc, outputs, ev = run_aggregate(work, "a.json b.json",
                                      "mncs-validation,rights-provenance",
                                      "", "agg", env, out_path)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((ev / "evidence-manifest.json").read_text(encoding="utf-8"))
    refs = manifest.get("references", [])
    assert len(refs) == 2
    kinds = {r["kind"] for r in refs}
    assert kinds == {"check-result"}
    for ref in refs:
        assert len(ref["digest"]) == 64
        assert ref["path"] in ("a.json", "b.json")
    agg = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    for entry in agg["checks"]:
        if entry["id"] in ("mncs-validation", "rights-provenance"):
            assert len(entry.get("digest", "")) == 64


def test_aggregate_result_digest_deterministic_for_same_inputs(tmp_path):
    import shutil as _shutil

    for trial in ("t1", "t2"):
        work = tmp_path / trial
        work.mkdir()
        _shutil.copyfile(CHECK_FIX / "pass.json", work / "a.json")
        env, out_path = isolated_env(work)
        proc = subprocess.run(
            [str(AGGREGATE_SH), "--checks", "a.json",
             "--required", "mncs-validation",
             "--evidence-dir", "agg", "--working-dir", ".",
             "--boundary", "det"],
            capture_output=True, text=True, env=env, cwd=work,
        )
        assert proc.returncode == 0, proc.stderr
    # Component bytes identical -> aggregate-result bytes identical
    # (manifest timestamps differ, but the claim document is deterministic).
    a1 = (tmp_path / "t1" / "agg" / "aggregate-result.json").read_bytes()
    a2 = (tmp_path / "t2" / "agg" / "aggregate-result.json").read_bytes()
    assert a1 == a2


# ---- 6. Identity binding ----

def test_duplicate_check_ids_are_invalid(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "a.json")
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "b.json")
    env, out_path = isolated_env(tmp_path)
    proc, outputs, _ = run_aggregate(work, "a.json b.json",
                                     "mncs-validation", "", "agg", env, out_path)
    assert proc.returncode == 2
    assert outputs["verdict"] == "INVALID"


def test_provider_impersonation_rejected(tmp_path):
    import shutil as _shutil

    work = tmp_path / "work"
    work.mkdir()
    _shutil.copyfile(CHECK_FIX / "pass.json", work / "c.json")
    env, out_path = isolated_env(tmp_path)
    proc = subprocess.run(
        [str(RUN_CHECK_SH), "--result-file", "c.json",
         "--evidence-dir", "ev", "--command-exit-code", "0",
         "--command", "fixture", "--expected-id", "mncs-validation",
         "--expected-provider", "attacker-provider"],
        capture_output=True, text=True, env=env, cwd=work,
    )
    assert proc.returncode == 2
    assert read_outputs(out_path)["verdict"] == "INVALID"
