"""Transport tests for the GitHub remote-evidence boundary.

These tests never touch the network: the ``gh`` subprocess layer is
stubbed, and only ledger semantics, error classification, and staged
validation run for real.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _load_transport():
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader(
        "mncs_actions_remote", str(ROOT / "bin" / "mncs-actions-remote"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TRANSPORT = _load_transport()


def _run_cli(*argv: str, env: dict | None = None) -> dict:
    full_env = dict(env or {})
    merged = {**dict(__import__("os").environ), **full_env}
    completed = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "mncs-actions-remote"), *argv],
        capture_output=True, text=True, check=False, env=merged)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_classify_auth_missing() -> None:
    code, detail = TRANSPORT._classify_gh_error(
        1, "", "gh: Not logged into any GitHub hosts")
    assert code == "auth_missing"


def test_classify_network_error() -> None:
    code, _ = TRANSPORT._classify_gh_error(
        1, "", "failed to connect: Could not resolve host github.com")
    assert code == "network_error"


def test_classify_timeout() -> None:
    code, _ = TRANSPORT._classify_gh_error(124, "", "gh timed out")
    assert code == "timeout"


def test_classify_missing_binary() -> None:
    code, _ = TRANSPORT._classify_gh_error(127, "", "gh executable not found")
    assert code == "gh_missing"


def test_probe_reports_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(TRANSPORT, "_gh", lambda argv, timeout: (1, "", "not logged in"))
    parser = TRANSPORT.build_parser()
    args = parser.parse_args(["probe"])
    assert TRANSPORT.cmd_probe(args) == 0


def test_ledger_claims_once_then_holds(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MNCS_ACTIONS_DISPATCH_LEDGER", str(tmp_path))
    first, problem = TRANSPORT._claim_ledger("abc123", "ses_one")
    assert problem == "" and first is not None
    assert first["session"] == "ses_one"
    second, problem = TRANSPORT._claim_ledger("abc123", "ses_two")
    assert second is None and problem == "held"


def test_ledger_expired_claim_can_be_superseded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MNCS_ACTIONS_DISPATCH_LEDGER", str(tmp_path))
    record, _ = TRANSPORT._claim_ledger("abc123", "ses_one")
    assert record is not None
    stale = dict(record, claimed_at=time.time() - TRANSPORT.LEDGER_TTL_SECONDS - 5)
    (tmp_path / "abc123.json").write_text(json.dumps(stale))
    record, problem = TRANSPORT._claim_ledger("abc123", "ses_two")
    assert problem == "" and record is not None
    assert record["session"] == "ses_two"
    assert record["superseded"] == "ses_one"


def test_dispatch_identity_is_stable() -> None:
    left = TRANSPORT._dispatch_identity("r", "w", "s", "i")
    right = TRANSPORT._dispatch_identity("r", "w", "s", "i")
    other = TRANSPORT._dispatch_identity("r", "w", "s2", "i")
    assert left == right and left != other


def test_dispatch_refuses_second_live_claim(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MNCS_ACTIONS_DISPATCH_LEDGER", str(tmp_path))
    calls: list[list[str]] = []

    def fake_gh(argv: list[str], timeout: int):
        calls.append(argv)
        if argv[0] == "api":
            return 0, '{"id":"1","path":".github/workflows/ci.yml"}\n', ""
        return 0, "", ""

    monkeypatch.setattr(TRANSPORT, "_gh", fake_gh)
    monkeypatch.setattr(TRANSPORT, "_resolve_run", lambda *a: {"run_id": 7})
    parser = TRANSPORT.build_parser()
    first = parser.parse_args(["dispatch", "--repo", "o/r", "--workflow", "ci.yml",
                                "--ref", "main", "--sha", "abc",
                                "--session", "ses_one"])
    assert TRANSPORT.cmd_dispatch(first) == 0
    assert any(argv[0] == "workflow" for argv in calls)
    calls.clear()
    second = parser.parse_args(["dispatch", "--repo", "o/r", "--workflow", "ci.yml",
                                 "--ref", "main", "--sha", "abc",
                                 "--session", "ses_two"])
    assert TRANSPORT.cmd_dispatch(second) == 0
    assert not any(argv[0] == "workflow" for argv in calls)


def test_fetch_normalizes_missing_artifact(tmp_path, monkeypatch,
                                         capsys) -> None:
    def fake_gh(argv: list[str], timeout: int):
        return 1, "", ("no artifact matches any of the names "
                       "or patterns provided")
    monkeypatch.setattr(TRANSPORT, "_gh", fake_gh)
    parser = TRANSPORT.build_parser()
    args = parser.parse_args(
        ["fetch", "--repo", "o/r", "--run-id", "7",
         "--artifact", "mncs-obligation-evidence",
         "--output-dir", str(tmp_path / "staged")])
    assert TRANSPORT.cmd_fetch(args) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["transport"] == "artifact_missing"


def _write_receipt_triple(target: Path, verdict: str = "PASS") -> None:
    import mncs_actions as actions
    receipt = actions.build_execution_receipt(
        command="probe", command_exit_code=0,
        claim_status=actions.CLAIM_ESTABLISHED,
        result_path="check-result.json", result_present=True,
        result_valid=True, result_errors=[], produced_files=[],
        inputs={}, provenance={"repository": "o/r", "commit": "abc",
                               "run_id": "1", "workflow": "CI"})
    (target / "execution-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True), encoding="utf-8")
    manifest = actions.build_evidence_manifest(
        verdict=verdict,
        result_sha256=__import__("hashlib").sha256(b"check").hexdigest(),
        receipt_ref={"path": "execution-receipt.json", "sha256":
                     __import__("hashlib").sha256(
                         (target / "execution-receipt.json").read_bytes()
                     ).hexdigest()},
        provenance=receipt["provenance"])
    (target / "evidence-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8")
    (target / "check-result.json").write_text(json.dumps({
        "schema_version": "mncs.check-result/1", "id": "ob.one",
        "provider": "o", "verdict": verdict}), encoding="utf-8")


def test_validate_accepts_well_formed_triple(tmp_path) -> None:
    _write_receipt_triple(tmp_path)
    out = _run_cli("validate", "--staged-dir", str(tmp_path))
    assert out["transport"] == "ok" and out["valid"] is True
    assert out["projected"]["verdict"] == "PASS"
    assert out["projected"]["check_id"] == "ob.one"


def test_validate_rejects_tampered_receipt(tmp_path) -> None:
    _write_receipt_triple(tmp_path)
    receipt_path = tmp_path / "execution-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["claim"]["verdict"] = "PASS"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    out = _run_cli("validate", "--staged-dir", str(tmp_path))
    assert out["transport"] == "ok" and out["valid"] is False
    assert any("sha256" in err for err in out["errors"])


def test_validate_rejects_missing_receipt(tmp_path) -> None:
    out = _run_cli("validate", "--staged-dir", str(tmp_path))
    assert out["transport"] == "ok" and out["valid"] is False


def test_emitter_maps_exit_code_to_verdict(tmp_path) -> None:
    emitter = ROOT / "scripts" / "emit_obligation_check.py"
    passing = tmp_path / "pass.json"
    completed = subprocess.run(
        [sys.executable, str(emitter), "--obligation", "ob.one",
         "--provider", "o", "--result-file", str(passing), "--",
         sys.executable, "-c", "pass"],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert json.loads(passing.read_text(encoding="utf-8"))["verdict"] == "PASS"
    failing = tmp_path / "fail.json"
    completed = subprocess.run(
        [sys.executable, str(emitter), "--obligation", "ob.one",
         "--provider", "o", "--result-file", str(failing), "--",
         sys.executable, "-c", "raise SystemExit(3)"],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert json.loads(failing.read_text(encoding="utf-8"))["verdict"] == "FAIL"
    missing = tmp_path / "missing.json"
    completed = subprocess.run(
        [sys.executable, str(emitter), "--obligation", "ob.one",
         "--provider", "o", "--result-file", str(missing), "--",
         "definitely-not-a-real-binary-xyz"],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    document = json.loads(missing.read_text(encoding="utf-8"))
    assert document["verdict"] == "UNKNOWN"
    assert document["unresolved"] == ["execution-launch"]
