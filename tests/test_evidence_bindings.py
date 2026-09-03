"""Hardened aggregate evidence bindings: checks[].digest / checks[].path.

These fields are optional (never required) but strict: malformed bindings
are rejected rather than silently accepted, while unknown additive fields
stay forward-compatible. Executable validation (lib/mncs_actions.py) and
the published JSON Schemas must agree mechanically.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]

GOOD_DIGEST = "a" * 64
GOOD_SHA_DIGEST = "sha256:" + "b" * 64


def base_aggregate():
    return {
        "schema_version": lib.AGGREGATE_RESULT_SCHEMA_VERSION,
        "verdict": "PASS",
        "required": ["mncs-validation"],
        "checks": [
            {
                "id": "mncs-validation",
                "verdict": "PASS",
                "required": True,
                "provider": "mncs-validator-rs",
                "scope": "specification",
                "digest": GOOD_DIGEST,
                "path": "mncs.json",
            }
        ],
    }


def base_check():
    return {
        "schema_version": lib.CHECK_RESULT_SCHEMA_VERSION,
        "id": "rights-provenance",
        "provider": "mncs-rights-provenance",
        "verdict": "PASS",
    }


def schema(name):
    return json.loads((REPO / "schemas" / name).read_text(encoding="utf-8"))


# ---- lib validation: digest ----

def test_valid_digests_accepted():
    for digest in (GOOD_DIGEST, GOOD_SHA_DIGEST):
        doc = base_aggregate()
        doc["checks"][0]["digest"] = digest
        assert lib.validate_aggregate_result(doc) == []
        check = base_check()
        check["digest"] = digest
        assert lib.validate_check_result(check) == []


def test_malformed_digests_rejected():
    for bad in ("not-a-digest", "", "xyz", "A" * 64, "sha256:short", "a" * 63, "a" * 65, 123, None):
        doc = base_aggregate()
        doc["checks"][0]["digest"] = bad
        assert lib.validate_aggregate_result(doc) != [], bad
        check = base_check()
        check["digest"] = bad
        # None means absent for check top-level? No: explicit None is malformed.
        assert lib.validate_check_result(check) != [], bad


def test_absent_digest_stays_valid():
    doc = base_aggregate()
    del doc["checks"][0]["digest"]
    assert lib.validate_aggregate_result(doc) == []
    assert lib.validate_check_result(base_check()) == []


# ---- lib validation: path ----

def test_valid_paths_accepted():
    for path in ("a.json", "bundle/manifest.json", ".mncs/mncs-check.json"):
        doc = base_aggregate()
        doc["checks"][0]["path"] = path
        assert lib.validate_aggregate_result(doc) == [], path


def test_unsafe_paths_rejected():
    for bad in ("/absolute", "../escape", "a/../b", "a\\b", "C:\\x", "C:/x",
                "", "a//b", "trailing/", ".", "..", "a/./b", 123):
        doc = base_aggregate()
        doc["checks"][0]["path"] = bad
        assert lib.validate_aggregate_result(doc) != [], bad


def test_absent_path_stays_valid():
    doc = base_aggregate()
    del doc["checks"][0]["path"]
    assert lib.validate_aggregate_result(doc) == []


def test_non_string_provider_scope_rejected():
    doc = base_aggregate()
    doc["checks"][0]["provider"] = 123
    assert lib.validate_aggregate_result(doc) != []
    doc = base_aggregate()
    doc["checks"][0]["scope"] = ["x"]
    assert lib.validate_aggregate_result(doc) != []


def test_forward_compatible_extra_fields_still_accepted():
    doc = base_aggregate()
    doc["checks"][0]["future_binding"] = {"nested": True}
    doc["future_top_level"] = [1, 2, 3]
    assert lib.validate_aggregate_result(doc) == []


# ---- schema/lib mechanical agreement ----

def test_schema_declares_digest_and_path_bindings():
    aggregate = schema("aggregate-result.schema.json")
    props = aggregate["properties"]["checks"]["items"]["properties"]
    for field in ("digest", "path", "provider", "scope"):
        assert field in props, field
    assert "pattern" in props["digest"]
    assert "pattern" in props["path"]
    # Still additive: only id/verdict required, extra fields permitted.
    assert set(aggregate["properties"]["checks"]["items"]["required"]) == {"id", "verdict"}
    assert aggregate["properties"]["checks"]["items"]["additionalProperties"] is True
    check = schema("check-result.schema.json")
    assert "pattern" in check["properties"]["digest"]
    ref = check["$defs"]["evidenceReference"]
    assert "pattern" in ref["properties"]["digest"]
    assert "pattern" in ref["properties"]["path"]
    manifest = schema("evidence-manifest.schema.json")
    manifest_ref_props = manifest["properties"]["references"]["items"]["properties"]
    assert "pattern" in manifest_ref_props["digest"]
    assert "pattern" in manifest_ref_props["path"]


def test_schema_and_lib_agree_on_bindings():
    jsonschema = pytest.importorskip("jsonschema")
    aggregate_schema = schema("aggregate-result.schema.json")
    valid = base_aggregate()
    jsonschema.validate(valid, aggregate_schema)
    assert lib.validate_aggregate_result(valid) == []
    # Malformed digest: both reject.
    bad_digest = copy.deepcopy(valid)
    bad_digest["checks"][0]["digest"] = "not-a-digest"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_digest, aggregate_schema)
    assert lib.validate_aggregate_result(bad_digest) != []
    # Traversal path: both reject.
    bad_path = copy.deepcopy(valid)
    bad_path["checks"][0]["path"] = "../escape"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_path, aggregate_schema)
    assert lib.validate_aggregate_result(bad_path) != []
    # Absent bindings: both accept.
    absent = copy.deepcopy(valid)
    del absent["checks"][0]["digest"]
    del absent["checks"][0]["path"]
    jsonschema.validate(absent, aggregate_schema)
    assert lib.validate_aggregate_result(absent) == []
