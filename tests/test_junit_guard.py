"""Regression coverage for the required-family JUnit execution guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_junit import JUnitReportError, inspect_report  # noqa: E402


def write_report(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(body, encoding="utf-8")
    return path


def test_nested_pytest_suites_count_testcases_not_root_counters(tmp_path):
    # This is the exact failure mode from family-contract-canary.yml: pytest
    # puts the meaningful counters and cases below a testsuites root.
    report = write_report(
        tmp_path,
        """<testsuites tests=\"0\" skipped=\"0\">
          <testsuite name=\"tests/test_family_contracts.py\" tests=\"2\" skipped=\"0\">
            <testcase classname=\"family\" name=\"fixed\" />
            <testcase classname=\"family\" name=\"artifacts\" />
          </testsuite>
        </testsuites>""",
    )
    assert inspect_report(report) == {
        "suites": 1,
        "tests": 2,
        "executed": 2,
        "skipped": 0,
        "failures": 0,
        "errors": 0,
    }


def test_nested_suite_and_namespaced_xml_are_supported(tmp_path):
    report = write_report(
        tmp_path,
        """<testsuites xmlns=\"urn:junit\"><testsuite name=\"outer\">
          <testsuite name=\"inner\"><testcase name=\"one\" /></testsuite>
        </testsuite></testsuites>""",
    )
    summary = inspect_report(report)
    assert summary["suites"] == 2
    assert summary["executed"] == 1


@pytest.mark.parametrize(
    "body, message",
    [
        ("<testsuites />", "no test suites"),
        ("<testsuites><testsuite name='empty' /></testsuites>", "no testcases"),
        (
            "<testsuites><testsuite><testcase name='x'><skipped /></testcase></testsuite></testsuites>",
            "skipped",
        ),
        (
            "<testsuites><testsuite><testcase name='x'><failure /></testcase></testsuite></testsuites>",
            "failure",
        ),
        (
            "<testsuites><testsuite><testcase name='x'><error /></testcase></testsuite></testsuites>",
            "error",
        ),
        ("<not-junit />", "root"),
        ("<testsuites>", "parse"),
    ],
)
def test_empty_skipped_failure_malformed_reports_fail_closed(tmp_path, body, message):
    with pytest.raises(JUnitReportError, match=message):
        inspect_report(write_report(tmp_path, body))


def test_cli_reports_machine_readable_summary(tmp_path):
    report = write_report(
        tmp_path,
        "<testsuite tests='1'><testcase classname='x' name='ok' /></testsuite>",
    )
    process = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts/check_junit.py"), str(report)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(process.stdout)["executed"] == 1
