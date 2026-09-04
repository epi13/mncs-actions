"""Commons Mesh family check: the mesh surface stays mechanically conformant.

Runs ``scripts/check_commons_mesh.py`` against a Commons checkout (sibling
``MNCS-Commons`` by default, ``COMMONS_CHECKOUT`` override).  A FAIL verdict
fails this test; an ERROR verdict (missing checkout) skips it so collectors
without the family layout stay green.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_commons_mesh.py"
DEFAULT_COMMONS = REPO.parent / "MNCS-Commons"
DEFAULT_MNCS_BIN = REPO.parent / "mncs-language" / "target" / "debug" / "mncs"


def _commons_root() -> Path | None:
    override = os.environ.get("COMMONS_CHECKOUT")
    candidate = Path(override) if override else DEFAULT_COMMONS
    if (candidate / "src" / "mncs_commons" / "mesh" / "__init__.py").exists():
        return candidate
    return None


def test_commons_mesh_conformance():
    commons = _commons_root()
    if commons is None:
        pytest.skip("MNCS-Commons checkout with a mesh package is absent")
    command = [sys.executable, str(SCRIPT), "--commons-root", str(commons)]
    mncs_bin = os.environ.get(
        "MNCS_BIN", str(DEFAULT_MNCS_BIN) if DEFAULT_MNCS_BIN.exists() else ""
    )
    if mncs_bin:
        command += ["--mncs-bin", mncs_bin]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=300)
    assert completed.returncode == 0, completed.stderr[-2000:]
    verdict = json.loads(completed.stdout)
    failures = [item for item in verdict["checks"] if not item["passed"]]
    assert verdict["verdict"] == "PASS", json.dumps(failures, indent=2)
    assert len(verdict["checks"]) >= 8
