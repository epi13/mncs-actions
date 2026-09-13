"""Cross-repository smoke test for the mncs-test action transport.

The test is skipped in the standalone mncs-actions checkout used by GitHub
CI unless a sibling mncs-test checkout and built mncs binary are supplied.
When available, it exercises the same provider script and run-check packaging
path used by the composite action.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MNCS_TEST = Path(os.environ.get("MNCS_TEST_REPO", REPO.parent / "mncs-test"))
MNCS_LANGUAGE = Path(os.environ.get("MNCS_LANGUAGE_REPO", REPO.parent / "mncs-language"))
MNCS = Path(os.environ.get("MNCS", MNCS_LANGUAGE / "target" / "debug" / "mncs"))
EMBED_LIBRARY = Path(
    os.environ.get(
        "MNCS_EMBED_LIBRARY",
        MNCS_LANGUAGE / "target" / "debug" / "libmncs_embed.so",
    )
)


@pytest.mark.skipif(
    not (MNCS_TEST / "bin" / "mncs-test").is_file()
    or not MNCS.is_file()
    or not EMBED_LIBRARY.is_file(),
    reason="sibling mncs-test checkout and built compiler/embed library are required",
)
def test_native_provider_and_run_check_packaging(tmp_path: Path):
    result = tmp_path / "mncs-test-check.json"
    test_result = tmp_path / "mncs-test-result.json"
    artifacts = tmp_path / "mncs-test-artifacts"
    evidence = tmp_path / "mncs-test-evidence"
    output = tmp_path / "github-output"
    summary = tmp_path / "github-summary"
    output.touch()
    summary.touch()
    environment = dict(os.environ)
    environment.update(
        {
            "MNCS_TEST_BIN": str(MNCS_TEST / "bin" / "mncs-test"),
            "MNCS_BIN": str(MNCS),
            "MNCS_MANIFEST": str(MNCS_TEST / "mncs-test.toml"),
            "MNCS_LIBRARY_PATH_INPUT": os.pathsep.join(
                (str(MNCS_TEST / "native"), str(MNCS_LANGUAGE / "library"))
            ),
            "MNCS_EMBED_LIBRARY_INPUT": str(EMBED_LIBRARY),
            "MNCS_RESULT_FILE": str(result),
            "MNCS_TEST_RESULT_FILE": str(test_result),
            "MNCS_ARTIFACTS_DIRECTORY": str(artifacts),
            "MNCS_TIMEOUT_SECONDS": "60",
            "MNCS_STEP_BUDGET": "200000",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
    )
    provider = subprocess.run(
        ["bash", str(REPO / "actions" / "mncs-test" / "test.sh")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert provider.returncode == 0, provider.stderr + provider.stdout
    package = subprocess.run(
        [
            "bash",
            str(REPO / "actions" / "run-check" / "run_check.sh"),
            "--result-file",
            str(result),
            "--evidence-dir",
            str(evidence),
            "--command-exit-code",
            str(provider.returncode),
            "--command",
            "mncs-test run --manifest mncs-test.toml",
            "--expected-id",
            "mncs-test",
            "--expected-provider",
            "mncs-test",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert package.returncode == 0, package.stderr + package.stdout
    assert json.loads(result.read_text(encoding="utf-8"))["verdict"] == "PASS"
    detailed = json.loads(test_result.read_text(encoding="utf-8"))
    assert detailed["summary"]["total"] == 6
    assert detailed["execution"]["mode"] == "retained-embed-batch"
    assert json.loads((evidence / "evidence-manifest.json").read_text(encoding="utf-8"))["verdict"] == "PASS"
    assert "verdict=PASS" in output.read_text(encoding="utf-8")
