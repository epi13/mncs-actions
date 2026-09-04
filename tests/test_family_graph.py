"""Family graph advancement: identity, coherence, cycles, graph subjects.

Covers scripts/family_graph.py, scripts/family_coherence.py, and the
graph-subject shape validation in lib/mncs_actions.py. Attack vectors:
member reorder, revision tampering without digest update, base mismatch,
moving refs, duplicate members, divergent pins, unmapped surfaces,
sole-self authority, unknown authorities, malformed graph subjects.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mncs_actions as lib
from family_coherence import _extract_pins, _impact
from family_graph import (
    GraphError,
    cmd_advance,
    cmd_build,
    cmd_verify,
    graph_digest,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ACTIONS = Path(__file__).resolve().parents[1]

HEX_A = "a" * 40
HEX_B = "b" * 40
HEX_C = "c" * 40


def _ns(**kwargs):
    return type("Args", (), kwargs)()


def _candidate(tmp_path: Path) -> Path:
    doc = {
        "schema_version": "mncs-actions.family-contract-candidate/1",
        "source": "moving-head",
        "branch": "main",
        "base_schema_version": "mncs-actions.family-contracts/1",
        "base_contract_digest": "0" * 64,
        "repositories": [
            {
                "name": "mncs-standard",
                "repository": "epi13/machine-native-complexity-standard",
                "branch": "main",
                "base_revision": HEX_A,
                "candidate_revision": HEX_B,
            },
            {
                "name": "mncds",
                "repository": "epi13/machine-native-complexity-development-specification",
                "branch": "main",
                "base_revision": HEX_A,
                "candidate_revision": HEX_A,
            },
        ],
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(doc))
    return path


def _fixed() -> dict:
    return {
        "schema_version": "mncs-actions.family-contracts/1",
        "repositories": [
            {"name": "mncs-standard", "repository": "epi13/x", "revision": HEX_A},
            {"name": "mncds", "repository": "epi13/y", "revision": HEX_A},
        ],
    }


def _descriptors() -> dict:
    return {
        "descriptors": [
            {
                "producer": "mncs-standard",
                "contract": {"id": "mncs-validator-rs", "revision": "0.2"},
                "outputs": [
                    {"check_id": "mncs-validation", "contract_revision": "0.2"}
                ],
            },
            {
                "producer": "mncds",
                "contract": {"id": "mncds", "revision": "0.1"},
                "outputs": [
                    {
                        "check_id": "mncds-development-record",
                        "contract_revision": "0.2-alpha.1",
                    }
                ],
            },
        ]
    }


def _write(tmp_path: Path, name: str, doc: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc))
    return path


def _build(tmp_path: Path, **overrides) -> dict:
    candidate = _candidate(tmp_path)
    fixed = _fixed()
    doc = json.loads(candidate.read_text())
    doc["base_contract_digest"] = lib.sha256_hex(lib.canonical_bytes(fixed))
    candidate.write_text(json.dumps(doc))
    fixed_path = _write(tmp_path, "fixed.json", fixed)
    desc_path = _write(tmp_path, "desc.json", _descriptors())
    out = tmp_path / "graph.json"
    args = {
        "candidate": str(candidate),
        "fixed": str(fixed_path),
        "descriptors": str(desc_path),
        "graph_id": "family-graph-9",
        "actions_revision": HEX_C,
        "accepted_graph": "",
        "coherence": "",
        "evidence_dir": "",
        "promotion_result": "",
        "commons_record": "",
        "boundary": "",
        "provenance_generator": "test",
        "generated_at": "2026-09-04T00:00:00Z",
        "output": str(out),
    }
    args.update(overrides)
    assert cmd_build(_ns(**args)) == 0
    return json.loads(out.read_text())


def test_graph_digest_is_deterministic(tmp_path: Path):
    first = _build(tmp_path)
    out2 = tmp_path / "graph2.json"
    candidate = _candidate(tmp_path)
    fixed = _fixed()
    doc = json.loads(candidate.read_text())
    doc["base_contract_digest"] = lib.sha256_hex(lib.canonical_bytes(fixed))
    candidate.write_text(json.dumps(doc))
    fixed_path = _write(tmp_path, "fixed.json", fixed)
    desc_path = _write(tmp_path, "desc.json", _descriptors())
    cmd_build(
        _ns(
            candidate=str(candidate),
            fixed=str(fixed_path),
            descriptors=str(desc_path),
            graph_id="family-graph-9",
            actions_revision=HEX_C,
            accepted_graph="",
            coherence="",
            evidence_dir="",
            promotion_result="",
            commons_record="",
            boundary="",
            provenance_generator="test",
            generated_at="2026-09-04T18:00:00Z",
            output=str(out2),
        )
    )
    second = json.loads(out2.read_text())
    assert first["digest"] == second["digest"]
    assert first["digest"] == graph_digest(first)


def test_member_reorder_preserves_identity(tmp_path: Path):
    graph = _build(tmp_path)
    reordered = dict(graph)
    reordered["members"] = list(reversed(graph["members"]))
    assert graph_digest(reordered) == graph["digest"]


def test_revision_tamper_changes_identity(tmp_path: Path):
    graph = _build(tmp_path)
    tampered = json.loads(json.dumps(graph))
    tampered["members"][0]["commit"] = HEX_C
    assert graph_digest(tampered) != graph["digest"]


def test_dependency_tamper_changes_identity(tmp_path: Path):
    graph = _build(tmp_path)
    assert graph["dependencies"], "test graph must carry a compat edge"
    tampered = json.loads(json.dumps(graph))
    tampered["dependencies"][0]["target"] = HEX_C
    assert graph_digest(tampered) != graph["digest"]


def test_wrong_base_graph_refuses_build(tmp_path: Path):
    candidate = _candidate(tmp_path)
    fixed_path = _write(tmp_path, "fixed.json", _fixed())
    desc_path = _write(tmp_path, "desc.json", _descriptors())
    with pytest.raises(GraphError):
        cmd_build(
            _ns(
                candidate=str(candidate),
                fixed=str(fixed_path),
                descriptors=str(desc_path),
                graph_id="g",
                actions_revision=HEX_C,
                accepted_graph="",
                coherence="",
                evidence_dir="",
                promotion_result="",
                commons_record="",
                boundary="",
                provenance_generator="t",
                generated_at="t",
                output=str(tmp_path / "g.json"),
            )
        )


def test_base_mismatch_refuses_build(tmp_path: Path):
    candidate = _candidate(tmp_path)
    fixed = _fixed()
    fixed["repositories"][0]["revision"] = HEX_C
    fixed_path = _write(tmp_path, "fixed.json", fixed)
    desc_path = _write(tmp_path, "desc.json", _descriptors())
    with pytest.raises(GraphError):
        cmd_build(
            _ns(
                candidate=str(candidate),
                fixed=str(fixed_path),
                descriptors=str(desc_path),
                graph_id="g",
                actions_revision=HEX_C,
                accepted_graph="",
                coherence="",
                evidence_dir="",
                promotion_result="",
                commons_record="",
                boundary="",
                provenance_generator="t",
                generated_at="t",
                output=str(tmp_path / "g.json"),
            )
        )


def test_moving_ref_member_refuses_build(tmp_path: Path):
    candidate = _candidate(tmp_path)
    doc = json.loads(candidate.read_text())
    doc["repositories"][0]["candidate_revision"] = "main"
    candidate.write_text(json.dumps(doc))
    fixed_path = _write(tmp_path, "fixed.json", _fixed())
    desc_path = _write(tmp_path, "desc.json", _descriptors())
    with pytest.raises(GraphError):
        cmd_build(
            _ns(
                candidate=str(candidate),
                fixed=str(fixed_path),
                descriptors=str(desc_path),
                graph_id="g",
                actions_revision=HEX_C,
                accepted_graph="",
                coherence="",
                evidence_dir="",
                promotion_result="",
                commons_record="",
                boundary="",
                provenance_generator="t",
                generated_at="t",
                output=str(tmp_path / "g.json"),
            )
        )


def test_verify_detects_digest_tamper(tmp_path: Path):
    graph = _build(tmp_path)
    graph["members"][0]["commit"] = HEX_C
    path = _write(tmp_path, "tampered.json", graph)
    fixed_path = _write(tmp_path, "fixed.json", _fixed())
    with pytest.raises(GraphError):
        cmd_verify(
            _ns(
                graph=str(path),
                fixed=str(fixed_path),
                promotion_result="",
                lib_dir="",
                coherence="",
                commons_record="",
                commons_root="",
            )
        )


def test_advance_refuses_without_proof(tmp_path: Path):
    graph = _build(tmp_path)
    path = _write(tmp_path, "graph.json", graph)
    fixed_path = _write(tmp_path, "fixed.json", _fixed())
    coherence = {
        "graph_digest": graph["digest"],
        "movements": [],
        "blockers": [],
        "cycles": {"unsafe": False},
    }
    coh_path = _write(tmp_path, "coh.json", coherence)
    code = cmd_advance(
        _ns(
            graph=str(path),
            fixed=str(fixed_path),
            promotion_result="",
            lib_dir="",
            coherence=str(coh_path),
            commons_record="x",
            commons_root="",
            output_contracts=str(tmp_path / "p.json"),
            output_graph=str(tmp_path / "a.json"),
        )
    )
    assert code == 2


def test_impact_lattice():
    rows = [
        {"paths": ["actions/", "lib/"], "impact": "executable"},
        {"paths": ["schemas/"], "impact": "contract"},
        {"paths": ["scripts/"], "impact": "evidence"},
        {"paths": ["docs/"], "impact": "docs"},
        {"paths": ["tests/"], "impact": "none"},
    ]
    impacts = {
        "executable": "REQUIRED",
        "contract": "REQUIRED",
        "evidence": "OPTIONAL",
        "docs": "NOT_REQUIRED",
        "none": "NOT_REQUIRED",
    }
    assert _impact([], rows, impacts) == "CURRENT"
    assert _impact(["docs/a.md"], rows, impacts) == "NOT_REQUIRED"
    assert _impact(["tests/t.py"], rows, impacts) == "NOT_REQUIRED"
    assert _impact(["scripts/x.py"], rows, impacts) == "OPTIONAL"
    assert _impact(["lib/y.py"], rows, impacts) == "REQUIRED"
    assert _impact(["schemas/z.json"], rows, impacts) == "REQUIRED"
    assert _impact(["docs/a.md", "lib/y.py"], rows, impacts) == "REQUIRED"
    assert _impact(["scripts/x.py", "docs/a.md"], rows, impacts) == "OPTIONAL"
    assert _impact(["brand-new/thing.py"], rows, impacts) == "UNKNOWN"


def test_extract_pins_agrees_or_lists(tmp_path: Path):
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / "promotion.yml").write_text(
        "uses: epi13/mncs-actions/.github/workflows/mncs-family-verify.yml@"
        + HEX_A
        + "\nMNC_ACTIONS_PIN: "
        + HEX_A
        + "\n"
    )
    spec = {
        "files": ["promotion.yml"],
        "patterns": [
            r"mncs-family-verify\.yml@(?P<sha>[0-9a-f]{40})",
            r"MNC_ACTIONS_PIN:\s*(?P<sha>[0-9a-f]{40})",
        ],
    }
    assert _extract_pins(checkout, spec) == {HEX_A}
    (checkout / "promotion.yml").write_text(
        "uses: epi13/mncs-actions/.github/workflows/mncs-family-verify.yml@"
        + HEX_A
        + "\nMNC_ACTIONS_PIN: "
        + HEX_B
        + "\n"
    )
    assert _extract_pins(checkout, spec) == {HEX_A, HEX_B}


def _git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=30)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t"], check=True, timeout=30
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "t"], check=True, timeout=30
    )


def _commit(path: Path, name: str, content: str) -> str:
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, timeout=30)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-qm", name], check=True, timeout=30
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def _coherence_graph(tmp_path: Path, consumer_rev: str, target_rev: str) -> Path:
    graph = {
        "schema_version": "mncs-actions.family-candidate-graph/1",
        "graph_id": "g",
        "digest": "d" * 64,
        "base": {"graph_id": "b", "digest": "e" * 64, "contract_digest": "f" * 64},
        "members": [
            {
                "name": "consumer",
                "repository": "epi13/consumer",
                "commit": consumer_rev,
                "changed": True,
                "contracts": {},
            },
            {
                "name": "target",
                "repository": "epi13/target",
                "commit": target_rev,
                "changed": True,
                "contracts": {},
            },
        ],
        "dependencies": [],
        "evidence_requirements": {"required": [], "optional": []},
        "status": "candidate",
    }
    return _write(tmp_path, "coh-graph.json", graph)


def _run_coherence(
    tmp_path: Path,
    capability: dict,
    pin_template: str,
    target_extra: list[tuple[str, str]] | None = None,
) -> dict:
    from family_coherence import cmd_coherence

    consumer = tmp_path / "consumer"
    target = tmp_path / "target"
    _git_repo(consumer)
    _git_repo(target)
    old = _commit(target, "base.txt", "v1")
    for name, content in target_extra or []:
        _commit(target, name, content)
    new = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    (consumer / "pin.yml").write_text(pin_template.format(sha=old))
    graph_path = _coherence_graph(tmp_path, new, new)
    cap_path = _write(tmp_path, "cap.json", capability)
    out = tmp_path / "coh.json"
    code = cmd_coherence(
        _ns(
            graph=str(graph_path),
            checkout=[f"consumer={consumer}", f"target={target}"],
            capability=str(cap_path),
            descriptors=str(_write(tmp_path, "d.json", {"descriptors": []})),
            authority_map="",
            boundary="",
            check_id="promotion-boundary",
            output=str(out),
        )
    )
    assert code == 0
    return json.loads(out.read_text())


def _capability(pins: list[dict], rows: list[dict]) -> dict:
    return {
        "impacts": {
            "executable": "REQUIRED",
            "contract": "REQUIRED",
            "evidence": "OPTIONAL",
            "docs": "NOT_REQUIRED",
            "none": "NOT_REQUIRED",
        },
        "pins": {"epi13/consumer": pins},
        "capabilities": {"epi13/target": rows},
    }


def _transport_capability() -> dict:
    return _capability(
        [
            {
                "edge": "transport",
                "target": "epi13/target",
                "files": ["pin.yml"],
                "patterns": [r"PIN:\s*(?P<sha>[0-9a-f]{40})"],
            }
        ],
        [
            {"paths": ["src/"], "impact": "executable"},
            {"paths": ["scripts/"], "impact": "evidence"},
            {"paths": ["docs/"], "impact": "docs"},
        ],
    )


def test_coherence_docs_delta_not_required(tmp_path: Path):
    report = _run_coherence(
        tmp_path,
        _transport_capability(),
        "PIN: {sha}\n",
        target_extra=[("docs/note.md", "words")],
    )
    assert len(report["movements"]) == 1
    movement = report["movements"][0]
    assert movement["classification"] == "NOT_REQUIRED"
    assert movement["satisfied"] is True
    assert report["blockers"] == []


def test_coherence_executable_delta_required(tmp_path: Path):
    report = _run_coherence(
        tmp_path,
        _transport_capability(),
        "PIN: {sha}\n",
        target_extra=[("src/code.py", "x = 1")],
    )
    movement = report["movements"][0]
    assert movement["classification"] == "REQUIRED"
    assert movement["satisfied"] is False
    # REQUIRED is ordered follow-up pressure, not a refusal by itself.
    assert report["blockers"] == []


def test_coherence_evidence_delta_optional(tmp_path: Path):
    report = _run_coherence(
        tmp_path,
        _transport_capability(),
        "PIN: {sha}\n",
        target_extra=[("scripts/tool.py", "pass")],
    )
    movement = report["movements"][0]
    assert movement["classification"] == "OPTIONAL"
    assert movement["satisfied"] is True


def test_coherence_unmapped_delta_unknown(tmp_path: Path):
    report = _run_coherence(
        tmp_path,
        _transport_capability(),
        "PIN: {sha}\n",
        target_extra=[("brand-new/thing.py", "pass")],
    )
    movement = report["movements"][0]
    assert movement["classification"] == "UNKNOWN"
    assert movement["satisfied"] is False


def test_coherence_divergent_pins_block(tmp_path: Path):
    from family_coherence import cmd_coherence

    consumer = tmp_path / "consumer"
    target = tmp_path / "target"
    _git_repo(consumer)
    _git_repo(target)
    rev = _commit(target, "base.txt", "v1")
    (consumer / "a.yml").write_text(f"PIN: {rev}\n")
    (consumer / "b.yml").write_text(f"PIN: {'d' * 40}\n")
    capability = _capability(
        [
            {
                "edge": "transport",
                "target": "epi13/target",
                "files": ["a.yml", "b.yml"],
                "patterns": [r"PIN:\s*(?P<sha>[0-9a-f]{40})"],
            }
        ],
        [{"paths": ["src/"], "impact": "executable"}],
    )
    graph_path = _coherence_graph(tmp_path, rev, rev)
    cap_path = _write(tmp_path, "cap.json", capability)
    out = tmp_path / "coh.json"
    code = cmd_coherence(
        _ns(
            graph=str(graph_path),
            checkout=[f"consumer={consumer}", f"target={target}"],
            capability=str(cap_path),
            descriptors=str(_write(tmp_path, "d.json", {"descriptors": []})),
            authority_map="",
            boundary="",
            check_id="promotion-boundary",
            output=str(out),
        )
    )
    assert code == 0
    report = json.loads(out.read_text())
    assert any("divergent" in blocker for blocker in report["blockers"])


def test_rebuild_with_coherence_preserves_identity(tmp_path: Path):
    """Evidence accumulation must never mutate graph identity.

    Regression: coherence-derived pin edges used to fold into the
    digested dependencies, so the skeleton -> validated rebuild changed
    the digest and broke the advancement chain.
    """
    plain = _build(tmp_path)
    coherence = {
        "graph_digest": plain["digest"],
        "edges": [
            {
                "from": "consumer",
                "to": "target",
                "edge": "transport",
                "target": "a" * 40,
            }
        ],
        "blockers": [],
    }
    coh_path = _write(tmp_path, "coh.json", coherence)
    enriched = _build(tmp_path, coherence=str(coh_path))
    assert enriched["digest"] == plain["digest"]
    assert enriched["pin_edges"] == coherence["edges"]
    assert enriched["status"] == "candidate"


def test_commons_references_use_owner_schema_fields(tmp_path: Path):
    """Commons references must stay inside the owner allowlist.

    Regression: the script emitted a ``recordVersion`` field the
    owner-native Commons ``normalize_producer_reference`` rejects,
    breaking the relate step of a real lifecycle run.
    """
    import sys
    import types

    captured: dict = {}
    family_module = types.ModuleType("mncs_commons.family")

    def fake_make_changeset_record(**kwargs):
        captured.update(kwargs)
        return {"kind": "changeset", "details": {"changesetId": kwargs["changeset_id"]}}

    family_module.make_changeset_record = fake_make_changeset_record
    validation_module = types.ModuleType("mncs_commons.validation")
    validation_module.validate_record = lambda record: types.SimpleNamespace(
        valid=True, diagnostics=[]
    )
    package = types.ModuleType("mncs_commons")
    sys.modules["mncs_commons"] = package
    sys.modules["mncs_commons.family"] = family_module
    sys.modules["mncs_commons.validation"] = validation_module
    try:
        sys.path.insert(0, str(SCRIPTS))
        import family_commons_record

        graph = {
            "graph_id": "g",
            "digest": "d" * 64,
            "base": {"digest": "e" * 64},
            "members": [
                {
                    "name": "m",
                    "repository": "epi13/m",
                    "commit": "a" * 40,
                    "changed": False,
                    "contracts": {},
                }
            ],
            "evidence": [
                {"check_id": "probe", "path": "probe.json", "digest": "f" * 64}
            ],
            "provenance": {"generated_at": "2026-09-04T00:00:00Z"},
        }
        checks_dir = tmp_path / "checks"
        checks_dir.mkdir()
        (checks_dir / "probe.json").write_text(
            json.dumps(
                {
                    "provider": "probe-authority",
                    "contract_revision": "0.1",
                    "subject": {"repository": "epi13/m", "commit": "a" * 40},
                }
            )
        )
        obligations_dir = tmp_path / "obligations"
        obligations_dir.mkdir()
        (obligations_dir / "o.json").write_text(
            json.dumps(
                {
                    "obligation_key": "pressure.m.test",
                    "subject": {"repository": "epi13/m", "commit": "a" * 40},
                }
            )
        )
        promotion = tmp_path / "promotion.json"
        promotion.write_text(json.dumps({"verdict": "PASS"}))
        code = family_commons_record.main(
            [
                "--graph",
                str(_write(tmp_path, "graph.json", graph)),
                "--checks-dir",
                str(checks_dir),
                "--promotion-result",
                str(promotion),
                "--obligations-dir",
                str(obligations_dir),
                "--commons-root",
                str(tmp_path),
                "--output",
                str(tmp_path / "record.json"),
            ]
        )
        assert code == 0
    finally:
        sys.modules.pop("mncs_commons", None)
        sys.modules.pop("mncs_commons.family", None)
        sys.modules.pop("mncs_commons.validation", None)
        sys.path.remove(str(SCRIPTS))
    allowed = {
        "schema",
        "producer",
        "recordKind",
        "schemaVersion",
        "stableId",
        "contentDigest",
        "artifact",
        "scope",
    }
    references = list(captured["supports"]) + list(captured["promotes"])
    assert references
    for reference in references:
        assert set(reference) <= allowed, set(reference) - allowed


def test_capability_covers_tracked_actions_files():
    """Every tracked mncs-actions file must match a capability row.

    An unmapped path would make a future transport delta unclassifiable,
    failing the family lifecycle closed. This test forces the map to grow
    with the repository.
    """
    import subprocess

    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.splitlines()
    capability = json.loads((root / "family-capability.json").read_text())
    rows = capability["capabilities"]["epi13/mncs-actions"]
    prefixes = [prefix for row in rows for prefix in row["paths"]]
    unmapped = [
        path
        for path in tracked
        if not any(path == prefix or path.startswith(prefix) for prefix in prefixes)
    ]
    assert unmapped == []


def test_floor_satisfied_by_descendant(tmp_path: Path):
    from family_coherence import cmd_coherence

    target = tmp_path / "target"
    _git_repo(target)
    floor = _commit(target, "base.txt", "v1")
    member = _commit(target, "next.txt", "v2")
    graph = {
        "schema_version": "mncs-actions.family-candidate-graph/1",
        "graph_id": "g",
        "digest": "d" * 64,
        "base": {"graph_id": "b", "digest": "e" * 64, "contract_digest": "f" * 64},
        "members": [
            {
                "name": "target",
                "repository": "epi13/target",
                "commit": member,
                "changed": True,
                "contracts": {},
            },
        ],
        "dependencies": [],
        "evidence_requirements": {"required": [], "optional": []},
        "status": "candidate",
    }
    capability = {
        "impacts": {},
        "pins": {},
        "capabilities": {},
        "floors": [
            {
                "id": "feature.x",
                "repo": "epi13/target",
                "min_revision": floor,
                "reason": "test floor",
            }
        ],
    }
    out = tmp_path / "coh.json"
    code = cmd_coherence(
        _ns(
            graph=str(_write(tmp_path, "g.json", graph)),
            checkout=[f"target={target}"],
            capability=str(_write(tmp_path, "cap.json", capability)),
            descriptors=str(_write(tmp_path, "d.json", {"descriptors": []})),
            authority_map="",
            boundary="",
            check_id="promotion-boundary",
            output=str(out),
        )
    )
    assert code == 0
    report = json.loads(out.read_text())
    assert report["floors"] == [
        {
            "id": "feature.x",
            "repo": "epi13/target",
            "status": "satisfied",
            "reason": "test floor",
        }
    ]
    assert report["blockers"] == []


def test_floor_violation_blocks(tmp_path: Path):
    from family_coherence import cmd_coherence

    target = tmp_path / "target"
    _git_repo(target)
    member = _commit(target, "base.txt", "v1")
    _commit(target, "next.txt", "v2")
    graph = {
        "schema_version": "mncs-actions.family-candidate-graph/1",
        "graph_id": "g",
        "digest": "d" * 64,
        "base": {"graph_id": "b", "digest": "e" * 64, "contract_digest": "f" * 64},
        "members": [
            {
                "name": "target",
                "repository": "epi13/target",
                "commit": member,
                "changed": False,
                "contracts": {},
            },
        ],
        "dependencies": [],
        "evidence_requirements": {"required": [], "optional": []},
        "status": "candidate",
    }
    capability = {
        "impacts": {},
        "pins": {},
        "capabilities": {},
        "floors": [
            {
                "id": "feature.x",
                "repo": "epi13/target",
                "min_revision": "f" * 40,
                "reason": "test floor",
            }
        ],
    }
    out = tmp_path / "coh.json"
    code = cmd_coherence(
        _ns(
            graph=str(_write(tmp_path, "g.json", graph)),
            checkout=[f"target={target}"],
            capability=str(_write(tmp_path, "cap.json", capability)),
            descriptors=str(_write(tmp_path, "d.json", {"descriptors": []})),
            authority_map="",
            boundary="",
            check_id="promotion-boundary",
            output=str(out),
        )
    )
    assert code == 0
    report = json.loads(out.read_text())
    assert report["floors"][0]["status"] in ("violated", "UNKNOWN")
    assert any("floor feature.x" in blocker for blocker in report["blockers"])


def test_cycle_audit_sole_self_is_unsafe(tmp_path: Path):
    from family_coherence import cmd_coherence

    boundary = {
        "schema_version": "mncs-promotion-boundary/0.1",
        "boundary_id": "x",
        "subject_repository": "epi13/consumer",
        "required_evidence": [{"check_id": "promotion-boundary", "authority": "a"}],
        "require_subject_binding": True,
    }
    graph_path = _coherence_graph(tmp_path, "a" * 40, "b" * 40)
    out = tmp_path / "coh.json"
    code = cmd_coherence(
        _ns(
            graph=str(graph_path),
            checkout=["consumer=" + str(tmp_path), "target=" + str(tmp_path)],
            capability=str(
                _write(
                    tmp_path,
                    "cap.json",
                    {"impacts": {}, "pins": {}, "capabilities": {}},
                )
            ),
            descriptors=str(_write(tmp_path, "d.json", {"descriptors": []})),
            authority_map="",
            boundary=str(_write(tmp_path, "b.json", boundary)),
            check_id="promotion-boundary",
            output=str(out),
        )
    )
    assert code == 0
    report = json.loads(out.read_text())
    assert report["cycles"]["unsafe"] is True
    assert any("sole" in finding for finding in report["cycles"]["unsafe_findings"])


def test_graph_subject_shapes():
    assert lib.parse_graph_subject("mncs-family/graph", "graph:" + "a" * 64) == "a" * 64
    assert lib.parse_graph_subject("mncs-family/graph", "graph:" + "A" * 64) is None
    assert lib.parse_graph_subject("mncs-family/graph", "graph:main") is None
    assert lib.parse_graph_subject("mncs-family/graph", "a" * 40) is None
    assert lib.parse_graph_subject("epi13/other", "graph:" + "a" * 64) is None


def test_graph_claim_subject_validated():
    claim = {
        "schema_version": "mncs.check-result/1",
        "id": "promotion-boundary",
        "provider": "mncs-promotion-boundary",
        "verdict": "PASS",
        "contract_revision": "0.1",
        "subject": {"repository": "mncs-family/graph", "commit": "graph:" + "a" * 64},
        "summary": "graph promotion",
        "scope": "family advancement",
        "claim": "graph satisfies the advancement boundary",
        "promotion": {
            "boundary_id": "family-advancement",
            "boundary_revision": "mncs-promotion-boundary/0.1",
            "subject": {
                "repository": "mncs-family/graph",
                "commit": "graph:" + "a" * 64,
            },
            "required_total": 1,
            "required_passed": 1,
            "blockers": [],
        },
        "references": [
            {
                "kind": "check-result",
                "check_id": "mncs-validation",
                "digest": "sha256:" + "b" * 64,
            }
        ],
    }
    errors = lib.validate_promotion_claim(
        claim,
        boundary_id="family-advancement",
        subject_repository="mncs-family/graph",
        subject_commit="graph:" + "a" * 64,
    )
    assert errors == []
    bad = json.loads(json.dumps(claim))
    bad["subject"]["commit"] = "graph:main"
    bad["promotion"]["subject"]["commit"] = "graph:main"
    errors = lib.validate_promotion_claim(
        bad,
        boundary_id="family-advancement",
        subject_repository="mncs-family/graph",
        subject_commit="graph:main",
    )
    assert any("graph identity" in error for error in errors)


def test_adapter_stamp_flags_exist():
    for adapter in ("commons_adapter.py", "forge_adapter.py", "language_adapter.py"):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS.parent / "adapters" / adapter), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0
        assert "--subject-repository" in proc.stdout
        assert "--subject-commit" in proc.stdout


def test_producer_refuses_foreign_stamp(tmp_path: Path):
    from family_producer import ProducerError
    from family_producer import run as producer_run

    origin = tmp_path / "origin"
    _git_repo(origin)
    rev = _commit(origin, "f.txt", "x")
    subprocess.run(
        ["git", "clone", "-q", f"file://{origin}", str(tmp_path / "x")],
        check=True,
        timeout=120,
    )
    checkout = tmp_path / "checkout"
    descriptors = {
        "descriptors": [
            {
                "producer": "probe",
                "repository": "epi13/probe",
                "artifact_paths": ["f.txt"],
                "contract": {"id": "probe", "revision": "0.1"},
                "adapter_id": "validator-json-v1",
                "required_capabilities": ["python", "owner-python-package"],
                "execution": {
                    "mode": "owner-native",
                    "operation": "mncs-standard-validate",
                    "input_paths": {"manifest": "x.json"},
                },
                "outputs": [
                    {
                        "check_id": "probe-check",
                        "provider": "probe",
                        "contract_revision": "0.1",
                        "roles": {
                            "evidence_provider": "probe",
                            "semantic_authority": "machine-native-complexity-standard",
                            "remediation_owner": "epi13/probe",
                            "transport_authority": "mncs-actions",
                            "originating_project": "epi13/mncs-actions",
                        },
                    }
                ],
            }
        ]
    }
    descriptors["schema_version"] = "mncs-actions.family-producer-descriptors/2"
    desc_path = _write(tmp_path, "desc.json", descriptors)
    contracts_path = _write(
        tmp_path,
        "contracts.json",
        {
            "schema_version": "mncs-actions.family-contracts/1",
            "repositories": [
                {
                    "name": "probe",
                    "repository": "epi13/probe",
                    "revision": rev,
                    "checkout_path": "x",
                    "artifacts": ["f.txt"],
                }
            ],
        },
    )
    (tmp_path / "out").mkdir()
    args = _ns(
        producer="probe",
        descriptors=desc_path,
        checkout=checkout,
        checkout_url=f"file://{origin}",
        family_root=tmp_path,
        actions_root=ACTIONS,
        output_dir=tmp_path / "out",
        language_binary=None,
        contracts=contracts_path,
        fixed_contracts=contracts_path,
        subject_repository="epi13/probe",
        subject_commit="d" * 40,
    )
    with pytest.raises(ProducerError, match="subject stamp must equal"):
        producer_run(args)
