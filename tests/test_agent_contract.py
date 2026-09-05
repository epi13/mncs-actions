"""The agent execution contract is bound to real machinery.

`AGENTS.md` names scripts, schemas, and directories. This test pins those
references mechanically: every referenced path must exist, and the authority
table in the contract must match the `authority` consts in
`schemas/development-pressure-evidence.schema.json`. Contract drift fails
loudly here instead of silently misleading the next agent.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "AGENTS.md"
SCHEMA = REPO / "schemas" / "development-pressure-evidence.schema.json"

EXPECTED_AUTHORITIES = {
    "pressure semantics": "MNCDS",
    "rights semantics": "mncs-rights-provenance",
    "coordination": "MNCS-Commons",
    "language capability": "mncs-language",
    "assurance semantics": "mncs-forge-mcp",
    "transport": "mncs-actions",
}


def contract_text() -> str:
    assert CONTRACT.is_file(), "AGENTS.md (agent execution contract) is missing"
    return CONTRACT.read_text(encoding="utf-8")


def test_contract_referenced_scripts_exist():
    text = contract_text()
    scripts = set(re.findall(r"scripts/[A-Za-z0-9_]+\.py", text))
    assert scripts, "contract must bind at least one scripts/*.py tool"
    for script in sorted(scripts):
        assert (REPO / script).is_file(), f"contract names missing {script}"


def test_contract_referenced_schemas_exist():
    text = contract_text()
    schemas = set(re.findall(r"schemas/[A-Za-z0-9_.-]+\.json", text))
    assert schemas, "contract must bind at least one schemas/*.json document"
    for schema in sorted(schemas):
        assert (REPO / schema).is_file(), f"contract names missing {schema}"


def test_contract_referenced_directories_exist():
    text = contract_text()
    for dirname in ("pressure/", "docs/"):
        assert dirname in text, f"contract must mention {dirname}"
        assert (REPO / dirname).is_dir(), f"contract names missing {dirname}"


def test_contract_authority_table_matches_schema():
    text = contract_text()
    authorities = json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"][
        "authority"
    ]["properties"]
    schema_values = {key: spec["const"] for key, spec in authorities.items()}
    assert schema_values == {
        "pressure_semantics": "MNCDS",
        "rights_semantics": "mncs-rights-provenance",
        "coordination_exchange": "MNCS-Commons",
        "language_capability": "mncs-language",
        "assurance_semantics": "mncs-forge-mcp",
        "transport": "mncs-actions",
    }, "schema authority consts changed; update contract and test together"
    for surface, owner in EXPECTED_AUTHORITIES.items():
        assert surface in text, f"contract authority table lost {surface!r}"
        assert owner in text, f"contract authority table lost {owner!r}"


def test_contract_states_pressure_routing_order():
    text = contract_text()
    emit = text.index("development_pressure.py")
    project = text.index("pressure_to_obligations.py")
    assert emit < project, "evidence must be emitted before it is projected"
    assert "mncs_project_check.py" in text, "contract must name the suite runner"
    assert (REPO / "scripts" / "mncs_project_check.py").is_file()
