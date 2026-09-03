"""Revision coherence: workflow revision X must execute action revision X.

A historical workflow revision must not silently execute a newer action
implementation. GitHub Actions requires literal `uses:` refs and resolves
`./actions/...` against the caller repo, so the reusable workflow pins its
own actions with synchronized immutable SHAs (see
docs/revision-coherence.md and scripts/sync-pins.sh).

These tests detect version skew and floating pins.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "mncs-family-verify.yml"
SYNC_SCRIPT = REPO / "scripts" / "sync-pins.sh"

SELF_REF = re.compile(r"epi13/mncs-actions/actions/(?P<name>[^@\s]+)@(?P<ref>[^\s\"']+)")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
FLOATING = re.compile(r"@(main|master|latest|stable|v\d+.*|beta|alpha)(?=[\s\"']|$)")


def uses_self_refs():
    refs = []
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("uses:"):
            continue
        match = SELF_REF.search(line)
        if match:
            refs.append((match.group("name"), match.group("ref")))
    return refs


def test_internal_pins_present():
    refs = uses_self_refs()
    names = [name for name, _ in refs]
    assert names.count("run-check") == 3, names
    assert names.count("aggregate") == 1, names
    assert names.count("render-badge") == 1, names


def test_no_floating_branch_or_tag_pins():
    refs = uses_self_refs()
    assert refs, "no internal self-references found"
    for name, ref in refs:
        assert not FLOATING.search(f"@{ref}"), f"{name} floats on @{ref}"
        assert FULL_SHA.match(ref), f"{name} pin is not an immutable full SHA: {ref!r}"


def test_all_executable_remote_action_refs_are_immutable():
    proc = subprocess.run(
        ["python3", str(REPO / "scripts" / "check-action-pins.py")],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr


def test_no_caller_relative_action_paths():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "./actions/run-check" not in text
    assert "./actions/aggregate" not in text


def test_all_internal_pins_identical():
    refs = uses_self_refs()
    pins = {ref for _, ref in refs}
    assert len(pins) == 1, f"version skew between internal actions: {refs}"


def test_sync_script_reproduces_baked_pins(tmp_path):
    refs = uses_self_refs()
    baked = refs[0][1]
    assert FULL_SHA.match(baked)
    before = WORKFLOW.read_bytes()
    binding_before = (REPO / "revision-binding.json").read_bytes()
    proc = subprocess.run(
        ["bash", str(SYNC_SCRIPT), baked],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    assert WORKFLOW.read_bytes() == before, "sync script is not idempotent for baked pins"
    assert (REPO / "revision-binding.json").read_bytes() == binding_before


def test_sync_script_rejects_floating_input():
    for bad in ("main", "v1", "abc123", "2a0df3c"):
        proc = subprocess.run(
            ["bash", str(SYNC_SCRIPT), bad],
            capture_output=True, text=True, cwd=str(REPO),
        )
        assert proc.returncode != 0, bad


def test_examples_never_float_on_main():
    for name in ("family.yml", "aggregate.yml", "basic.yml"):
        path = REPO / "examples" / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "mncs-actions/actions/run-check@main" not in text, name
        assert "mncs-actions/actions/aggregate@main" not in text, name
        assert "mncs-family-verify.yml@main" not in text, name


def test_release_doc_exists():
    assert (REPO / "docs" / "revision-coherence.md").is_file()
    assert SYNC_SCRIPT.is_file()


def test_revision_binding_matches_baked_pin():
    import json

    binding = json.loads((REPO / "revision-binding.json").read_text(encoding="utf-8"))
    refs = uses_self_refs()
    assert binding["implementation_revision"] == refs[0][1]
    assert binding["actions"] == [
        "actions/aggregate",
        "actions/render-badge",
        "actions/run-check",
    ]
