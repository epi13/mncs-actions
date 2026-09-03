"""Mechanical agreement between published schemas and lib/mncs_actions.py.

The runtime must not quietly accept what the schema rejects or reject
what the schema says is valid.  These tests pin the shared vocabulary.
"""
import json
import sys
from pathlib import Path

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


def test_aggregate_schema_matches_lib():
    schema = load_schema("aggregate-result.schema.json")
    assert schema["properties"]["schema_version"]["const"] == lib.AGGREGATE_RESULT_SCHEMA_VERSION
    assert set(schema["required"]) == {"schema_version", "verdict", "required", "checks"}
    assert set(schema["properties"]["verdict"]["enum"]) == set(lib.VERDICTS)


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
