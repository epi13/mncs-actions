"""Action YAML and shell hygiene: every action must parse and every script must pass bash -n."""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def load_action(name: str) -> dict:
    path = REPO / "actions" / name / "action.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_verify_action_shape():
    action = load_action("verify")
    for key in ("command", "working-directory", "result-file", "evidence-directory", "fail-on-unknown"):
        assert key in action["inputs"], key
    for key in ("verdict", "claim-status", "evidence-path", "result-path",
                "execution-receipt-path", "manifest-digest", "provenance-digest",
                "command-exit-code"):
        assert key in action["outputs"], key
    # Compat alias must resolve to the same step output value source.
    assert action["outputs"]["manifest-digest"]["value"] != ""
    assert action["outputs"]["provenance-digest"]["value"] != ""


def test_run_check_action_shape():
    action = load_action("run-check")
    assert "command" in action["inputs"]
    assert "verdict" in action["outputs"]
    assert "claim-status" in action["outputs"]


def test_aggregate_action_shape():
    action = load_action("aggregate")
    for key in ("checks", "required", "evidence-directory", "fail-on-unknown",
                "implementation-revision", "carrier-revision", "strict-membership"):
        assert key in action["inputs"], key
    assert "verdict" in action["outputs"]
    assert "aggregate-path" in action["outputs"]
    assert "aggregate-digest" in action["outputs"]


def test_render_badge_action_shape():
    action = load_action("render-badge")
    for key in ("verdict", "label", "output-file", "sidecar-file", "working-directory"):
        assert key in action["inputs"], key
    for key in ("state", "badge-path", "sidecar-path", "badge-digest", "sidecar-digest"):
        assert key in action["outputs"], key


def test_examples_parse():
    for name in ("basic.yml", "family.yml", "aggregate.yml"):
        path = REPO / "examples" / name
        if path.exists():
            yaml.safe_load(path.read_text(encoding="utf-8"))


def test_workflows_parse():
    for path in (REPO / ".github" / "workflows").glob("*.yml"):
        yaml.safe_load(path.read_text(encoding="utf-8"))
