"""Promotion transport: MNCDS/promotion wiring without owning their semantics.

Covers the adversarial boundary between owner authorities and transport:

- stale/cross-subject evidence never promotes another revision;
- unresolved required obligations surface as UNKNOWN with exact keys;
- missing required evidence stays UNKNOWN, omitted optional stays absent;
- malformed or forged claims establish nothing (INVALID, never UNKNOWN);
- duplicate ids, wrong digests, and moving refs fail safely;
- valid negatives stay FAIL (never demoted to INVALID, never promoted).

Verdict semantics themselves belong to MNCDS (check catalog) and MNCS
(promotion evaluator); these tests pin the transport membrane around them.
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "promotion"
ADAPTERS = REPO / "adapters"
SCRIPTS = REPO / "scripts"

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
BOUNDARY_ID = "family-promotion"
SUBJECT_REPO = "epi13/mncs-actions"


def _fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _promotion_kwargs(**overrides):
    params = {
        "boundary_id": BOUNDARY_ID,
        "subject_repository": SUBJECT_REPO,
        "subject_commit": COMMIT,
    }
    params.update(overrides)
    return params


def _run_adapter(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ADAPTERS / script), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _write(tmp_path: Path, name: str, doc: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


# --- promotion claim validation (transport membrane) ---


def test_valid_promotion_pass_is_accepted():
    assert lib.validate_promotion_claim(_fixture("promotion-pass.json"), **_promotion_kwargs()) == []


def test_promotion_for_wrong_revision_is_rejected():
    doc = _fixture("promotion-pass.json")
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs(subject_commit=OTHER_COMMIT))


def test_promotion_for_wrong_boundary_is_rejected():
    doc = _fixture("promotion-pass.json")
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs(boundary_id="other-boundary"))


def test_promotion_unknown_without_blockers_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["verdict"] = "UNKNOWN"
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_pass_with_blockers_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["promotion"]["blockers"] = ["required check x is missing"]
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_forged_pass_without_promotion_extension_is_rejected():
    doc = _fixture("promotion-pass.json")
    del doc["promotion"]
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_without_digest_bound_evidence_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["verdict"] = "UNKNOWN"
    doc["promotion"]["blockers"] = ["required check mncs-validation is missing"]
    doc["unresolved"] = ["required check mncs-validation is missing"]
    for ref in doc["references"]:
        ref.pop("digest", None)
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_unknown_with_blockers_is_accepted():
    doc = _fixture("promotion-pass.json")
    doc["verdict"] = "UNKNOWN"
    doc["promotion"]["blockers"] = ["obligation pressure.gap-1 open (required)"]
    doc["unresolved"] = ["obligation pressure.gap-1 open (required)"]
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs()) == []


# --- subject stamping ---


def test_subject_stamp_rejects_moving_refs():
    stamp, error = lib.subject_stamp(SUBJECT_REPO, "main")
    assert stamp is None and error


def test_subject_stamp_rejects_partial_binding():
    stamp, error = lib.subject_stamp(SUBJECT_REPO, "")
    assert stamp is None and error


def test_subject_stamp_accepts_exact_revision():
    stamp, error = lib.subject_stamp(SUBJECT_REPO, COMMIT)
    assert error is None and stamp == {"repository": SUBJECT_REPO, "commit": COMMIT}


# --- MNCDS adapter: catalog mapping is transport, not invention ---


def _mncds_report(**overrides) -> dict:
    report = {
        "target": "record.json",
        "valid": True,
        "supported": True,
        "computed_status": "PASS",
        "profile": "MNCDS-D1",
        "record_id": "record.fixture-1",
        "issues": [],
        "warnings": [],
        "category": "PASS",
    }
    report.update(overrides)
    return report


def test_mncds_adapter_pass_unknown_fail(tmp_path: Path):
    for status, verdict in (("PASS", "PASS"), ("UNKNOWN", "UNKNOWN"), ("FAIL", "FAIL")):
        src = _write(tmp_path, f"report-{status}.json", _mncds_report(computed_status=status))
        out = str(tmp_path / f"check-{status}.json")
        proc = _run_adapter(
            "mncds_adapter.py",
            "--input", src, "--output", out,
            "--subject-repository", SUBJECT_REPO, "--subject-commit", COMMIT,
        )
        assert proc.returncode == 0, proc.stderr
        check = json.loads(Path(out).read_text(encoding="utf-8"))
        assert check["verdict"] == verdict
        assert check["subject"] == {"repository": SUBJECT_REPO, "commit": COMMIT}
        assert lib.validate_check_result(check) == []


def test_mncds_adapter_invalid_record_is_fail_not_invalid(tmp_path: Path):
    # valid=false means issues were established: a valid negative (FAIL),
    # not a missing claim.
    src = _write(tmp_path, "report.json", _mncds_report(valid=False, computed_status="FAIL", issues=[{"code": "x", "message": "bad"}]))
    out = str(tmp_path / "check.json")
    proc = _run_adapter("mncds_adapter.py", "--input", src, "--output", out)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(Path(out).read_text(encoding="utf-8"))["verdict"] == "FAIL"


def test_mncds_adapter_malformed_report_establishes_no_claim(tmp_path: Path):
    src = tmp_path / "report.json"
    src.write_text("{not json", encoding="utf-8")
    proc = _run_adapter("mncds_adapter.py", "--input", str(src), "--output", str(tmp_path / "check.json"))
    assert proc.returncode == 2
    assert not (tmp_path / "check.json").exists()


def test_mncds_adapter_forged_status_never_passes(tmp_path: Path):
    src = _write(tmp_path, "report.json", _mncds_report(computed_status="BLESSED"))
    out = str(tmp_path / "check.json")
    proc = _run_adapter("mncds_adapter.py", "--input", src, "--output", out)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(Path(out).read_text(encoding="utf-8"))["verdict"] == "UNKNOWN"


def test_mncds_adapter_moving_subject_is_rejected(tmp_path: Path):
    src = _write(tmp_path, "report.json", _mncds_report())
    proc = _run_adapter(
        "mncds_adapter.py", "--input", src, "--output", str(tmp_path / "check.json"),
        "--subject-repository", SUBJECT_REPO, "--subject-commit", "main",
    )
    assert proc.returncode == 2


# --- obligation projection: pressure enters the lifecycle ---


def _obligation(key: str, status: str, required: bool = True, commit: str = COMMIT) -> dict:
    doc = {
        "schema_version": "mncds-obligation-record/0.2",
        "obligation_key": key,
        "status": status,
        "required": required,
        "subject": {"repository": SUBJECT_REPO, "commit": commit},
        "origin": {"kind": "development-pressure", "authority": "mncs-actions"},
        "evidence": [],
        "supersedes": None,
        "extensions": {},
    }
    if status in ("resolved", "rejected"):
        doc["resolution"] = {
            "resolution": "fixed" if status == "resolved" else "rejected",
            "evidence_refs": ["sha256:" + "c" * 64],
            "resolved_by": "epi13/mncs-actions",
            "resolved_at": "2026-09-04T00:00:00Z",
        }
    return doc


def _project(tmp_path: Path, obligations: list[dict], namespace: str) -> tuple[int, dict | None]:
    paths = [_write(tmp_path, f"{namespace}-{i}.json", doc) for i, doc in enumerate(obligations)]
    out = str(tmp_path / f"{namespace}-check.json")
    command = [
        sys.executable, str(SCRIPTS / "project_obligations.py"),
        "--subject-repository", SUBJECT_REPO, "--subject-commit", COMMIT,
        "--output", out,
    ]
    if paths:
        command += ["--obligations", *paths]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    result = json.loads(Path(out).read_text(encoding="utf-8")) if Path(out).is_file() else None
    return proc.returncode, result


def test_open_required_obligation_is_unknown_with_key(tmp_path: Path):
    code, result = _project(tmp_path, [_obligation("pressure.gap-1", "open")], "open")
    assert code == 0
    assert result is not None and result["verdict"] == "UNKNOWN"
    assert result["id"] == "mncds-obligations"
    assert any("pressure.gap-1" in item for item in result["unresolved"])
    assert lib.validate_check_result(result) == []


def test_resolved_obligations_promote_to_pass(tmp_path: Path):
    code, result = _project(
        tmp_path,
        [_obligation("pressure.gap-1", "resolved"), _obligation("pressure.gap-2", "open", required=False)],
        "resolved",
    )
    assert code == 0
    assert result is not None and result["verdict"] == "PASS"


def test_derived_authority_map_matches_owner_contract(tmp_path: Path):
    out = str(tmp_path / "authority-map.json")
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "authority_map.py"),
            "--descriptors", str(REPO / "family-producer-descriptors.json"),
            "--output", out,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(Path(out).read_text(encoding="utf-8"))
    assert doc["schema_version"] == "mncs-authority-map/0.1"
    assert doc["authorities"]["mncds-development-record"] == {
        "provider": "mncds",
        "authority": "machine-native-complexity-development-specification",
        "repository": "epi13/machine-native-complexity-development-specification",
    }
    assert doc["authorities"]["mncs-validation"]["authority"] == (
        "machine-native-complexity-standard"
    )
    assert doc["authorities"]["rights-provenance"]["provider"] == (
        "mncs-rights-provenance"
    )


def test_fixture_open_obligation_is_unknown_with_key(tmp_path: Path):
    out = str(tmp_path / "fixture-check.json")
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "project_obligations.py"),
            "--obligations", str(FIX / "obligation-open.json"),
            "--subject-repository", SUBJECT_REPO, "--subject-commit", COMMIT,
            "--output", out,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    check = json.loads(Path(out).read_text(encoding="utf-8"))
    assert check["verdict"] == "UNKNOWN"
    assert any("pressure.fixture.open-required" in item for item in check["unresolved"])


def test_empty_obligation_set_is_pass(tmp_path: Path):
    code, result = _project(tmp_path, [], "empty")
    assert code == 0
    assert result is not None and result["verdict"] == "PASS"


def test_rejected_obligation_is_negative(tmp_path: Path):
    code, result = _project(tmp_path, [_obligation("pressure.gap-9", "rejected")], "rejected")
    assert code == 0
    assert result is not None and result["verdict"] == "FAIL"


def test_stale_obligation_against_newer_candidate_is_rejected(tmp_path: Path):
    code, result = _project(
        tmp_path, [_obligation("pressure.gap-1", "open", commit=OTHER_COMMIT)], "stale"
    )
    assert code == 2
    assert result is None


def test_duplicate_obligation_keys_are_contradictory(tmp_path: Path):
    code, result = _project(
        tmp_path,
        [_obligation("pressure.gap-1", "open"), _obligation("pressure.gap-1", "resolved")],
        "duplicate",
    )
    assert code == 2
    assert result is None


# --- pressure enters the obligation lifecycle, not beside it ---


def _pressure_evidence(*keys: str) -> dict:
    return {
        "schema_version": "mncs.development-pressure-evidence/1",
        "obligations": [
            {
                "pressure_id": f"sha256:{'1' * 64}",
                "obligation_key": key,
                "producer": "mncs-actions",
                "semantic_authority": "mncs-language",
                "unresolved": [f"gap {key} blocks capability X"],
            }
            for key in keys
        ],
        "not_reproduced": [],
    }


def test_pressure_projects_to_open_required_obligations(tmp_path):
    pressure = _write(tmp_path, "pressure.json", _pressure_evidence("pressure.gap-1"))
    out = str(tmp_path / "obligations.json")
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "pressure_to_obligations.py"),
            "--pressure", pressure,
            "--subject-repository", SUBJECT_REPO, "--subject-commit", COMMIT,
            "--output", out,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    records = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["obligation_key"] == "pressure.gap-1"
    assert records[0]["status"] == "open" and records[0]["required"] is True
    assert records[0]["origin"]["kind"] == "development-pressure"
    assert records[0]["subject"] == {"repository": SUBJECT_REPO, "commit": COMMIT}
    # The projected set chains into the obligations check as UNKNOWN.
    code, result = _project(tmp_path, [], "chain")
    assert code == 0 and result is not None and result["verdict"] == "PASS"
    chained = tmp_path / "chain-check.json"
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "project_obligations.py"),
            "--obligations", out,
            "--subject-repository", SUBJECT_REPO, "--subject-commit", COMMIT,
            "--output", str(chained),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    check = json.loads(chained.read_text(encoding="utf-8"))
    assert check["verdict"] == "UNKNOWN"
    assert any("pressure.gap-1" in item for item in check["unresolved"])


def test_pressure_projection_rejects_moving_subject(tmp_path):
    pressure = _write(tmp_path, "pressure.json", _pressure_evidence("pressure.gap-1"))
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "pressure_to_obligations.py"),
            "--pressure", pressure,
            "--subject-repository", SUBJECT_REPO, "--subject-commit", "main",
            "--output", str(tmp_path / "obligations.json"),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2


# --- aggregation: promotion composes like every other check ---


def test_missing_required_promotion_stays_unknown():
    verdict, _ = lib.aggregate_verdict(
        {"mncs-validation": "PASS", "mncds-development-record": "PASS"},
        required=["mncs-validation", "mncds-development-record", "promotion-boundary"],
    )
    assert verdict == "UNKNOWN"


def test_unknown_never_converts_to_pass():
    verdict, _ = lib.aggregate_verdict(
        {"mncs-validation": "PASS", "promotion-boundary": "UNKNOWN"},
        required=["mncs-validation", "promotion-boundary"],
    )
    assert verdict == "UNKNOWN"


def test_valid_negative_promotion_is_fail():
    verdict, _ = lib.aggregate_verdict(
        {"mncs-validation": "PASS", "promotion-boundary": "FAIL"},
        required=["mncs-validation", "promotion-boundary"],
    )
    assert verdict == "FAIL"


def test_omitted_optional_promotion_has_no_effect():
    verdict, unresolved = lib.aggregate_verdict(
        {"mncs-validation": "PASS"},
        required=["mncs-validation"],
        optional=["promotion-boundary"],
    )
    assert verdict == "PASS"
    assert unresolved == []


def test_duplicate_promotion_ids_fail_closed(tmp_path):
    import os
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    shutil.copyfile(FIX / "promotion-pass.json", work / "a.json")
    shutil.copyfile(FIX / "promotion-pass.json", work / "b.json")
    ev = work / "agg-evidence"
    out = tmp_path / "github-output.txt"
    summary = tmp_path / "step-summary.md"
    out.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["GITHUB_OUTPUT"] = str(out)
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    proc = subprocess.run(
        [
            str(REPO / "actions" / "aggregate" / "aggregate.sh"),
            "--checks", "a.json b.json",
            "--required", "promotion-boundary",
            "--evidence-dir", str(ev),
            "--working-dir", str(work),
            "--boundary", "family-promotion",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=work,
    )
    assert proc.returncode != 0
    assert "duplicate check id" in (proc.stderr + proc.stdout)


def test_full_promotion_path_aggregates_to_pass(tmp_path):
    import os
    import shutil

    work = tmp_path / "work"
    work.mkdir()
    for name in ("mncs-pass.json", "mncds-pass.json", "promotion-pass.json"):
        shutil.copyfile(FIX / name, work / name)
    ev = work / "agg-evidence"
    out = tmp_path / "github-output.txt"
    summary = tmp_path / "step-summary.md"
    out.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["GITHUB_OUTPUT"] = str(out)
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    proc = subprocess.run(
        [
            str(REPO / "actions" / "aggregate" / "aggregate.sh"),
            "--checks", "mncs-pass.json mncds-pass.json promotion-pass.json",
            "--required", "mncs-validation,mncds-development-record,promotion-boundary",
            "--evidence-dir", str(ev),
            "--working-dir", str(work),
            "--boundary", "family-promotion",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    agg = json.loads((ev / "aggregate-result.json").read_text(encoding="utf-8"))
    assert agg["verdict"] == "PASS"
    assert lib.validate_aggregate_result(agg) == []


def test_promotion_with_impossible_counts_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["promotion"]["required_passed"] = 4
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_pass_with_partial_counts_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["promotion"]["required_passed"] = 2
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_with_wrong_boundary_revision_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["promotion"]["boundary_revision"] = "mncs-promotion-boundary/9.9"
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_with_disagreeing_subject_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["subject"] = {"repository": SUBJECT_REPO, "commit": OTHER_COMMIT}
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_with_duplicate_references_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["references"].append(copy.deepcopy(doc["references"][0]))
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_promotion_with_malformed_authority_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["references"][0]["authority"] = ""
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_summary_renderer_projects_claim(tmp_path: Path):
    out = str(tmp_path / "summary.md")
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "render_promotion_summary.py"),
            "--input", str(FIX / "promotion-pass.json"),
            "--output", out,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    text = Path(out).read_text(encoding="utf-8")
    assert "Verdict: PASS" in text
    assert "family-promotion" in text
    assert COMMIT in text
    assert "Blockers (0)" in text


def test_summary_renderer_rejects_non_claim(tmp_path: Path):
    src = _write(tmp_path, "bad.json", {"nope": True})
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "render_promotion_summary.py"),
            "--input", src,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2


def test_promotion_with_unnamed_reference_is_rejected():
    doc = _fixture("promotion-pass.json")
    doc["references"].append(
        {"kind": "check-result", "digest": "sha256:" + "e" * 64}
    )
    assert lib.validate_promotion_claim(doc, **_promotion_kwargs())


def test_valid_negative_is_not_invalid():
    assert lib.validate_check_result(_fixture("mncs-pass.json")) == []
    forged = copy.deepcopy(_fixture("mncs-pass.json"))
    forged["verdict"] = "FORGED"
    assert lib.validate_check_result(forged)


def test_wrong_evidence_digest_is_rejected():
    doc = copy.deepcopy(_fixture("mncs-pass.json"))
    doc["references"][0]["digest"] = "not-a-digest"
    assert lib.validate_check_result(doc)
