"""Deterministic badge projection and revision-binding coverage."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mncs_actions as lib

REPO = Path(__file__).resolve().parents[1]
RENDER = REPO / "actions" / "render-badge" / "render_badge.sh"


def outputs(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value
    return result


def badge_env(tmp_path: Path, verdict: str = "PASS") -> tuple[dict[str, str], Path]:
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "GITHUB_OUTPUT": str(output),
            "MNCS_BADGE_VERDICT": verdict,
            "MNCS_BADGE_LABEL": "MNCS <family>",
            "MNCS_BADGE_OUTPUT_FILE": str(tmp_path / "badge.svg"),
            "MNCS_BADGE_SIDECAR_FILE": str(tmp_path / "badge.json"),
            "MNCS_BADGE_REPOSITORY": "epi13/example",
            "MNCS_BADGE_SUBJECT_COMMIT": "subject-sha",
            "MNCS_BADGE_BOUNDARY": "family",
            "MNCS_BADGE_AGGREGATE_DIGEST": "a" * 64,
            "MNCS_BADGE_MANIFEST_DIGEST": "b" * 64,
            "MNCS_CARRIER_REVISION": "carrier-sha",
            "MNCS_IMPLEMENTATION_REVISION": "",
        }
    )
    return env, output


def test_badge_projection_is_closed():
    assert [lib.project_badge_state(state) for state in lib.BADGE_STATES] == list(lib.BADGE_STATES)
    assert lib.project_badge_state("NOT_ESTABLISHED") is None
    assert lib.project_badge_state(1) is None


def test_badge_doc_is_deterministic_and_schema_valid():
    kwargs = {
        "label": "MNCS",
        "verdict": "UNKNOWN",
        "repository": "epi13/example",
        "subject_commit": "abc",
        "boundary": "family",
        "aggregate_digest": "a" * 64,
        "manifest_digest": "b" * 64,
        "carrier_revision": "carrier",
        "implementation_revision": "c" * 40,
    }
    first = lib.build_badge_doc(**kwargs)
    second = lib.build_badge_doc(**kwargs)
    assert first == second
    assert lib.validate_badge(first) == []
    assert b"generated" not in lib.canonical_bytes(first)


def test_badge_svg_escapes_label_and_is_deterministic():
    svg = lib.render_badge_svg("A & <B>", "PASS")
    assert svg == lib.render_badge_svg("A & <B>", "PASS")
    assert "A &amp; &lt;B&gt;" in svg
    assert "#4c1" in svg
    assert svg.endswith("\n")


def test_render_badge_writes_svg_sidecar_and_outputs(tmp_path):
    env, output = badge_env(tmp_path)
    proc = subprocess.run([str(RENDER)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    result = outputs(output)
    assert result["state"] == "PASS"
    assert Path(result["badge-path"]).is_file()
    assert Path(result["sidecar-path"]).is_file()
    sidecar = json.loads((tmp_path / "badge.json").read_text(encoding="utf-8"))
    assert sidecar["verdict"] == "PASS"
    assert sidecar["revisions"]["implementation_revision"] == "dceb839c097b328f23785ab40d35016832a79f6d"
    assert lib.validate_badge(sidecar) == []


def test_render_badge_supports_invalid_presentation_state(tmp_path):
    env, output = badge_env(tmp_path, "INVALID")
    proc = subprocess.run([str(RENDER)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert outputs(output)["state"] == "INVALID"
    assert json.loads((tmp_path / "badge.json").read_text(encoding="utf-8"))["verdict"] == "INVALID"


def test_render_badge_rejects_unknown_state(tmp_path):
    env, _ = badge_env(tmp_path, "NOT_ESTABLISHED")
    proc = subprocess.run([str(RENDER)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 2
    assert "must be PASS, FAIL, UNKNOWN, or INVALID" in proc.stderr


def test_revision_resolution_rejects_disagreement(tmp_path):
    (tmp_path / "revision-binding.json").write_text(
        json.dumps({"implementation_revision": "a" * 40}), encoding="utf-8"
    )
    revision, warnings, error = lib.resolve_implementation_revision("b" * 40, tmp_path)
    assert revision is None
    assert warnings == []
    assert "disagrees" in error


def test_carrier_revision_rejects_whitespace(tmp_path):
    env, _ = badge_env(tmp_path)
    env["MNCS_CARRIER_REVISION"] = "carrier revision"
    proc = subprocess.run([str(RENDER)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 2
    assert "carrier_revision" in proc.stderr
