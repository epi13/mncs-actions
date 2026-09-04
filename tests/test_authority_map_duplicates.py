"""Duplicate check_id policy for authority_map.py (mechanical, owns nothing).

Policy under test: a repeated declaration that is field-identical
(provider, semantic authority, repository attribution including its
absence) deduplicates deterministically; any repeat differing in any
field -- provider, authority, or repository attribution (changed, added,
or removed) -- is rejected with exit 2.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _descriptor(repository, check_id="dup.check", provider="prov", authority="auth.sem"):
    return {
        "repository": repository,
        "outputs": [
            {
                "check_id": check_id,
                "provider": provider,
                "roles": {"semantic_authority": authority},
            }
        ],
    }


def _run(tmp_path: Path, descriptors: list) -> subprocess.CompletedProcess:
    src = tmp_path / "descriptors.json"
    src.write_text(json.dumps({"descriptors": descriptors}), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable, str(SCRIPTS / "authority_map.py"),
            "--descriptors", str(src),
            "--output", str(tmp_path / "authority-map.json"),
        ],
        capture_output=True, text=True, timeout=60,
    )


def _map(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "authority-map.json").read_text(encoding="utf-8"))


def test_identical_duplicate_binding_deduplicates_deterministically(tmp_path: Path):
    proc = _run(tmp_path, [_descriptor("epi13/producer"), _descriptor("epi13/producer")])
    assert proc.returncode == 0, proc.stderr
    first = _map(tmp_path)["authorities"]["dup.check"]
    assert first == {
        "provider": "prov",
        "authority": "auth.sem",
        "repository": "epi13/producer",
    }
    proc2 = _run(tmp_path, [_descriptor("epi13/producer")] * 3)
    assert proc2.returncode == 0, proc2.stderr
    assert _map(tmp_path)["authorities"]["dup.check"] == first


def test_identical_duplicate_without_repository_deduplicates(tmp_path: Path):
    proc = _run(tmp_path, [_descriptor(""), _descriptor("")])
    assert proc.returncode == 0, proc.stderr
    assert _map(tmp_path)["authorities"]["dup.check"] == {
        "provider": "prov",
        "authority": "auth.sem",
    }


def test_conflicting_repository_is_rejected(tmp_path: Path):
    proc = _run(tmp_path, [_descriptor("epi13/a"), _descriptor("epi13/b")])
    assert proc.returncode == 2
    assert "repository attribution" in proc.stderr


def test_repository_cannot_silently_disappear(tmp_path: Path):
    proc = _run(tmp_path, [_descriptor("epi13/a"), _descriptor("")])
    assert proc.returncode == 2
    assert "repository attribution" in proc.stderr


def test_repository_cannot_silently_appear(tmp_path: Path):
    proc = _run(tmp_path, [_descriptor(""), _descriptor("epi13/a")])
    assert proc.returncode == 2
    assert "repository attribution" in proc.stderr


def test_different_provider_is_rejected(tmp_path: Path):
    proc = _run(
        tmp_path,
        [_descriptor("epi13/a", provider="prov"), _descriptor("epi13/a", provider="other")],
    )
    assert proc.returncode == 2
    assert "provider" in proc.stderr


def test_different_semantic_authority_is_rejected(tmp_path: Path):
    proc = _run(
        tmp_path,
        [_descriptor("epi13/a", authority="auth.one"), _descriptor("epi13/a", authority="auth.two")],
    )
    assert proc.returncode == 2
    assert "semantic authority" in proc.stderr
