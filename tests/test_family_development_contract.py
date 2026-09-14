"""Fixed-revision vertical MNCS development-loop contract checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "family-development-contract.json"
CHECKER = REPO / "scripts/check_family_development_contract.py"


def test_fixed_development_contract_is_exact_and_complete() -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert document["schema_version"] == "mncs-actions.family-development-contract/1"
    assert document["carrier"]["revision_binding"] == "workflow_subject_sha"
    names = {entry["name"] for entry in document["repositories"]}
    assert names == {
        "mncs-language", "mncs-test", "mncs-debug", "RAVEL", "mncs-forge-mcp",
        "MNCS-Commons", "mncs-language-service", "mncs-doctor", "mncs-atlas",
    }
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--contract", str(CONTRACT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    validation = json.loads(result.stdout)
    assert validation["verdict"] == "PASS"
    assert validation["repositories"] == 9
