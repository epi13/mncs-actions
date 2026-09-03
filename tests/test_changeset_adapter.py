"""Cross-repository ChangeSet/lineage bridge and live family canaries."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "adapters" / "changeset_adapter.py"
FIXTURE = REPO / "tests" / "fixtures" / "changeset" / "lineage-v0.3-pass.json"
RIGHTS_ROOT = REPO.parent / "mncs-rights-provenance"
MNCS_ROOT = REPO.parent / "machine-native-complexity-standard"
MNCDS_ROOT = REPO.parent / "machine-native-complexity-development-specification"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def reseal(record: dict) -> dict:
    unsigned = {key: value for key, value in record.items() if key != "content_digest"}
    record["content_digest"] = "sha256:" + lib.sha256_hex(lib.canonical_bytes(unsigned))
    return record


def test_current_bridge_fixture_is_pass_and_digest_bound():
    verdict, unresolved, errors, summary = lib.classify_changeset_lineage(load_fixture())
    assert errors == []
    assert unresolved == []
    assert verdict == "PASS"
    assert summary["participant_count"] == 2
    assert summary["content_digest_matches"] is True


def test_current_rights_lineage_record_is_an_honest_unknown_canary():
    current = RIGHTS_ROOT / "dogfood" / "distributed-pressure-changeset.json"
    if not current.is_file():
        pytest.skip("current mncs-rights-provenance lineage fixture is not available")
    record = json.loads(current.read_text(encoding="utf-8"))
    verdict, unresolved, errors, summary = lib.classify_changeset_lineage(record)
    assert errors == []
    assert verdict == "UNKNOWN"
    assert unresolved
    assert summary["content_digest_matches"] is True


def test_adapter_emits_independent_generic_check(tmp_path):
    output = tmp_path / "check.json"
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), "--input", str(FIXTURE), "--output", str(output)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr
    check = json.loads(output.read_text(encoding="utf-8"))
    assert check["id"] == "changeset-coordination"
    assert check["provider"] == "mncs-rights-provenance-lineage"
    assert check["verdict"] == "PASS"
    assert check["references"][0]["digest"] == load_fixture()["content_digest"][7:]
    assert lib.validate_check_result(check) == []


def test_duplicate_participant_is_not_established():
    record = load_fixture()
    record["changesets"][0]["base_revisions"].append(
        copy.deepcopy(record["changesets"][0]["base_revisions"][0])
    )
    reseal(record)
    verdict, _, errors, _ = lib.classify_changeset_lineage(record)
    assert verdict is None
    assert any("duplicate ChangeSet participant" in error for error in errors)


def test_wrong_expected_participant_revision_is_not_established():
    record = load_fixture()
    repo = record["changesets"][0]["base_revisions"][0]["repository"]
    expected = {repo: "0" * 40}
    verdict, _, errors, _ = lib.classify_changeset_lineage(
        record, expected_revisions=expected
    )
    assert verdict is None
    assert any("revision mismatch" in error for error in errors)


def test_malformed_expected_revision_is_rejected(tmp_path):
    output = tmp_path / "check.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(ADAPTER),
            "--input",
            str(FIXTURE),
            "--output",
            str(output),
            "--expected-revision",
            "github.com/epi13/MNCS-Commons=not-a-sha",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 2
    assert "40-character-SHA" in proc.stderr


def test_wrong_content_digest_is_not_established():
    record = load_fixture()
    record["content_digest"] = "sha256:" + "0" * 64
    verdict, _, errors, _ = lib.classify_changeset_lineage(record)
    assert verdict is None
    assert any("content_digest does not match" in error for error in errors)


def test_missing_coordination_evidence_is_unknown_not_pass():
    record = load_fixture()
    record["unresolved"] = ["commons-retention-refs"]
    reseal(record)
    verdict, unresolved, errors, _ = lib.classify_changeset_lineage(record)
    assert errors == []
    assert verdict == "UNKNOWN"
    assert "commons-retention-refs" in unresolved


def test_wrong_local_evidence_digest_is_not_established(tmp_path):
    record = load_fixture()
    evidence = tmp_path / "receipt.json"
    evidence.write_text("actual\n", encoding="utf-8")
    record["subject"]["evidence_refs"] = [
        {"kind": "validation-receipt", "reference": "receipt.json", "sha256": "0" * 64}
    ]
    reseal(record)
    verdict, _, errors, _ = lib.classify_changeset_lineage(
        record, evidence_root=tmp_path
    )
    assert verdict is None
    assert any("do not match sha256" in error for error in errors)


def test_live_rights_validate_canary(tmp_path):
    if not RIGHTS_ROOT.is_dir():
        pytest.skip("mncs-rights-provenance checkout is not available")
    report = tmp_path / "rights-report.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mncs_rights_provenance.cli",
            "validate",
            str(RIGHTS_ROOT / "dogfood" / "human-specification.json"),
            "--findings-are-not-failures",
        ],
        cwd=RIGHTS_ROOT,
        env={**os.environ, "PYTHONPATH": str(RIGHTS_ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    report.write_text(proc.stdout, encoding="utf-8")
    output = tmp_path / "rights-check.json"
    adapted = subprocess.run(
        [sys.executable, str(REPO / "adapters" / "rights_adapter.py"), "--input", str(report), "--output", str(output)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert adapted.returncode == 0, adapted.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["verdict"] == "UNKNOWN"


def test_live_mncs_validator_canary(tmp_path):
    if not MNCS_ROOT.is_dir():
        pytest.skip("machine-native-complexity-standard checkout is not available")
    report = tmp_path / "mncs-report.json"
    script = (
        "from mncs_validator.cli import main; "
        f"raise SystemExit(main(['validate', '{MNCS_ROOT / 'examples/minimal/manifest.json'}', '--json']))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=MNCS_ROOT,
        env={**os.environ, "PYTHONPATH": str(MNCS_ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    report.write_text(proc.stdout, encoding="utf-8")
    output = tmp_path / "mncs-check.json"
    adapted = subprocess.run(
        [sys.executable, str(REPO / "adapters" / "validator_adapter.py"), "--input", str(report), "--output", str(output)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert adapted.returncode == 0, adapted.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_live_mncds_validator_canary():
    if not MNCDS_ROOT.is_dir():
        pytest.skip("machine-native-complexity-development-specification checkout is not available")
    script = (
        "from pathlib import Path; import json; "
        "from mncds_validator.mncds import validate_development_record; "
        f"print(json.dumps(validate_development_record(Path('{MNCDS_ROOT / 'examples/mncds-0.2-alpha/language-span-fix.development-record.json'}')).as_dict()))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=MNCDS_ROOT,
        env={**os.environ, "PYTHONPATH": str(MNCDS_ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    report = json.loads(proc.stdout)
    assert report["supported"] is True
    assert report["valid"] is True
    assert report["computed_status"] == "PASS"
