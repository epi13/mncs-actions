"""Live membrane test for mncs-test -> mncs-debug -> mncs-actions evidence."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(REPO / "scripts"))
from family_roots import discover_family_repo


MNCS_TEST = discover_family_repo(
    "mncs-test", environment_name="MNCS_TEST_REPO", start=REPO
)
MNCS_DEBUG = discover_family_repo(
    "mncs-debug", environment_name="MNCS_DEBUG_REPO", start=REPO
)
MNCS_LANGUAGE = discover_family_repo(
    "mncs-language", environment_name="MNCS_LANGUAGE_REPO", start=REPO
)
MNCS = Path(os.environ.get("MNCS", MNCS_LANGUAGE / "target" / "debug" / "mncs"))
EMBED = Path(
    os.environ.get("MNCS_EMBED_LIBRARY", MNCS_LANGUAGE / "target" / "debug" / "libmncs_embed.so")
)


LIVE = all(
    (
        (MNCS_TEST / "bin" / "mncs-test").is_file(),
        (MNCS_DEBUG / "bin" / "mncs-debug").is_file(),
        MNCS.is_file(),
        EMBED.is_file(),
    )
)


def test_minimal_debug_path_delegates_escalation_to_debug_owner() -> None:
    script = (REPO / "actions" / "mncs-debug" / "test.sh").read_text(encoding="utf-8")
    assert '"$debug_bin" diagnose' in script
    assert "--evidence-operation" not in script


@pytest.mark.skipif(not LIVE, reason="sibling MNCS test/debug providers and runtime are required")
def test_failing_test_produces_debug_evidence_with_lineage(tmp_path: Path) -> None:
    test_result = tmp_path / "test-result.json"
    test_check = tmp_path / "test-check.json"
    test_artifacts = tmp_path / "test-artifacts"
    test_env = dict(os.environ)
    test_env.update(
        {
            "MNCS_TEST_BIN": str(MNCS_TEST / "bin" / "mncs-test"),
            "MNCS_BIN": str(MNCS),
            "MNCS_SOURCE_FILE": str(
                MNCS_TEST / "tests" / "fixtures" / "first_class_failing.mncs"
            ),
            "MNCS_LIBRARY_PATH_INPUT": os.pathsep.join((str(MNCS_TEST / "native"), str(MNCS_LANGUAGE / "library"))),
            "MNCS_EMBED_LIBRARY_INPUT": str(EMBED),
            "MNCS_STEP_BUDGET": "200000",
            "MNCS_RESULT_FILE": str(test_check),
            "MNCS_TEST_RESULT_FILE": str(test_result),
            "MNCS_ARTIFACTS_DIRECTORY": str(test_artifacts),
        }
    )
    test_run = subprocess.run(
        ["bash", str(REPO / "actions" / "mncs-test" / "test.sh")],
        cwd=tmp_path,
        env=test_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert test_run.returncode == 1, test_run.stderr + test_run.stdout

    debug_check = tmp_path / "debug-check.json"
    witness = tmp_path / "debug-witness.json"
    debug_artifacts = tmp_path / "debug-artifacts"
    debug_env = dict(os.environ)
    debug_env.update(
        {
            "MNCS_DEBUG_BIN": str(MNCS_DEBUG / "bin" / "mncs-debug"),
            "MNCS_BIN": str(MNCS),
            "MNCS_LIBRARY_PATH_INPUT": os.pathsep.join((str(MNCS_TEST / "native"), str(MNCS_LANGUAGE / "library"))),
            "MNCS_DEBUG_TEST_RESULT_FILE": str(test_result),
            "MNCS_DEBUG_WORKING_DIRECTORY": str(tmp_path),
            "MNCS_DEBUG_CAPTURE_POLICY": "failure-only",
            "MNCS_DEBUG_CHECK_FILE": str(debug_check),
            "MNCS_DEBUG_WITNESS_FILE": str(witness),
            "MNCS_DEBUG_ARTIFACTS_DIRECTORY": str(debug_artifacts),
        }
    )
    debug_run = subprocess.run(
        ["bash", str(REPO / "actions" / "mncs-debug" / "test.sh")],
        cwd=tmp_path,
        env=debug_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert debug_run.returncode == 0, debug_run.stderr + debug_run.stdout
    check = json.loads(debug_check.read_text(encoding="utf-8"))
    assert check["schema_version"] == "mncs.check-result/1"
    assert check["verdict"] == "PASS"
    assert "mncs-test-result" in {item["kind"] for item in check["references"]}
    debug = check["debug"]
    assert "diagnose" in debug["diagnostic"]["operations"]
    assert "sufficiency" not in debug["diagnostic"]["operations"]
    assert debug["diagnostic"]["sufficiency"]["sufficient"] is True
    assert debug["integration"]["run_id"] == json.loads(test_result.read_text(encoding="utf-8"))["run_id"]
    assert debug["integration"]["test_execution"]["execution_identity"]
    assert debug["witness_id"] == json.loads(witness.read_text(encoding="utf-8"))["witness_id"]
    assert debug["observation"]["schema_revision"] == "mncs.execution-observation/1"
    assert debug["observation"]["identity"]
    assert debug["source_map"]["schema_revision"] == "mncs.execution-source-map/1"

    evidence = tmp_path / "evidence"
    package = subprocess.run(
        [
            "bash",
            str(REPO / "actions" / "run-check" / "run_check.sh"),
            "--result-file",
            str(debug_check),
            "--evidence-dir",
            str(evidence),
            "--command-exit-code",
            "0",
            "--command",
            "mncs-debug bounded failure capture",
            "--expected-id",
            "mncs-debug",
            "--expected-provider",
            "mncs-debug",
        ],
        cwd=tmp_path,
        env=debug_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert package.returncode == 0, package.stderr + package.stdout
    assert json.loads((evidence / "evidence-manifest.json").read_text(encoding="utf-8"))["verdict"] == "PASS"


@pytest.mark.skipif(not LIVE, reason="sibling MNCS test/debug providers and runtime are required")
def test_failure_only_policy_does_not_trace_passing_test(tmp_path: Path) -> None:
    test_result = tmp_path / "passing-result.json"
    test_check = tmp_path / "passing-check.json"
    command = [
        "python3",
        str(MNCS_TEST / "tools" / "mncs_test.py"),
        "run",
        "--manifest",
        str(MNCS_TEST / "mncs-test.toml"),
        "--mncs",
        str(MNCS),
        "--library",
        str(MNCS_TEST / "native"),
        "--library",
        str(MNCS_LANGUAGE / "library"),
        "--embed-library",
        str(EMBED),
        "--result",
        str(test_result),
        "--check-result",
        str(test_check),
        "--artifacts",
        str(tmp_path / "test-artifacts"),
    ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    debug_check = tmp_path / "debug-check.json"
    env = dict(os.environ)
    env.update(
        {
            "MNCS_DEBUG_TEST_RESULT_FILE": str(test_result),
            "MNCS_DEBUG_CAPTURE_POLICY": "failure-only",
            "MNCS_DEBUG_CHECK_FILE": str(debug_check),
            "MNCS_DEBUG_WORKING_DIRECTORY": str(tmp_path),
        }
    )
    run = subprocess.run(
        ["bash", str(REPO / "actions" / "mncs-debug" / "test.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0
    assert json.loads(debug_check.read_text(encoding="utf-8"))["verdict"] == "UNKNOWN"


def test_corrupt_witness_never_establishes_debug_claim(tmp_path: Path) -> None:
    fake = tmp_path / "fake-debug.py"
    fake.write_text(
        """#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
def output_path():
    return Path(args[args.index('--output') + 1])
if args and args[0] == 'import-test':
    output_path().write_text(json.dumps({'schema_version': 'mncs.debug-witness/1'}))
elif args and args[0] == 'validate':
    output_path().write_text(json.dumps({'schema_version': 'mncs.debug-validation/1', 'valid': False}))
""",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    result = tmp_path / "failed-result.json"
    result.write_text(
        json.dumps({"schema_version": "mncs.test-result/1", "verdict": "FAIL"}) + "\n",
        encoding="utf-8",
    )
    check = tmp_path / "debug-check.json"
    env = dict(os.environ)
    env.update(
        {
            "MNCS_DEBUG_BIN": str(fake),
            "MNCS_DEBUG_TEST_RESULT_FILE": str(result),
            "MNCS_DEBUG_WORKING_DIRECTORY": str(tmp_path),
            "MNCS_DEBUG_CHECK_FILE": str(check),
            "MNCS_DEBUG_WITNESS_FILE": str(tmp_path / "witness.json"),
            "MNCS_DEBUG_ARTIFACTS_DIRECTORY": str(tmp_path / "artifacts"),
        }
    )
    run = subprocess.run(
        ["bash", str(REPO / "actions/mncs-debug/test.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0
    assert not check.exists()
