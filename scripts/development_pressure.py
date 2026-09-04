"""Turn unresolved family checks into MNCDS-shaped development evidence.

This is a transport/correlation layer.  It does not choose a solution, close
an obligation, or assign semantic authority to mncs-actions.  The field names
follow the current MNCDS DevelopmentPressure vocabulary so Commons, MNCDS,
Forge, and owner repositories can consume the observation without a second
issue taxonomy.
"""

from __future__ import annotations

from typing import Any

from family_protocol import PRESSURE_EVIDENCE_SCHEMA, canonical_digest


def _category(check: dict[str, Any]) -> str:
    value = f"{check.get('id', '')} {check.get('provider', '')}".lower()
    if "rights" in value or "provenance" in value:
        return "rights/provenance"
    if "language" in value or "compiler" in value:
        return "language/compiler capability"
    if "forge" in value or "assurance" in value:
        return "assurance"
    if "changeset" in value or "coordination" in value:
        return "coordination/change-set"
    if "mncds" in value or "development" in value:
        return "development-record"
    return "contract"


def _surfaces(category: str) -> list[str]:
    if category == "language/compiler capability":
        return ["language", "compiler", "tooling"]
    if category == "rights/provenance":
        return ["rights", "provenance", "contract"]
    if category == "assurance":
        return ["runtime", "process", "assurance"]
    if category == "coordination/change-set":
        return ["process", "tooling", "contract"]
    if category == "development-record":
        return ["process", "contract"]
    return ["contract", "tooling"]


def _references(check: dict[str, Any]) -> list[dict[str, Any]]:
    references = check.get("references", [])
    return [item for item in references if isinstance(item, dict)] if isinstance(references, list) else []


def _previous_index(previous: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not previous:
        return {}
    obligations = previous.get("obligations", [])
    if not isinstance(obligations, list):
        return {}
    return {
        item["obligation_key"]: item
        for item in obligations
        if isinstance(item, dict) and isinstance(item.get("obligation_key"), str)
    }


def build_pressure_bundle(
    checks: list[dict[str, Any]],
    *,
    mode: str,
    contract_document: str,
    contract_digest: str,
    descriptor_document: str,
    descriptor_digest: str,
    actions_revision: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_by_key = _previous_index(previous)
    obligations: list[dict[str, Any]] = []
    for check in checks:
        if check.get("verdict") != "UNKNOWN":
            continue
        unresolved = check.get("unresolved", [])
        if not isinstance(unresolved, list) or not unresolved:
            unresolved = ["the producer did not establish PASS or FAIL"]
        category = _category(check)
        references = _references(check)
        for detail in unresolved:
            detail_text = str(detail)
            owner = str(check.get("provider", "unknown-owner"))
            key_material = {
                "check_id": check.get("id", ""),
                "owner": owner,
                "category": category,
                "claim": check.get("claim", ""),
                "limitation": detail_text,
            }
            obligation_key = "sha256:" + canonical_digest(key_material)
            prior = previous_by_key.get(obligation_key)
            source = {
                "check_id": check.get("id", ""),
                "path": check.get("path", ""),
                "digest": check.get("digest", ""),
                "producer": check.get("producer", "unknown-producer"),
                "producer_revision": check.get("producer_revision", ""),
            }
            payload: dict[str, Any] = {
                "producer": "mncs-actions",
                "owner": owner,
                "originating_project": "epi13/mncs-actions",
                "source_revision": actions_revision,
                "contract_revision": check.get("contract_revision", ""),
                "requested_capability": (
                    f"establish {check.get('claim') or check.get('id', 'family check')}"
                ),
                "current_limitation": detail_text,
                "affected_surfaces": _surfaces(category),
                "protected_properties": [
                    "PASS/FAIL/UNKNOWN distinction",
                    "producer identity and revision binding",
                    "owner authority remains outside mncs-actions",
                ],
                "evidence_requirements": [
                    "owner-native evidence for the claim",
                    "exact producer revision",
                    "an independently transported check-result/1 binding",
                ],
                "reproducer": {
                    "kind": "family-contract-check",
                    "mode": mode,
                    "contract_document": contract_document,
                    "contract_digest": contract_digest,
                    "descriptor_document": descriptor_document,
                    "descriptor_digest": descriptor_digest,
                    "source": source,
                },
                "references": references,
                "upstream_downstream_contracts": [
                    "mncs.check-result/1",
                    "mncs-actions.family-integration-evidence/1",
                    str(check.get("contract_revision", "")),
                ],
                "status": "UNKNOWN",
                "unresolved": [detail_text],
                "obligation_key": obligation_key,
                "history": {
                    "same_obligation_appeared_previously": "YES" if prior else "NOT_OBSERVED",
                    "prior_pressure_id": prior.get("pressure_id") if prior else None,
                    "resolved_by_revision": None,
                },
            }
            payload["pressure_id"] = "sha256:" + canonical_digest(payload)
            obligations.append(payload)

    current_keys = {item["obligation_key"] for item in obligations}
    not_reproduced = []
    for key, prior in previous_by_key.items():
        if key not in current_keys:
            not_reproduced.append(
                {
                    "obligation_key": key,
                    "prior_pressure_id": prior.get("pressure_id"),
                    "current_status": "NOT_REPRODUCED",
                    "resolved_by_revision": None,
                    "note": "Absence from one observation is not proof of semantic resolution.",
                }
            )
    return {
        "schema_version": PRESSURE_EVIDENCE_SCHEMA,
        "protocol": "MNCDS DevelopmentPressure transport; observation only",
        "mode": mode,
        "source_revision": actions_revision,
        "contract_document": contract_document,
        "contract_digest": contract_digest,
        "descriptor_document": descriptor_document,
        "descriptor_digest": descriptor_digest,
        "obligations": obligations,
        "not_reproduced": not_reproduced,
        "authority": {
            "pressure_semantics": "MNCDS",
            "rights_semantics": "mncs-rights-provenance",
            "coordination_exchange": "MNCS-Commons",
            "language_capability": "mncs-language",
            "assurance_semantics": "mncs-forge-mcp",
            "transport": "mncs-actions",
        },
        "promotion": "observation only; no pressure observation authorizes a change",
    }
