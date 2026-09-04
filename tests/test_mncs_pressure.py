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
MNCS_BIN = Path(
    os.environ.get(
        "MNCS_BIN", "/home/epi13/Documents/Projects/mncs-language/target/debug/mncs"
    )
)

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
        arm = re.search(rf"RightsOutcome\.{outcome}\s*=>\s*Status\.{verdict}\b", text)
        assert arm, f"MNCS projection missing {outcome} => {verdict}"
    # Binding discipline: PASS + failure -> FAIL must be explicit.
    assert re.search(r"Truth\.No\s*=>\s*Status\.FAIL", text), (
        "binding-failure downgrade arm missing"
    )
    # No arm may turn UNKNOWN into PASS or delete FAIL.
    assert "UNKNOWN => Status.PASS" not in text
    assert re.search(r"Status\.FAIL\s*=>\s*Status\.FAIL", text), (
        "FAIL carriage arm missing"
    )
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
        capture_output=True,
        text=True,
    )
    document = json.loads(proc.stdout)
    diags = [
        d for d in document.get("diagnostics", []) if d.get("stage") != "elaboration"
    ]
    return document, diags


def test_pressure_sources_lex_and_parse():
    if not MNCS_BIN.is_file():
        import pytest

        pytest.skip("mncs compiler binary unavailable (set MNCS_BIN)")
    for name in (
        "family-boundary.mncs",
        "rights-projection.mncs",
        "changeset-boundary.mncs",
        "promotion-boundary.mncs",
        "family-graph-coherence.mncs",
        "family-acceptance.mncs",
    ):
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
    # Authority and revision standing mirror the evaluator's eligibility
    # gates: unestablished authority and omitted/mismatched revisions stay
    # UNKNOWN, never PASS; substitution and malformed carriers have no arm
    # because the host establishes no claim for them.
    assert "AuthorityBinding.Unbound => Status.UNKNOWN" in text
    assert "RevisionStanding.Missing => Status.UNKNOWN" in text
    assert "RevisionStanding.Mismatched => Status.UNKNOWN" in text
    assert "fn eligible" in text


def test_graph_coherence_arms_match_host():
    text = (PRESSURE / "family-graph-coherence.mncs").read_text(encoding="utf-8")
    capability = json.loads(
        (REPO / "family-capability.json").read_text(encoding="utf-8")
    )
    lattice = capability["impacts"]
    # Every host impact class has exactly the MNCS arm the lattice demands.
    for impact, arm_name, advancement in (
        ("Executable", "Required", "REQUIRED"),
        ("Contract", "Required", "REQUIRED"),
        ("Evidence", "Optional", "OPTIONAL"),
        ("Docs", "NotRequired", "NOT_REQUIRED"),
    ):
        arm = re.search(rf"Impact\.{impact}\s*=>\s*Advancement\.{arm_name}\b", text)
        assert arm, f"MNCS classification missing {impact} => {arm_name}"
        assert lattice[impact.lower()] == advancement, impact
    assert "Impact.NoImpact => Advancement.NotRequired" in text
    assert "Impact.Unmapped => Advancement.Unknown" in text
    # Satisfaction: only Current/Optional/NotRequired satisfy; Required and
    # Unknown block. No arm may map Unknown to satisfied.
    for satisfied in ("Current", "Optional", "NotRequired"):
        assert re.search(rf"Advancement\.{satisfied}\s*=>\s*Truth\.Yes", text), (
            satisfied
        )
    for blocking in ("Required", "Unknown"):
        assert re.search(rf"Advancement\.{blocking}\s*=>\s*Truth\.No", text), blocking
    assert "Unknown => Truth.Yes" not in text
    # Acceptance: only Related + PASS accepts; every other state refuses.
    assert "GraphState.Related => match gate.promotion" in text
    assert "Status.PASS => Truth.Yes" in text
    assert text.count("Truth.No") >= 8
    # Cycle classification: sole self-authority is unsafe, anything else safe.
    assert "Truth.Yes => match cycle.independent_present" in text
    assert "Truth.No => Truth.Yes" in text


def test_acceptance_arms_match_host():
    text = (PRESSURE / "family-acceptance.mncs").read_text(encoding="utf-8")
    # Tier projection: only required-complete without open required
    # obligations and all-optional-PASS projects Full; anything else is
    # Core; incomplete required evidence is Refused (never accepted).
    assert "Truth.No => Tier.Refused" in text
    assert "Truth.Yes => Tier.Core" in text
    assert "Truth.Yes => Tier.Full" in text
    assert "Truth.No => Tier.Core" in text
    assert "Tier.Full =>" not in text and "=> Tier.Pass" not in text
    # Replay gate: any single No dominates to No (dominance discipline).
    assert text.count("Truth.No => Truth.No") >= 5
    # Chain link: all three equalities required.
    assert "link.contracts_match" in text
    # The host mirror implements the same table; pin all eight tier inputs.
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import family_graph

        tier = family_graph.acceptance_tier
    finally:
        sys.path.remove(str(REPO / "scripts"))
    assert (
        tier(
            verdict="PASS",
            required_total=3,
            required_passed=3,
            optional_verdicts={"o": "PASS"},
            optional_expected=["o"],
            open_required_obligations=[],
        )
        == "full"
    )
    assert (
        tier(
            verdict="PASS",
            required_total=3,
            required_passed=3,
            optional_verdicts={"o": "UNKNOWN"},
            optional_expected=["o"],
            open_required_obligations=[],
        )
        == "core"
    )
    assert (
        tier(
            verdict="PASS",
            required_total=3,
            required_passed=3,
            optional_verdicts={},
            optional_expected=["o"],
            open_required_obligations=[],
        )
        == "core"
    )
    assert (
        tier(
            verdict="PASS",
            required_total=3,
            required_passed=3,
            optional_verdicts={"o": "PASS"},
            optional_expected=["o"],
            open_required_obligations=["pressure.x.required"],
        )
        == "core"
    )
    assert (
        tier(
            verdict="PASS",
            required_total=3,
            required_passed=2,
            optional_verdicts={},
            optional_expected=[],
            open_required_obligations=[],
        )
        == "refused"
    )
    assert (
        tier(
            verdict="FAIL",
            required_total=3,
            required_passed=3,
            optional_verdicts={},
            optional_expected=[],
            open_required_obligations=[],
        )
        == "refused"
    )
    assert (
        tier(
            verdict="PASS",
            required_total=0,
            required_passed=0,
            optional_verdicts={},
            optional_expected=[],
            open_required_obligations=[],
        )
        == "refused"
    )


def test_changeset_projection_arms_match_host():
    text = (PRESSURE / "changeset-boundary.mncs").read_text(encoding="utf-8")
    assert "CoordinationEvidence.Complete => Status.PASS" in text
    assert "CoordinationEvidence.Incomplete => Status.UNKNOWN" in text
    verdict, _, errors, _ = lib.classify_changeset_lineage(
        {"schema_version": "unsupported"}
    )
    assert verdict is None
    assert errors
