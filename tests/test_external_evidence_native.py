"""Native tests for ambient external-evidence policy.

The policy module is exercised through the canonical coherence shim so
the descriptor, interface identity, and application path stay covered.
When the native toolchain cannot resolve its library roots (a broken
selected toolchain, not a policy fault) the tests skip rather than
fail; a stale interface identity on an unchanged module is a real
failure and is not skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR = ROOT / "native-applications" / "actions-external-evidence.json"
SHIM = ROOT / "bin" / "mncs-actions-coherence"


def _obligation(identity: str, **overrides) -> dict:
    base = {
        "obligation": identity,
        "routable_kind": True,
        "has_workflow_binding": True,
        "explicit_only": False,
        "subject_state": "clean",
        "subject_digest": "sha256:aaa",
        "remote_available": True,
        "attempts_exhausted": False,
        "evidence_present": False,
        "evidence": {
            "obligation": "", "subject_digest": "", "run_identity": "",
            "verdict": "none", "claim": "none", "complete": False,
        },
        "run_present": False,
        "run": {"run_identity": "", "subject_digest": "",
                "state": "none", "conclusion": "none"},
    }
    base.update(overrides)
    return base


def _run_policy(request: dict, tmp_path: Path) -> dict:
    binary = os.environ.get("MNCS_BINARY")
    if not binary:
        pytest.skip("set MNCS_BINARY to exercise native external-evidence policy")
    env = dict(os.environ)
    env["MNCS"] = binary
    env["MNCS_NATIVE_APPLICATION_CACHE_DIR"] = str(tmp_path / "cache")
    (tmp_path / "request.json").write_text(json.dumps(request), encoding="utf-8")
    try:
        completed = subprocess.run(
            [str(SHIM), "request.json", "result.json"], cwd=tmp_path,
            capture_output=True, text=True, check=False, env=env, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"native MNCS launcher unavailable: {error}")
    if completed.returncode != 0:
        blob = (completed.stderr or "") + (completed.stdout or "")
        if "could not be resolved" in blob or "MNE173" in blob \
                or "unavailable to the resolver" in blob:
            pytest.skip(f"native toolchain unresolvable: {blob[:200]}")
        if "stale_interface" in blob:
            pytest.fail(f"descriptor interface identity is stale: {blob[:300]}")
        pytest.fail(f"policy execution failed: {blob[:500]}")
    return json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))


def _request(obligations: list[dict]) -> dict:
    return {"schema_version": "mncs.actions-external-evidence-request/1",
            "max_dispatches": 4, "obligations": obligations}


def test_clean_missing_evidence_is_dispatch_eligible(tmp_path) -> None:
    result = _run_policy(_request([_obligation("ob.one")]), tmp_path)
    assert result["schema_version"] == "mncs.actions-external-evidence/1"
    (decision,) = result["decisions"]
    assert decision["status"] == "dispatch_eligible"
    assert decision["reason"] == "no_evidence"
    assert decision["operation"] == "dispatch"
    assert result["dispatch_queue"] == ["ob.one"]


def test_dirty_subject_is_unavailable(tmp_path) -> None:
    result = _run_policy(
        _request([_obligation("ob.one", subject_state="dirty")]), tmp_path)
    (decision,) = result["decisions"]
    assert decision["status"] == "unavailable"
    assert decision["reason"] == "dirty_subject"
    assert decision["operation"] == "none"


def test_unpublished_revision_is_unavailable(tmp_path) -> None:
    result = _run_policy(
        _request([_obligation("ob.one", subject_state="clean_unpublished")]),
        tmp_path)
    (decision,) = result["decisions"]
    assert decision["status"] == "unavailable"
    assert decision["reason"] == "unpublished_subject"


def test_missing_binding_has_no_route(tmp_path) -> None:
    result = _run_policy(
        _request([_obligation("ob.one", has_workflow_binding=False)]), tmp_path)
    (decision,) = result["decisions"]
    assert decision["status"] == "no_route"
    assert decision["reason"] == "no_workflow_binding"


def test_explicit_only_escalates(tmp_path) -> None:
    result = _run_policy(
        _request([_obligation("ob.one", explicit_only=True)]), tmp_path)
    (decision,) = result["decisions"]
    assert decision["status"] == "escalate"
    assert decision["reason"] == "explicit_only_effects"


def test_exact_evidence_is_current_and_carries_verdict(tmp_path) -> None:
    evidence = {"obligation": "ob.one", "subject_digest": "sha256:aaa",
                "run_identity": "123", "verdict": "failed",
                "claim": "established", "complete": True}
    result = _run_policy(
        _request([_obligation("ob.one", evidence_present=True, evidence=evidence)]),
        tmp_path)
    (decision,) = result["decisions"]
    assert decision["status"] == "current"
    assert decision["verdict"] == "failed"
    assert decision["run_identity"] == "123"


def test_unestablished_claim_never_admits(tmp_path) -> None:
    evidence = {"obligation": "ob.one", "subject_digest": "sha256:aaa",
                "run_identity": "123", "verdict": "passed",
                "claim": "not_established", "complete": True}
    result = _run_policy(
        _request([_obligation("ob.one", evidence_present=True, evidence=evidence)]),
        tmp_path)
    (decision,) = result["decisions"]
    assert decision["status"] == "dispatch_eligible"
    assert decision["reason"] == "claim_not_established"


def test_in_flight_run_attaches_without_redispatch(tmp_path) -> None:
    run = {"run_identity": "999", "subject_digest": "sha256:aaa",
           "state": "in_progress", "conclusion": "none"}
    result = _run_policy(
        _request([_obligation("ob.one", run_present=True, run=run)]), tmp_path)
    (decision,) = result["decisions"]
    assert decision["status"] == "delegated_pending"
    assert decision["reason"] == "run_pending"
    assert decision["operation"] == "subscribe"
    assert result["dispatch_queue"] == []


def test_budget_defers_beyond_max_dispatches(tmp_path) -> None:
    request = _request([_obligation(f"ob.{index}") for index in range(3)])
    request["max_dispatches"] = 1
    result = _run_policy(request, tmp_path)
    assert result["summary"]["dispatch_queued"] == 1
    assert result["summary"]["deferred"] == 2
    assert len(result["dispatch_queue"]) == 1
