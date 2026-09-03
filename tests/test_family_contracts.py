"""Deterministic hosted canaries for the current MNCS-family contract set."""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONTRACTS = REPO / "family-contracts.json"


def test_fixed_family_checkouts_and_contract_artifacts_are_present():
    if os.environ.get("MNCS_ACTIONS_REQUIRE_FAMILY") != "1":
        pytest.skip("fixed family checkouts are only required by the hosted canary")
    document = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    assert document["schema_version"] == "mncs-actions.family-contracts/1"
    family_root = Path(os.environ["MNCS_ACTIONS_FAMILY_ROOT"])
    for entry in document["repositories"]:
        checkout = family_root / entry["checkout_path"]
        assert checkout.is_dir(), f"missing checkout for {entry['name']}: {checkout}"
        actual = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        assert actual == entry["revision"], f"{entry['name']} moved from fixed revision"
        for artifact in entry["artifacts"]:
            assert (checkout / artifact).is_file(), f"missing {entry['name']} artifact: {artifact}"


def test_commons_registry_names_current_family_contracts():
    if os.environ.get("MNCS_ACTIONS_REQUIRE_FAMILY") != "1":
        pytest.skip("fixed family checkouts are only required by the hosted canary")
    root = Path(os.environ["MNCS_ACTIONS_FAMILY_ROOT"])
    registry = json.loads(
        (root / "family/commons/compat/family-record-producers.json").read_text(
            encoding="utf-8"
        )
    )
    contracts = {item["producer"]: item for item in registry["contracts"]}
    assert contracts["mncs-language"]["recordKind"] == "CompilationStudyResult"
    assert contracts["mncs-forge"]["recordKind"] == "ConceptEvaluation"
    assert contracts["mncds"]["recordKind"] == "DevelopmentRecord"


def test_family_contract_artifacts_have_expected_transport_shapes():
    if os.environ.get("MNCS_ACTIONS_REQUIRE_FAMILY") != "1":
        pytest.skip("fixed family checkouts are only required by the hosted canary")
    root = Path(os.environ["MNCS_ACTIONS_FAMILY_ROOT"])
    mncs = json.loads((root / "family/mncs-standard/examples/minimal/manifest.json").read_text())
    rights_schema = json.loads(
        (root / "family/rights-provenance/schemas/v0.3/lineage-record.schema.json").read_text()
    )
    mncds_schema = json.loads(
        (root / "family/mncds/schemas/mncds-development-record-0.2-alpha.schema.json").read_text()
    )
    forge = json.loads(
        (root / "family/forge/examples/forge-cell/execution-record.json").read_text()
    )
    assert isinstance(mncs.get("schema_version"), str)
    assert rights_schema["properties"]["schema_version"]["const"] == "0.3.0"
    assert mncds_schema["$schema"].startswith("https://json-schema.org/")
    assert forge["record_type"] == "forge-cell-execution"
    assert forge["schema_version"] == "0.1"
