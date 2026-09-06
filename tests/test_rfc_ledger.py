"""RFC ledger check: claims stay tied to evidence, defects fail closed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))

from check_rfc_ledger import check_ledger


def ledger_with(criteria, **overrides):
    entry = {
        "number": "0007",
        "title": "Proof-Carrying Dependent Core",
        "design_status": "DRAFT",
        "implementation_status": "BOUNDED_IMPLEMENTATION",
        "acceptance_criteria": criteria,
    }
    entry.update(overrides)
    return {"schema_version": "0.1", "entries": [entry]}


def test_valid_ledger_passes(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "kernel.mncs").write_text("mncs 0.10;\n", encoding="utf-8")
    ledger = ledger_with(
        [
            {"id": "C1", "text": "kernel", "state": "satisfied", "evidence": ["kernel.mncs"]},
            {"id": "C2", "text": "erasure", "state": "unsatisfied", "evidence": []},
        ]
    )
    assert check_ledger(ledger, root) == []


def test_satisfied_claim_without_evidence_fails(tmp_path):
    ledger = ledger_with(
        [{"id": "C1", "text": "kernel", "state": "satisfied", "evidence": []}]
    )
    errors = check_ledger(ledger, tmp_path)
    assert any("carries no evidence" in error for error in errors)


def test_missing_evidence_path_fails(tmp_path):
    ledger = ledger_with(
        [{"id": "C1", "text": "kernel", "state": "partial", "evidence": ["absent.mncs"]}]
    )
    errors = check_ledger(ledger, tmp_path)
    assert any("missing evidence" in error for error in errors)


def test_unsatisfied_claim_with_evidence_fails(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "kernel.mncs").write_text("x", encoding="utf-8")
    ledger = ledger_with(
        [{"id": "C1", "text": "kernel", "state": "unsatisfied", "evidence": ["kernel.mncs"]}]
    )
    errors = check_ledger(ledger, root)
    assert any("must carry no evidence" in error for error in errors)


def test_excused_gap_reference_is_allowed(tmp_path):
    ledger = ledger_with(
        [
            {
                "id": "C1",
                "text": "erasure",
                "state": "unsatisfied",
                "evidence": ["0007-G6"],
                "excused": True,
            }
        ]
    )
    assert check_ledger(ledger, tmp_path) == []


def test_unknown_state_and_unordered_entries_fail(tmp_path):
    ledger = {
        "schema_version": "0.1",
        "entries": [
            {
                "number": "0007",
                "design_status": "DRAFT",
                "implementation_status": "BOUNDED_IMPLEMENTATION",
                "acceptance_criteria": [
                    {"id": "C1", "text": "x", "state": "claimed", "evidence": []}
                ],
            },
            {
                "number": "0002",
                "design_status": "NOPE",
                "implementation_status": "PARTIAL",
                "acceptance_criteria": [
                    {"id": "C1", "text": "x", "state": "partial", "evidence": ["f"]}
                ],
            },
        ],
    }
    errors = check_ledger(ledger, tmp_path)
    joined = "\n".join(errors)
    assert "ascending numeric order" in joined
    assert "outside the vocabulary" in joined


def test_cli_validates_the_real_mncs_language_ledger():
    language = Path("/home/epi13/Documents/Projects/mncs-language")
    ledger_path = language / "rfcs" / "conformance-ledger.json"
    if not ledger_path.is_file():
        return
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_rfc_ledger.py"),
         "--ledger", str(ledger_path), "--root", str(language)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout
