"""MNCS pressure agreement: host semantics stay pinned to MNCS expression.

`pressure/rights-projection.mncs` expresses the pure core of
`lib/mncs_actions.py::classify_rights_report`. These tests pin the two
together mechanically: every native outcome arm must exist in the MNCS
source with the same verdict the host produces, and both pressure files
must lex/parse cleanly (workspace import resolution is excluded: it needs
the mncs-language workspace, exactly like the pre-existing
`pressure/family-boundary.mncs`).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
PRESSURE = REPO / "pressure"
MNCS_BIN = Path(os.environ.get(
    "MNCS_BIN", "/home/epi13/Documents/Projects/mncs-language/target/debug/mncs"
))

# Expected pure projection per coherent report (host authority).
EXPECTED_ARMS = {
    "Pass": "PASS",
    "Blocked": "FAIL",
    "Invalid": "FAIL",
    "PassWithFindings": "UNKNOWN",
    "ReviewRequired": "UNKNOWN",
    "Unknown": "UNKNOWN",
    "Unrecognized": "UNKNOWN",
}


def test_rights_projection_arms_match_host():
    text = (PRESSURE / "rights-projection.mncs").read_text(encoding="utf-8")
    for outcome, verdict in EXPECTED_ARMS.items():
        arm = re.search(
            rf"RightsOutcome\.{outcome}\s*=>\s*Status\.{verdict}\b", text
        )
        assert arm, f"MNCS projection missing {outcome} => {verdict}"
    # Binding discipline: PASS + failure -> FAIL must be explicit.
    assert re.search(r"Truth\.No\s*=>\s*Status\.FAIL", text), \
        "binding-failure downgrade arm missing"
    # No arm may turn UNKNOWN into PASS or delete FAIL.
    assert "UNKNOWN => Status.PASS" not in text
    assert re.search(r"Status\.FAIL\s*=>\s*Status\.FAIL", text), \
        "FAIL carriage arm missing"
    # Host agrees arm by arm (classify without identity/coherence twists).
    host = {
        "pass": "PASS",
        "blocked": "FAIL",
        "invalid": "FAIL",
        "pass-with-findings": "UNKNOWN",
        "review-required": "UNKNOWN",
        "unknown": "UNKNOWN",
    }
    for outcome, verdict in host.items():
        got, _, error = lib.classify_rights_report({"outcome": outcome})
        assert error is None
        assert got == verdict, outcome
    got, unresolved, error = lib.classify_rights_report({"outcome": "future-x"})
    assert error is None and got == "UNKNOWN" and unresolved


def test_coherence_gate_arms_present():
    text = (PRESSURE / "rights-projection.mncs").read_text(encoding="utf-8")
    # pass-for-invalid-structure and invalid-for-valid-structure are false.
    assert text.count("=> false") >= 2
    assert text.count("=> true") >= 2


def _study_stages(path: Path):
    proc = subprocess.run(
        [str(MNCS_BIN), "source-study", str(path)],
        capture_output=True, text=True,
    )
    document = json.loads(proc.stdout)
    diags = [d for d in document.get("diagnostics", []) if d.get("stage") != "elaboration"]
    return document, diags


def test_pressure_sources_lex_and_parse():
    if not MNCS_BIN.is_file():
        import pytest

        pytest.skip("mncs compiler binary unavailable (set MNCS_BIN)")
    for name in ("family-boundary.mncs", "rights-projection.mncs", "changeset-boundary.mncs", "promotion-boundary.mncs"):
        document, diags = _study_stages(PRESSURE / name)
        assert diags == [], (name, diags)
        assert document.get("lexical"), name
        assert document.get("cst"), name
        assert document.get("ast"), name


def test_promotion_boundary_arms_match_evaluator():
    text = (PRESSURE / "promotion-boundary.mncs").read_text(encoding="utf-8")
    # Required combination uses the authoritative lattice join.
    assert "dominate(pair.first, pair.second)" in text
    # Open required obligations force UNKNOWN; optional evidence is carried.
    assert "Truth.Yes => Status.UNKNOWN" in text
    assert "return state.boundary;" in text
    # No arm may turn UNKNOWN into PASS or delete FAIL. Rejected
    # obligations arrive as FAIL evidence through required_boundary
    # (dominate), never as obligations, so FAIL dominance needs no
    # separate arm here.
    assert "UNKNOWN => Status.PASS" not in text
    assert "FAIL => Status.PASS" not in text
    assert "dominate" in text


def test_changeset_projection_arms_match_host():
    text = (PRESSURE / "changeset-boundary.mncs").read_text(encoding="utf-8")
    assert "CoordinationEvidence.Complete => Status.PASS" in text
    assert "CoordinationEvidence.Incomplete => Status.UNKNOWN" in text
    verdict, _, errors, _ = lib.classify_changeset_lineage(
        {"schema_version": "unsupported"}
    )
    assert verdict is None
    assert errors
