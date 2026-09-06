"""Capability-resolution pressure agreement: MNCS stays pinned to Fabric.

`pressure/capability-resolution.mncs` expresses the pure pre-execution
eligibility core the Fabric host enforces in
`mncs_fabric/capability_resolution.py::resolve_code` (specified by
`mncs/worker_capability.mncs` in mncs-fabric). These tests pin the two
together mechanically:

- every MNCS arm must exist with the same outcome the host produces,
  over the full (liveness, freshness, provenance, env, intent) table;
- the pressure source must lex/parse cleanly through the real compiler;
- no arm may admit an ineligible worker (no stale/unverified/missing/
  denied path maps to Eligible).
"""

import json
import os
import re
import subprocess
import sys
import itertools
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRESSURE = REPO / "pressure"
MNCS_BIN = Path(
    os.environ.get(
        "MNCS_BIN", "/home/epi13/Documents/Projects/mncs-language/target/debug/mncs"
    )
)
FABRIC_SRC = REPO.parent / "mncs-fabric" / "src"

sys.path.insert(0, str(REPO / "lib"))


def _fabric():
    if not FABRIC_SRC.is_dir():
        import pytest

        pytest.skip("mncs-fabric checkout unavailable (sibling repository)")
    sys.path.insert(0, str(FABRIC_SRC))
    try:
        import mncs_fabric.capability_resolution as resolution
    finally:
        sys.path.remove(str(FABRIC_SRC))
    return resolution


def test_resolution_arms_match_host_truth_table():
    resolution = _fabric()
    text = (PRESSURE / "capability-resolution.mncs").read_text(encoding="utf-8")
    liveness = ["AVAILABLE", "UNAVAILABLE", "DISCONNECTED"]
    freshness = ["fresh", "stale"]
    expected_codes = {
        "ELIGIBLE",
        "CAPABILITY_UNSATISFIED",
        "POLICY_DENIED",
        "PROVENANCE_UNVERIFIED",
        "WORKER_STALE",
        "WORKER_UNAVAILABLE",
        "WORKER_DISCONNECTED",
    }
    seen = set()
    for live, fresh, prov, env, intent in itertools.product(
        liveness, freshness, [True, False], [True, False], [True, False]
    ):
        code = resolution.resolve_code(
            liveness=live,
            freshness=fresh,
            provenance_ok=prov,
            env_ok=env,
            intent_ok=intent,
        )
        assert code in expected_codes, code
        seen.add(code)
        # The MNCS enum spells codes in PascalCase (Eligible,
        # CapabilityUnsatisfied, ...); every host code must name one.
        pascal = "".join(part.capitalize() for part in code.split("_"))
        assert re.search(rf"Resolution\.{pascal}\b", text), f"MNCS arm missing for {code}"
    assert seen == expected_codes, f"truth table does not cover every code: {seen}"
    # Ordering: liveness decides before freshness before provenance
    # before environment before policy.
    assert text.index("Unavailable => Resolution.WorkerUnavailable") < text.index(
        "Stale => Resolution.WorkerStale"
    )
    decide = text[text.index("fn decide_fresh("):]
    assert decide.index("if provenance_ok") < decide.index("if env_ok") < decide.index(
        "if intent_ok"
    )


def test_no_arm_admits_the_ineligible():
    text = (PRESSURE / "capability-resolution.mncs").read_text(encoding="utf-8")
    eligible_fn = text[text.index("fn is_eligible("):]
    for denied in (
        "CapabilityUnsatisfied",
        "PolicyDenied",
        "ProvenanceUnverified",
        "WorkerStale",
        "WorkerUnavailable",
        "WorkerDisconnected",
    ):
        assert re.search(rf"{denied}\s*=>\s*false", eligible_fn), denied
    assert re.search(r"Eligible\s*=>\s*true", eligible_fn)


def test_provenance_and_intent_arms_match_host():
    resolution = _fabric()
    text = (PRESSURE / "capability-resolution.mncs").read_text(encoding="utf-8")
    assert resolution.provenance_trusted("worker-observed") is True
    assert resolution.provenance_trusted("operator-asserted") is True
    assert resolution.provenance_trusted("consumer-declared") is False
    assert "ConsumerDeclared => false" in text
    assert "WorkerObserved => true" in text
    assert "OperatorAsserted => true" in text
    stable = resolution.default_policy()
    assert resolution.intent_allowed(stable, "normal") is True
    assert resolution.intent_allowed(stable, "mutating") is False
    assert resolution.intent_allowed(stable, "privileged") is False
    bare = dict(stable, experimental=True)
    # Experimental status alone grants nothing.
    assert resolution.intent_allowed(bare, "mutating") is False
    assert resolution.intent_allowed(bare, "privileged") is False
    assert "Normal => true" in text
    assert "Mutating => policy.experimental && policy.allow_toolchain_install" in text
    assert "Privileged => policy.experimental && policy.allow_root_mutation" in text


def test_capability_pressure_source_lex_and_parse():
    if not MNCS_BIN.is_file():
        import pytest

        pytest.skip("mncs compiler binary unavailable (set MNCS_BIN)")
    proc = subprocess.run(
        [str(MNCS_BIN), "source-study", str(PRESSURE / "capability-resolution.mncs")],
        capture_output=True,
        text=True,
    )
    document = json.loads(proc.stdout)
    assert document.get("compilation_status") == "completed"
    assert document.get("diagnostics", []) == []
