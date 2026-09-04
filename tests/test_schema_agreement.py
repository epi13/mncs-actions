"""Mechanical agreement between published schemas and lib/mncs_actions.py.

The runtime must not quietly accept what the schema rejects or reject
what the schema says is valid.  These tests pin the shared vocabulary.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]


def load_schema(name: str) -> dict:
    return json.loads((REPO / "schemas" / name).read_text(encoding="utf-8"))


def test_verification_schema_matches_lib():
    schema = load_schema("verification-result.schema.json")
    assert schema["properties"]["schema_version"]["const"] == lib.VERIFICATION_RESULT_SCHEMA_VERSION
    assert set(schema["properties"]["verdict"]["enum"]) == set(lib.VERDICTS)
    assert set(schema["required"]) == {"schema_version", "verdict", "checks"}
    check = schema["$defs"]["check"]
    assert set(check["required"]) == {"id", "verdict"}
    assert set(check["properties"]["verdict"]["enum"]) == set(lib.VERDICTS)
    # Forward compatibility: extra fields permitted on both levels.
    assert schema["additionalProperties"] is True
    assert check["additionalProperties"] is True


def test_evidence_manifest_schema_additive():
    schema = load_schema("evidence-manifest.schema.json")
    assert schema["properties"]["schema_version"]["const"] == lib.EVIDENCE_MANIFEST_SCHEMA_VERSION
    for field in ("schema_version", "kind", "verdict", "result", "provenance", "command_exit_code", "generated_at"):
        assert field in schema["required"], field
    # v1 clarification: kind permits the family of claim kinds.
    assert set(schema["properties"]["kind"]["enum"]) == {"verification", "check", "aggregation"}
    assert set(schema["properties"]["verdict"]["enum"]) == set(lib.VERDICTS)
    # New optional plumbing must not become required.
    for field in ("receipt", "references", "claim_status", "unresolved", "boundary"):
        assert field in schema["properties"]
        assert field not in schema["required"]


def test_receipt_schema_matches_lib():
    schema = load_schema("execution-receipt.schema.json")
    assert schema["properties"]["schema_version"]["const"] == lib.EXECUTION_RECEIPT_SCHEMA_VERSION
    assert schema["properties"]["kind"]["const"] == "execution-receipt"
    assert set(schema["properties"]["claim_status"]["enum"]) == {"ESTABLISHED", "NOT_ESTABLISHED"}
    assert "command_exit_code" in schema["required"]
    assert "claim_status" in schema["required"]


def test_check_schema_matches_lib():
    schema = load_schema("check-result.schema.json")
    assert schema["properties"]["schema_version"]["const"] == lib.CHECK_RESULT_SCHEMA_VERSION
    assert set(schema["required"]) == {"schema_version", "id", "provider", "verdict"}
    assert set(schema["properties"]["verdict"]["enum"]) == set(lib.VERDICTS)
    ref = schema["$defs"]["evidenceReference"]
    assert ref["required"] == ["kind"]
    # Hardened bindings: digest format and safe-path patterns declared.
    assert "pattern" in schema["properties"]["digest"]
    assert "pattern" in ref["properties"]["digest"]
    assert "pattern" in ref["properties"]["path"]


def test_aggregate_schema_matches_lib():
    schema = load_schema("aggregate-result.schema.json")
    assert schema["properties"]["schema_version"]["const"] == lib.AGGREGATE_RESULT_SCHEMA_VERSION
    assert set(schema["required"]) == {"schema_version", "verdict", "required", "checks"}
    assert set(schema["properties"]["verdict"]["enum"]) == set(lib.VERDICTS)
    entry = schema["properties"]["checks"]["items"]
    assert set(entry["required"]) == {"id", "verdict"}
    # Hardened but optional bindings: digest/path declared, never required.
    for field in ("digest", "path", "provider", "scope"):
        assert field in entry["properties"], field
        assert field not in entry["required"]
    assert "pattern" in entry["properties"]["digest"]
    assert "pattern" in entry["properties"]["path"]
    # Forward compatible: extra component fields permitted.
    assert entry["additionalProperties"] is True


def test_family_candidate_schema_is_explicit_and_forward_compatible():
    schema = load_schema("family-contract-candidate.schema.json")
    assert schema["properties"]["schema_version"]["const"] == "mncs-actions.family-contract-candidate/1"
    assert schema["properties"]["source"]["const"] == "moving-head"
    assert set(schema["required"]) == {
        "schema_version",
        "source",
        "base_schema_version",
        "base_contract_digest",
        "branch",
        "repositories",
    }
    assert schema["additionalProperties"] is True


def test_lib_validates_published_fixtures():
    # Every shipping example fixture for the happy path must validate.
    for name in ("pass.json", "extra-fields.json"):
        doc = json.loads((REPO / "tests" / "fixtures" / "verify" / name).read_text(encoding="utf-8"))
        assert lib.validate_verification_result(doc) == [], name
    for name in ("pass.json", "fail.json", "unknown.json", "extra-fields.json"):
        doc = json.loads((REPO / "tests" / "fixtures" / "check" / name).read_text(encoding="utf-8"))
        assert lib.validate_check_result(doc) == [], name


def test_digest_output_naming():
    # Correct name is manifest-digest; provenance-digest is a compat alias.
    assert lib.MANIFEST_DIGEST_OUTPUT == "manifest-digest"
    assert lib.PROVENANCE_DIGEST_OUTPUT_COMPAT == "provenance-digest"


def test_badge_schema_matches_lib():
    schema = load_schema("badge.schema.json")
    assert schema["properties"]["schema_version"]["const"] == lib.BADGE_SCHEMA_VERSION
    assert set(schema["properties"]["verdict"]["enum"]) == set(lib.BADGE_STATES)
    assert set(schema["required"]) == {"schema_version", "label", "verdict"}
    assert schema["properties"]["label"]["maxLength"] == lib.BADGE_LABEL_MAX_LEN
    assert schema["additionalProperties"] is True


def test_family_protocol_schemas_cover_runtime_contracts():
    descriptor = load_schema("family-producer-descriptor.schema.json")
    assert descriptor["properties"]["schema_version"]["const"] == "mncs-actions.family-producer-descriptors/2"
    assert descriptor["additionalProperties"] is False
    producer = load_schema("family-producer-output.schema.json")
    assert producer["properties"]["schema_version"]["const"] == "mncs-actions.family-producer-output/2"
    assert set(producer["required"]) >= {"producer", "revision", "descriptor_digest", "contract_digest", "files", "check_results"}
    integration = load_schema("family-integration-evidence.schema.json")
    assert integration["properties"]["schema_version"]["const"] == "mncs-actions.family-integration-evidence/2"
    assert set(integration["required"]) >= {
        "mode", "contract_document", "contract_digest", "family_revisions",
        "checks", "unresolved_obligations", "authority", "promotion",
        "execution", "development_pressure", "provenance_bindings", "observation",
    }
    pressure = load_schema("development-pressure-evidence.schema.json")
    assert pressure["properties"]["schema_version"]["const"] == "mncs-actions.development-pressure-evidence/2"
    assert set(pressure["$defs"]["obligation"]["required"]) >= {
        "pressure_id", "obligation_key", "owner", "current_limitation", "evidence_provider",
        "semantic_authority", "remediation_owner", "transport_authority", "originating_project",
        "category", "reproducer", "history", "lifecycle",
    }


def test_family_protocol_fixtures_validate_against_published_schemas():
    jsonschema = pytest.importorskip("jsonschema")
    for fixture, schema_name in (
        ("producer-output.json", "family-producer-output.schema.json"),
        ("integration-evidence.json", "family-integration-evidence.schema.json"),
        ("development-pressure-evidence.json", "development-pressure-evidence.schema.json"),
    ):
        value = json.loads((REPO / "tests/fixtures/family" / fixture).read_text(encoding="utf-8"))
        jsonschema.validate(value, load_schema(schema_name))
