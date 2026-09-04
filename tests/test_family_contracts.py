"""Deterministic hosted canaries for the current MNCS-family contract set."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONTRACTS = REPO / "family-contracts.json"

sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "scripts"))

import mncs_actions as lib
from family_contracts import ContractError, resolve_candidates


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
            assert (checkout / artifact).is_file(), (
                f"missing {entry['name']} artifact: {artifact}"
            )


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
    mncs = json.loads(
        (root / "family/mncs-standard/examples/minimal/manifest.json").read_text()
    )
    rights_schema = json.loads(
        (
            root / "family/rights-provenance/schemas/v0.3/lineage-record.schema.json"
        ).read_text()
    )
    mncds_schema = json.loads(
        (
            root / "family/mncds/schemas/mncds-development-record-0.2-alpha.schema.json"
        ).read_text()
    )
    forge = json.loads(
        (root / "family/forge/examples/forge-cell/execution-record.json").read_text()
    )
    assert isinstance(mncs.get("schema_version"), str)
    assert rights_schema["properties"]["schema_version"]["const"] == "0.3.0"
    assert mncds_schema["$schema"].startswith("https://json-schema.org/")
    assert forge["record_type"] == "forge-cell-execution"
    assert forge["schema_version"] == "0.1"


# --- Recorded-candidate resolution ----------------------------------------

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_ENV)
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _member_repo(
    root: Path, *, recorded: str | None, obligations: dict[str, str]
) -> Path:
    """Build a synthetic member checkout: base commit, then a head commit.

    recorded: the commit the head tree's promotion/candidate.json names
    ("head" for the head commit itself, a 40-hex string, or None for no file).
    obligations: basename -> subject-commit written into the head tree.
    """
    checkout = root / "family" / "probe"
    checkout.mkdir(parents=True)
    _git(checkout, "init", "-q")
    (checkout / "artifact.txt").write_text("v1\n")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "base")
    base = _git(checkout, "rev-parse", "HEAD")
    if recorded is not None or obligations:
        if recorded == "head":
            recorded = base
        if recorded is not None:
            candidate = {
                "schema_version": "mncds-promotion-candidate/0.1",
                "repository": "epi13/probe",
                "commit": recorded,
                "obligations": [
                    f"promotion/obligations/{name}" for name in obligations
                ],
            }
            path = checkout / "promotion" / "candidate.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
        for name, subject in obligations.items():
            record = {
                "schema_version": "mncds-obligation-record/0.2",
                "obligation_key": f"pressure.probe.{name}",
                "status": "resolved",
                "subject": {"repository": "epi13/probe", "commit": subject},
            }
            path = checkout / "promotion" / "obligations" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record) + "\n")
        _git(checkout, "add", ".")
        _git(checkout, "commit", "-q", "-m", "head")
    return checkout


def _documents(root: Path, head: str, base: str):
    fixed = {
        "schema_version": "mncs-actions.family-contracts/1",
        "repositories": [
            {
                "name": "probe",
                "repository": "epi13/probe",
                "revision": base,
                "checkout_path": "family/probe",
                "artifacts": ["artifact.txt"],
            }
        ],
    }
    candidate = {
        "schema_version": "mncs-actions.family-contract-candidate/1",
        "source": "moving-head",
        "base_schema_version": "mncs-actions.family-contracts/1",
        "base_contract_digest": lib.sha256_hex(lib.canonical_bytes(fixed)),
        "branch": "main",
        "repositories": [
            {
                "name": "probe",
                "repository": "epi13/probe",
                "branch": "main",
                "base_revision": base,
                "candidate_revision": head,
                "checkout_path": "family/probe",
                "artifacts": ["artifact.txt"],
            }
        ],
    }
    return fixed, candidate


def _resolve(root: Path, fixed: dict, candidate: dict, out_name: str = "out"):
    obligations = root / "obligations"
    resolved = resolve_candidates(candidate, fixed, root, obligations)
    assert (
        resolved["repositories"][0]["base_revision"]
        == fixed["repositories"][0]["revision"]
    )
    return resolved, obligations


def test_resolve_honors_recorded_candidate_and_snapshots_set(tmp_path: Path):
    root = tmp_path / "fam"
    checkout = _member_repo(root, recorded="head", obligations={})
    base = _git(checkout, "rev-parse", "HEAD~1")
    head = _git(checkout, "rev-parse", "HEAD")
    # Rewrite the recorded file to name the base commit with one obligation.
    record = {
        "schema_version": "mncds-obligation-record/0.2",
        "obligation_key": "pressure.probe.required",
        "status": "resolved",
        "subject": {"repository": "epi13/probe", "commit": base},
    }
    (checkout / "promotion" / "obligations").mkdir(parents=True, exist_ok=True)
    (checkout / "promotion" / "obligations" / "req.obligation.json").write_text(
        json.dumps(record) + "\n"
    )
    candidate_doc = {
        "schema_version": "mncds-promotion-candidate/0.1",
        "repository": "epi13/probe",
        "commit": base,
        "obligations": ["promotion/obligations/req.obligation.json"],
    }
    (checkout / "promotion" / "candidate.json").write_text(
        json.dumps(candidate_doc, indent=2) + "\n"
    )
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "rebind")
    head = _git(checkout, "rev-parse", "HEAD")
    fixed, candidate = _documents(root, head, base)
    resolved, obligations = _resolve(root, fixed, candidate)
    assert resolved["repositories"][0]["candidate_revision"] == base
    staged = obligations / "probe--req.obligation.json"
    assert staged.is_file()
    assert json.loads(staged.read_text())["subject"]["commit"] == base


def test_resolve_keeps_head_without_recorded_candidate(tmp_path: Path):
    root = tmp_path / "fam"
    checkout = _member_repo(root, recorded=None, obligations={})
    head = _git(checkout, "rev-parse", "HEAD")
    fixed, candidate = _documents(root, head, head)
    resolved, obligations = _resolve(root, fixed, candidate)
    assert resolved["repositories"][0]["candidate_revision"] == head
    assert list(obligations.iterdir()) == []


def test_resolve_refuses_obligations_without_candidate(tmp_path: Path):
    root = tmp_path / "fam"
    checkout = _member_repo(root, recorded=None, obligations={"x.json": "0" * 40})
    head = _git(checkout, "rev-parse", "HEAD")
    fixed, candidate = _documents(root, head, head)
    with pytest.raises(ContractError):
        _resolve(root, fixed, candidate)


def test_resolve_refuses_wrong_repository(tmp_path: Path):
    root = tmp_path / "fam"
    checkout = _member_repo(root, recorded="head", obligations={})
    head = _git(checkout, "rev-parse", "HEAD")
    doc = json.loads((checkout / "promotion" / "candidate.json").read_text())
    doc["repository"] = "epi13/other"
    (checkout / "promotion" / "candidate.json").write_text(json.dumps(doc))
    fixed, candidate = _documents(root, head, head)
    with pytest.raises(ContractError):
        _resolve(root, fixed, candidate)


def test_resolve_refuses_unknown_commit_object(tmp_path: Path):
    root = tmp_path / "fam"
    checkout = _member_repo(root, recorded="head", obligations={})
    head = _git(checkout, "rev-parse", "HEAD")
    doc = json.loads((checkout / "promotion" / "candidate.json").read_text())
    doc["commit"] = "f" * 40
    (checkout / "promotion" / "candidate.json").write_text(json.dumps(doc))
    fixed, candidate = _documents(root, head, head)
    with pytest.raises(ContractError):
        _resolve(root, fixed, candidate)


def test_resolve_refuses_unlisted_shipped_obligation(tmp_path: Path):
    root = tmp_path / "fam"
    checkout = _member_repo(root, recorded="head", obligations={})
    base = _git(checkout, "rev-parse", "HEAD~1")
    record = {
        "schema_version": "mncds-obligation-record/0.2",
        "obligation_key": "pressure.probe.req",
        "status": "resolved",
        "subject": {"repository": "epi13/probe", "commit": base},
    }
    (checkout / "promotion" / "obligations").mkdir(parents=True, exist_ok=True)
    (checkout / "promotion" / "obligations" / "req.json").write_text(
        json.dumps(record) + "\n"
    )
    doc = json.loads((checkout / "promotion" / "candidate.json").read_text())
    doc["obligations"] = ["promotion/obligations/req.json"]
    (checkout / "promotion" / "candidate.json").write_text(json.dumps(doc))
    record = {
        "schema_version": "mncds-obligation-record/0.2",
        "obligation_key": "pressure.probe.extra",
        "status": "open",
        "subject": {"repository": "epi13/probe", "commit": base},
    }
    (checkout / "promotion" / "obligations").mkdir(parents=True, exist_ok=True)
    (checkout / "promotion" / "obligations" / "extra.json").write_text(
        json.dumps(record) + "\n"
    )
    head = _git(checkout, "rev-parse", "HEAD")
    fixed, candidate = _documents(root, head, base)
    with pytest.raises(ContractError):
        _resolve(root, fixed, candidate)


def test_resolve_refuses_nonempty_obligations_dir(tmp_path: Path):
    root = tmp_path / "fam"
    checkout = _member_repo(root, recorded=None, obligations={})
    head = _git(checkout, "rev-parse", "HEAD")
    fixed, candidate = _documents(root, head, head)
    (root / "obligations").mkdir(parents=True)
    (root / "obligations" / "stale.json").write_text("{}\n")
    with pytest.raises(ContractError):
        _resolve(root, fixed, candidate)
