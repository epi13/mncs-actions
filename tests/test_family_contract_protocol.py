"""Moving-head observation and explicit family advancement protocol tests."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import family_contracts as contracts

REPO = Path(__file__).resolve().parents[1]
FIXED_PATH = REPO / "family-contracts.json"


def fixed_document() -> dict:
    return json.loads(FIXED_PATH.read_text(encoding="utf-8"))


def test_moving_candidate_is_exact_and_does_not_mutate_fixed(monkeypatch):
    fixed = fixed_document()
    before = FIXED_PATH.read_bytes()
    revisions = {
        entry["repository"]: f"{index + 1:040x}"
        for index, entry in enumerate(fixed["repositories"])
    }
    monkeypatch.setattr(contracts, "resolve_remote_head", lambda slug, branch: revisions[slug])

    first = contracts.propose(fixed, "main")
    second = contracts.propose(fixed, "main")

    assert first == second
    assert first["schema_version"] == contracts.CANDIDATE_SCHEMA
    assert first["source"] == "moving-head"
    assert all(item["candidate_revision"] == revisions[item["repository"]] for item in first["repositories"])
    with pytest.raises(contracts.ContractError):
        contracts.validate_fixed(first)
    assert FIXED_PATH.read_bytes() == before


def test_malformed_resolved_revision_is_rejected(monkeypatch):
    fixed = fixed_document()
    monkeypatch.setattr(contracts, "resolve_remote_head", lambda slug, branch: "not-a-sha")
    with pytest.raises(contracts.ContractError, match="candidate_revision"):
        contracts.propose(fixed, "main")


def test_partial_or_metadata_changed_candidate_is_rejected(monkeypatch):
    fixed = fixed_document()
    monkeypatch.setattr(
        contracts,
        "resolve_remote_head",
        lambda slug, branch: "a" * 40,
    )
    candidate = contracts.propose(fixed, "main")

    partial = copy.deepcopy(candidate)
    partial["repositories"].pop()
    with pytest.raises(contracts.ContractError, match="missing a fixed family repository"):
        contracts.validate_against_fixed(partial, fixed)

    changed = copy.deepcopy(candidate)
    changed["repositories"][0]["artifacts"] = ["missing.json"]
    with pytest.raises(contracts.ContractError, match="changed fixed artifacts"):
        contracts.validate_against_fixed(changed, fixed)


def test_promotion_writes_only_a_separate_proposed_file(tmp_path, monkeypatch):
    fixed = fixed_document()
    monkeypatch.setattr(contracts, "resolve_remote_head", lambda slug, branch: "b" * 40)
    candidate = contracts.propose(fixed, "main")
    candidate_path = tmp_path / "candidate.json"
    fixed_path = tmp_path / "family-contracts.json"
    output_path = tmp_path / "family-contracts.next.json"
    contracts.write_document(candidate_path, candidate)
    contracts.write_document(fixed_path, fixed)

    assert contracts.main(
        [
            "promote",
            "--candidate",
            str(candidate_path),
            "--fixed",
            str(fixed_path),
            "--output",
            str(output_path),
        ]
    ) == 0
    proposed = json.loads(output_path.read_text(encoding="utf-8"))
    assert proposed["schema_version"] == contracts.FIXED_SCHEMA
    assert all(item["revision"] == "b" * 40 for item in proposed["repositories"])
    assert json.loads(fixed_path.read_text(encoding="utf-8")) == fixed
    assert contracts.main(
        [
            "promote",
            "--candidate",
            str(candidate_path),
            "--fixed",
            str(fixed_path),
            "--output",
            str(fixed_path),
        ]
    ) == 2


def test_checkout_validation_rejects_missing_artifact(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "test"], check=True)
    (source / "contract.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "contract.json"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    checkout_root = tmp_path / "checkouts"
    checkout = checkout_root / "family/test"
    checkout.parent.mkdir(parents=True)
    checkout.symlink_to(source, target_is_directory=True)
    document = {
        "schema_version": contracts.FIXED_SCHEMA,
        "repositories": [
            {
                "name": "test",
                "repository": "owner/test",
                "revision": revision,
                "checkout_path": "family/test",
                "artifacts": ["contract.json", "missing.json"],
            }
        ],
    }
    path = tmp_path / "fixed.json"
    contracts.write_document(path, document)
    assert contracts.main(["validate", str(path), "--checkouts-root", str(checkout_root)]) == 2
