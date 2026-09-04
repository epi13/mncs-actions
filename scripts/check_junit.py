#!/usr/bin/env python3
"""Fail closed when a required JUnit test report is empty, skipped, or bad.

Pytest commonly emits a ``testsuites`` root whose counters live on nested
``testsuite`` elements.  This guard counts testcase elements instead of
trusting a particular producer's summary attributes.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class JUnitReportError(ValueError):
    """The report cannot establish execution of required tests."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def inspect_report(path: Path) -> dict[str, Any]:
    try:
        source = path.read_text(encoding="utf-8")
        if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
            raise JUnitReportError("JUnit report must not contain DTD or entity declarations")
        root = ET.parse(path).getroot()
    except JUnitReportError:
        raise
    except (OSError, UnicodeError, ET.ParseError) as exc:
        raise JUnitReportError(f"cannot parse JUnit report {path}: {exc}") from exc

    # DTD/entity declarations are not part of the report contract.  Rejecting
    # them keeps this small XML reader from becoming an entity expansion or
    # external-reference surface.
    if _local_name(root.tag) not in {"testsuites", "testsuite"}:
        raise JUnitReportError("JUnit root must be <testsuites> or <testsuite>")

    suites = [element for element in root.iter() if _local_name(element.tag) == "testsuite"]
    cases = [element for element in root.iter() if _local_name(element.tag) == "testcase"]
    if not suites:
        raise JUnitReportError("JUnit report contains no test suites")
    if not cases:
        raise JUnitReportError("JUnit report contains no testcases")

    skipped = 0
    failures = 0
    errors = 0
    for case in cases:
        children = {_local_name(child.tag) for child in case}
        skipped += int("skipped" in children)
        failures += int("failure" in children)
        errors += int("error" in children)

    executed = len(cases) - skipped
    result = {
        "suites": len(suites),
        "tests": len(cases),
        "executed": executed,
        "skipped": skipped,
        "failures": failures,
        "errors": errors,
    }
    if skipped:
        raise JUnitReportError(f"{skipped} required family canary test(s) skipped")
    if executed <= 0:
        raise JUnitReportError("JUnit report executed zero required tests")
    if failures or errors:
        raise JUnitReportError(
            f"JUnit report contains {failures} failure(s) and {errors} error(s)"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        summary = inspect_report(args.report)
    except JUnitReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
