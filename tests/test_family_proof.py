"""Durable acceptance proof: adversarial replay and boundary tests.

Every vector here must fail closed (exit 2 / GraphError). Vectors that
mutate bundle bytes use two layers deliberately: plain mutations are
expected to refuse at the content-inventory layer, while semantic-rule
vectors first *reclose* the bundle (recompute artifact digests and the
proof digest) so the refusal must come from the semantic rule itself,
never from a stale digest.

The evaluator and Commons doubles under tests/fixtures are plumbing
only (see their docstrings): they exercise wiring and claim
comparison. Semantic proof for the owner-native evaluator and
validator comes from the real end-to-end run, not from these tests.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
FIXTURES = REPO / "tests" / "fixtures"
STUB_EVALUATOR = FIXTURES / "replay_evaluator_stub.py"
COMMONS_ROOT = FIXTURES

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "lib"))

from family_graph import (
    GraphError,
    cmd_advance,
    cmd_build,
    cmd_verify,
)
from family_proof import cmd_build_proof, cmd_publish_commons, cmd_verify_accepted

A = "a" * 40
B = "b" * 40
C = "c" * 40
D = "d" * 40
M1 = "1" * 40
C1 = "2" * 40
P0 = "3" * 40
P1 = "4" * 40
X1 = "e" * 40

MNCS_REPO = "epi13/machine-native-complexity-standard"
COMMONS_REPO = "epi13/MNCS-Commons"
PROBE_REPO = "epi13/probe"
ACTIONS_REPO = "epi13/mncs-actions"

GRAPH_ID = "family-graph-9"
BASE_DIGEST = "f" * 64


def _ns(**kwargs):
    defaults = {
        "candidate": "",
        "fixed": "",
        "descriptors": "",
        "graph_id": GRAPH_ID,
        "actions_revision": X1,
        "accepted_graph": "",
        "coherence": "",
        "evidence_dir": "",
        "promotion_result": "",
        "commons_record": "",
        "boundary": "",
        "boundary_template": "",
        "authority_map": "",
        "checks_dir": "",
        "obligations_dir": "",
        "provenance_generator": "test",
        "generated_at": "2026-09-04T00:00:00Z",
        "output": "",
        "graph": "",
        "fixed_contracts": "",
        "lib_dir": str(REPO / "lib"),
        "commons_root": "",
        "output_contracts": "",
        "output_graph": "",
    }
    defaults.update(kwargs)
    return type("NS", (), defaults)()


def _write(path: Path, doc: dict) -> Path:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class Universe:
    """A complete synthetic family universe with real tool wiring."""

    def __init__(
        self,
        root: Path,
        *,
        poison_check_subject=None,
        poison_obligation_subject=None,
        poison_provider=None,
    ):
        self.root = root
        self.run = root / "run"
        self.proof_args: dict = {}
        self.replay_args: dict = {}
        self.graph_digest = ""
        # Poison is applied to the bundle inputs AFTER the claim is
        # minted (the minter, like the real evaluator, refuses unbound
        # subjects itself). Everything downstream stays consistent, so
        # only the replay's binding rule can fire.
        self.poison_check_subject = poison_check_subject
        self.poison_obligation_subject = poison_obligation_subject
        self.poison_provider = poison_provider
        self.build()

    def build(self):
        run = self.run
        (run / "checks").mkdir(parents=True)
        (run / "obligations").mkdir(parents=True)

        members = [
            {"name": "mncs-standard", "repository": MNCS_REPO, "commit": M1},
            {"name": "commons", "repository": COMMONS_REPO, "commit": C1},
            {"name": "probe", "repository": PROBE_REPO, "commit": P1},
            {"name": "mncs-actions", "repository": ACTIONS_REPO, "commit": X1},
        ]
        self.members = members

        fixed = {
            "schema_version": "mncs-actions.family-contracts/1",
            "repositories": [
                {"name": "mncs-standard", "repository": MNCS_REPO, "revision": M1},
                {"name": "commons", "repository": COMMONS_REPO, "revision": C1},
                {"name": "probe", "repository": PROBE_REPO, "revision": P0},
            ],
        }
        import mncs_actions as lib

        candidate = {
            "schema_version": "mncs-actions.family-contract-candidate/1",
            "base_contract_digest": lib.sha256_hex(lib.canonical_bytes(fixed)),
            "repositories": [
                {
                    "name": "mncs-standard",
                    "repository": MNCS_REPO,
                    "branch": "main",
                    "base_revision": M1,
                    "candidate_revision": M1,
                },
                {
                    "name": "commons",
                    "repository": COMMONS_REPO,
                    "branch": "main",
                    "base_revision": C1,
                    "candidate_revision": C1,
                },
                {
                    "name": "probe",
                    "repository": PROBE_REPO,
                    "branch": "main",
                    "base_revision": P0,
                    "candidate_revision": P1,
                },
            ],
        }
        fixed_path = _write(run / "fixed.json", fixed)
        candidate_path = _write(run / "candidate.json", candidate)
        descriptors_path = _write(
            run / "descriptors.json",
            {
                "descriptors": [
                    {
                        "producer": "probe",
                        "contract": {"id": "probe", "revision": "0.1"},
                        "outputs": [
                            {"check_id": "probe-check", "contract_revision": "0.1"}
                        ],
                    }
                ]
            },
        )

        template = {
            "schema_version": "mncs-promotion-boundary/0.1",
            "boundary_id": "family-advancement",
            "subject_repository": "mncs-family/graph",
            "required_evidence": [
                {
                    "check_id": "probe-check",
                    "authority": "probe-authority",
                    "contract_revision": "0.1",
                }
            ],
            "optional_evidence": [
                {
                    "check_id": "opt-check",
                    "authority": "opt-authority",
                    "contract_revision": "0.1",
                }
            ],
            "require_subject_binding": True,
            "obligation_check_id": "probe-obligations",
            "tolerated_obligations": [],
            "extensions": {},
        }
        template_path = _write(run / "template.json", template)

        accepted_graph = {
            "schema_version": "mncs-actions.family-candidate-graph/1",
            "graph_id": "family-graph-8",
            "digest": BASE_DIGEST,
            "status": "accepted",
        }
        accepted_path = _write(run / "accepted.json", accepted_graph)

        # Skeleton first to learn the digest, then materialize everything
        # bound to it (boundary, claim, coherence).
        out = run / "graph-skeleton.json"
        assert (
            cmd_build(
                _ns(
                    candidate=str(candidate_path),
                    fixed=str(fixed_path),
                    descriptors=str(descriptors_path),
                    boundary=str(template_path),
                    accepted_graph=str(accepted_path),
                    output=str(out),
                )
            )
            == 0
        )
        skeleton = json.loads(out.read_text())
        digest = skeleton["digest"]
        self.graph_digest = digest

        boundary = json.loads(json.dumps(template))
        boundary["graph"] = {
            "digest": digest,
            "members": [
                {"repository": m["repository"], "commit": m["commit"]}
                for m in skeleton["members"]
            ],
        }
        boundary_path = _write(run / "boundary.json", boundary)

        authority_map = {
            "schema_version": "mncs-authority-map/0.1",
            "authorities": {
                "probe-check": {
                    "provider": "probe-authority",
                    "authority": "probe-authority",
                },
                "opt-check": {
                    "provider": "opt-authority",
                    "authority": "opt-authority",
                },
            },
        }
        map_path = _write(run / "authority-map.json", authority_map)

        checks_dir = run / "checks"
        probe_check = {
            "schema_version": "mncs.check-result/1",
            "id": "probe-check",
            "provider": "probe-authority",
            "contract_revision": "0.1",
            "verdict": "PASS",
            "subject": {"repository": PROBE_REPO, "commit": P1},
            "summary": "probe passes",
        }
        opt_check = {
            "schema_version": "mncs.check-result/1",
            "id": "opt-check",
            "provider": "opt-authority",
            "contract_revision": "0.1",
            "verdict": "PASS",
            "subject": {"repository": PROBE_REPO, "commit": P1},
            "summary": "optional passes",
        }
        _write(checks_dir / "probe-check.json", probe_check)
        _write(checks_dir / "opt-check.json", opt_check)

        obligations_dir = run / "obligations"
        _write(
            obligations_dir / "o1.json",
            {
                "obligation_key": "pressure.probe.required",
                "status": "resolved",
                "required": True,
                "subject": {"repository": PROBE_REPO, "commit": P1},
            },
        )

        # The promotion claim is produced by the stub evaluator over the
        # same bytes replay will feed it, so the positive path compares
        # identical derivations.
        claim_path = run / "promotion-check.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(STUB_EVALUATOR),
                "--boundary",
                str(boundary_path),
                "--authority-map",
                str(map_path),
                "--checks",
                str(checks_dir / "probe-check.json"),
                str(checks_dir / "opt-check.json"),
                "--obligations",
                str(obligations_dir / "o1.json"),
                "--subject-graph",
                str(out),
                "--check-id",
                "promotion-boundary",
                "--provider",
                "mncs-promotion-boundary",
                "--contract-revision",
                "mncs-promotion-boundary/0.1",
                "--producer-revision",
                M1,
                "--output",
                str(claim_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        claim = json.loads(claim_path.read_text())
        assert claim["verdict"] == "PASS"

        if self.poison_check_subject is not None:
            _write(
                checks_dir / "probe-check.json",
                {
                    **probe_check,
                    "subject": {
                        "repository": self.poison_check_subject[0],
                        "commit": self.poison_check_subject[1],
                    },
                },
            )
        if self.poison_obligation_subject is not None:
            _write(
                obligations_dir / "o1.json",
                {
                    "obligation_key": "pressure.probe.required",
                    "status": "resolved",
                    "required": True,
                    "subject": {
                        "repository": self.poison_obligation_subject[0],
                        "commit": self.poison_obligation_subject[1],
                    },
                },
            )
        if self.poison_provider is not None:
            _write(claim_path, {**claim, "provider": self.poison_provider})

        coherence = {
            "graph_digest": digest,
            "edges": [
                {
                    "from": "probe",
                    "to": "mncs-standard",
                    "edge": "transport",
                    "target": M1,
                }
            ],
            "movements": [],
            "blockers": [],
            "cycles": {"unsafe": False},
            "floors": [],
        }
        coh_path = _write(run / "coherence.json", coherence)

        related_path = run / "graph-related.json"
        assert (
            cmd_build(
                _ns(
                    candidate=str(candidate_path),
                    fixed=str(fixed_path),
                    descriptors=str(descriptors_path),
                    boundary=str(template_path),
                    accepted_graph=str(accepted_path),
                    coherence=str(coh_path),
                    evidence_dir=str(checks_dir),
                    promotion_result=str(claim_path),
                    output=str(related_path),
                )
            )
            == 0
        )
        related = json.loads(related_path.read_text())
        assert related["digest"] == digest

        commons_record = {
            "kind": "ChangeSet",
            "details": {
                "changesetId": "changeset.family-graph-9",
                "baseRevisions": [
                    {"repository": m["repository"], "commit": m["commit"]}
                    for m in skeleton["members"]
                ],
                "references": [
                    {
                        "group": "supports",
                        "reference": {
                            "stableId": "s",
                            "contentDigest": _digest_file(
                                checks_dir / "probe-check.json"
                            ),
                        },
                    },
                    {
                        "group": "promotes",
                        "reference": {
                            "stableId": "p",
                            "contentDigest": _digest_file(claim_path),
                        },
                    },
                ],
                "predecessorGraph": BASE_DIGEST,
            },
        }
        commons_path = _write(run / "commons-record.json", commons_record)
        capability = {"impacts": {}, "pins": {}, "capabilities": {}, "floors": []}
        capability_path = _write(run / "capability.json", capability)

        # Full related graph with the Commons relation attached.
        assert (
            cmd_build(
                _ns(
                    candidate=str(candidate_path),
                    fixed=str(fixed_path),
                    descriptors=str(descriptors_path),
                    boundary=str(template_path),
                    accepted_graph=str(accepted_path),
                    coherence=str(coh_path),
                    evidence_dir=str(checks_dir),
                    promotion_result=str(claim_path),
                    commons_record=str(commons_path),
                    output=str(related_path),
                )
            )
            == 0
        )
        assert json.loads(related_path.read_text())["digest"] == digest

        proposed_path = run / "proposed.json"
        accepted_out = run / "accepted-graph.json"
        assert (
            cmd_advance(
                _ns(
                    graph=str(related_path),
                    fixed=str(fixed_path),
                    promotion_result=str(claim_path),
                    coherence=str(coh_path),
                    commons_record=str(commons_path),
                    commons_root=str(COMMONS_ROOT),
                    boundary=str(boundary_path),
                    boundary_template=str(template_path),
                    authority_map=str(map_path),
                    checks_dir=str(checks_dir),
                    obligations_dir=str(obligations_dir),
                    output_contracts=str(proposed_path),
                    output_graph=str(accepted_out),
                )
            )
            == 0
        )

        self.paths = {
            "fixed": fixed_path,
            "candidate": candidate_path,
            "descriptors": descriptors_path,
            "template": template_path,
            "accepted_prev": accepted_path,
            "boundary": boundary_path,
            "authority_map": map_path,
            "checks": checks_dir,
            "obligations": obligations_dir,
            "claim": claim_path,
            "coherence": coh_path,
            "related": related_path,
            "commons": commons_path,
            "capability": capability_path,
            "proposed": proposed_path,
            "accepted": accepted_out,
        }
        self.proof_args = {
            "graph": str(related_path),
            "accepted_graph": str(accepted_out),
            "coherence": str(coh_path),
            "boundary": str(boundary_path),
            "boundary_template": str(template_path),
            "authority_map": str(map_path),
            "capability": str(capability_path),
            "descriptors": str(descriptors_path),
            "checks_dir": str(checks_dir),
            "obligations_dir": str(obligations_dir),
            "promotion_result": str(claim_path),
            "commons_record": str(commons_path),
            "fixed": str(fixed_path),
            "proposed_contracts": str(proposed_path),
            "orchestrator_revision": X1,
            "evaluator_path": str(STUB_EVALUATOR),
            "commons_root": str(COMMONS_ROOT),
            "output_dir": str(self.root / "proof"),
        }

    def build_proof(self, **overrides):
        args = dict(self.proof_args)
        args.update(overrides)
        assert cmd_build_proof(_ns(**args)) == 0
        return Path(args["output_dir"])

    def replay(self, proof_dir: Path, **overrides):
        args = {
            "proof": str(proof_dir),
            "boundary_template": str(self.paths["template"]),
            "evaluator": str(STUB_EVALUATOR),
            "commons_root": str(COMMONS_ROOT),
            "lib_dir": str(REPO / "lib"),
            "coherence_path": str(SCRIPTS / "family_coherence.py"),
            "checkouts_root": "",
            "previous_proof": "",
        }
        args.update(overrides)
        return cmd_verify_accepted(_ns(**args))


def _reclose(proof_dir: Path):
    """Recompute artifact digests + proof digest after a semantic mutation.

    Used to isolate semantic rules: after reclosing, inventory and digest
    checks pass, so any refusal must come from the rule under test.
    """
    import mncs_actions as lib

    manifest_path = proof_dir / "proof.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["artifacts"]:
        data = (proof_dir / entry["ref"]).read_bytes()
        entry["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    accepted_path = proof_dir / "accepted-graph.json"
    accepted = json.loads(accepted_path.read_text())
    core = {k: v for k, v in accepted.items() if k != "proof"}
    manifest["accepted_graph_digest"] = lib.sha256_hex(lib.canonical_bytes(core))
    manifest.pop("proof_digest", None)
    manifest["proof_digest"] = lib.sha256_hex(lib.canonical_bytes(manifest))
    accepted["proof"] = {"digest": manifest["proof_digest"], "ref": "proof.json"}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    accepted_path.write_text(json.dumps(accepted, indent=2, sort_keys=True) + "\n")
    return manifest


def _copy_bundle(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    return dest


def test_positive_replay_passes(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    proof_dir = universe.build_proof()
    manifest = json.loads((proof_dir / "proof.json").read_text())
    assert manifest["acceptance"]["tier"] == "full"
    assert manifest["promotion"]["verdict"] == "PASS"
    assert universe.replay(proof_dir) == 0


def _fresh_bundle(tmp_path: Path, name: str = "u"):
    universe = Universe(tmp_path / name)
    return universe, universe.build_proof()


def _edit_json(path: Path, mutate):
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


# --- Inventory and digest layer -------------------------------------------


def test_replay_refuses_graph_digest_tamper(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "graph.json",
        lambda g: g["members"].__setitem__(0, {**g["members"][0], "commit": B}),
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_proof_digest_tamper(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "proof.json", lambda m: m.__setitem__("graph_digest", "0" * 64)
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_missing_proof_member(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    (proof_dir / "checks" / "opt-check.json").unlink()
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_injected_extra_evidence(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    shutil.copyfile(
        proof_dir / "checks" / "opt-check.json", proof_dir / "checks" / "extra.json"
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_duplicated_evidence_ids(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    shutil.copyfile(
        proof_dir / "checks" / "probe-check.json",
        proof_dir / "checks" / "probe-check-dup.json",
    )
    _edit_json(
        proof_dir / "checks" / "probe-check-dup.json",
        lambda d: d.__setitem__("subject", d["subject"]),
    )
    # Same check id under a second file: the closed inventory refuses.
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_promotion_bytes_mismatch(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "promotion-check.json",
        lambda c: c.__setitem__("summary", "tampered"),
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_bundle_without_accepted_graph(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    (proof_dir / "accepted-graph.json").unlink()
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_missing_proof_manifest(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert universe.replay(empty) == 2


def test_replay_refuses_absolute_temp_paths(tmp_path: Path):
    # A valid digest with forbidden content: reclose the bundle so only
    # the temp-path rule can fire.
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "proof.json",
        lambda m: m["acceptance"].update({"policy": "see /tmp/evil for details"}),
    )
    _reclose(proof_dir)
    code = universe.replay(proof_dir)
    assert code == 2


def test_build_proof_refuses_temp_paths_in_graph(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    related = universe.paths["related"]
    _edit_json(
        related,
        lambda g: g["evidence"].append(
            {"check_id": "x", "path": "/tmp/evil.json", "digest": "a" * 64}
        ),
    )
    with pytest.raises(GraphError):
        universe.build_proof(output_dir=str(tmp_path / "proof2"))


# --- Boundary layer (semantic; reclosed so only the rule fires) -----------


def _reclosed_with(proof_dir: Path, mutate):
    _edit_json(proof_dir / "boundary.json", mutate)
    _reclose(proof_dir)
    return proof_dir


def test_replay_refuses_weaker_substituted_boundary(tmp_path: Path):
    # Even when the operator template is swapped to match, the claim's
    # required_total no longer coheres with the boundary: a genuine PASS
    # under weaker requirements must not verify against it.
    universe, proof_dir = _fresh_bundle(tmp_path)
    _reclosed_with(
        proof_dir,
        lambda b: b.__setitem__(
            "required_evidence",
            [e for e in b["required_evidence"] if e["check_id"] != "probe-check"],
        ),
    )
    weak_template = tmp_path / "weak-template.json"
    shutil.copyfile(universe.paths["template"], weak_template)
    _edit_json(
        weak_template,
        lambda t: t.__setitem__(
            "required_evidence",
            [e for e in t["required_evidence"] if e["check_id"] != "probe-check"],
        ),
    )
    assert universe.replay(proof_dir, boundary_template=str(weak_template)) == 2


def test_replay_refuses_modified_boundary_after_evaluation(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    # Same id, different authority: the digest binding must still fire.
    _edit_json(
        proof_dir / "boundary.json",
        lambda b: b["required_evidence"].__setitem__(
            0,
            {
                "check_id": "probe-check",
                "authority": "someone-else",
                "contract_revision": "0.1",
            },
        ),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_boundary_member_mismatch(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _reclosed_with(
        proof_dir,
        lambda b: b["graph"].__setitem__(
            "members",
            [m for m in b["graph"]["members"] if m["repository"] != PROBE_REPO],
        ),
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_forged_boundary_digest(tmp_path: Path):
    # Forge the manifest's boundary digest, then reclose only the proof
    # digest around the forgery so the boundary rule itself must fire.
    universe, proof_dir = _fresh_bundle(tmp_path)
    import mncs_actions as lib

    manifest_path = proof_dir / "proof.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["boundary"]["digest"] = "sha256:" + "0" * 64
    manifest.pop("proof_digest", None)
    manifest["proof_digest"] = lib.sha256_hex(lib.canonical_bytes(manifest))
    accepted_path = proof_dir / "accepted-graph.json"
    accepted = json.loads(accepted_path.read_text())
    accepted["proof"] = {"digest": manifest["proof_digest"], "ref": "proof.json"}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    accepted_path.write_text(json.dumps(accepted, indent=2, sort_keys=True) + "\n")
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_correct_claim_with_wrong_boundary(tmp_path: Path):
    # Swap in a well-formed boundary for a different constellation while
    # keeping the original claim: the claim/boundary binding must fire.
    universe, proof_dir = _fresh_bundle(tmp_path)
    other_members = [
        {"repository": MNCS_REPO, "commit": M1},
        {"repository": COMMONS_REPO, "commit": C1},
        {"repository": PROBE_REPO, "commit": B},
    ]
    _edit_json(
        proof_dir / "boundary.json",
        lambda b: b["graph"].update({"digest": "1" * 64, "members": other_members}),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_correct_boundary_with_wrong_graph(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "graph.json",
        lambda g: g["members"].__setitem__(2, {**g["members"][2], "commit": B}),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


# --- Authority layer -------------------------------------------------------


def test_replay_refuses_unexpected_authority_binding(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "authority-map.json",
        lambda m: m["authorities"].update(
            {"ghost-check": {"provider": "x", "authority": "x"}}
        ),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_authority_mismatch(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "authority-map.json",
        lambda m: m["authorities"]["probe-check"].update({"authority": "someone-else"}),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_missing_authority_binding(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "authority-map.json", lambda m: m["authorities"].pop("probe-check")
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_duplicate_authority_keys(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    raw_path = proof_dir / "authority-map.json"
    raw = raw_path.read_text()
    dup = raw.replace('"opt-check": {', '"probe-check": {', 1)
    assert dup != raw
    raw_path.write_text(dup)
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


# --- Evidence and obligation layer -----------------------------------------


def _poisoned_bundle(tmp_path: Path, name: str, **kwargs):
    # Build the closure around already-wrong subjects: every layer up to
    # the evaluator rerun stays consistent, so the rerun's binding rule
    # is what must fire.
    universe = Universe(tmp_path / name, **kwargs)
    return universe, universe.build_proof()


def test_replay_refuses_evidence_from_wrong_revision(tmp_path: Path):
    universe, proof_dir = _poisoned_bundle(
        tmp_path, "u", poison_check_subject=(PROBE_REPO, B)
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_stale_obligation_set(tmp_path: Path):
    universe, proof_dir = _poisoned_bundle(
        tmp_path, "u", poison_obligation_subject=(PROBE_REPO, B)
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_forged_producer_stamp(tmp_path: Path):
    universe, proof_dir = _poisoned_bundle(
        tmp_path, "u", poison_check_subject=("epi13/ghost", B)
    )
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_promotion_from_wrong_producer(tmp_path: Path):
    universe, proof_dir = _poisoned_bundle(
        tmp_path, "u", poison_provider="someone-else"
    )
    assert universe.replay(proof_dir) == 2


# --- Generator and chain layer ---------------------------------------------


def test_replay_refuses_mismatched_generator_revision(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "proof.json",
        lambda m: m["generators"]["evaluator"].update({"commit": B}),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_moving_git_ref(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "proof.json",
        lambda m: m["generators"]["orchestrator"].update({"commit": "main"}),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_tampered_tool_bytes(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    tampered = tmp_path / "tampered-evaluator.py"
    tampered.write_bytes(
        Path(universe.proof_args["evaluator_path"]).read_bytes() + b"\n# x\n"
    )
    assert universe.replay(proof_dir, evaluator=str(tampered)) == 2


def test_replay_refuses_commons_predecessor_mismatch(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "commons-record.json",
        lambda r: r["details"].update({"predecessorGraph": "0" * 64}),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_commons_digest_mismatch(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "commons-record.json",
        lambda r: r.__setitem__("contentDigest", "sha256:" + "0" * 64),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_unsafe_authority_cycle(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "coherence.json",
        lambda c: c.update(
            {"cycles": {"unsafe": True, "unsafe_findings": ["self-approval"]}}
        ),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_unknown_capability_path(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "coherence.json",
        lambda c: c.__setitem__(
            "movements",
            [
                {
                    "from": "probe",
                    "to": "probe",
                    "edge": "transport",
                    "classification": "UNKNOWN",
                    "satisfied": False,
                    "member_commit": P1,
                    "pinned": P0,
                    "paths": ["mystery/x"],
                }
            ],
        ),
    )
    _edit_json(
        proof_dir / "proof.json", lambda m: m["coherence"].update({"movements": 1})
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_refuses_incomplete_member_set(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(
        proof_dir / "contracts-after.json",
        lambda c: c.__setitem__(
            "repositories", [e for e in c["repositories"] if e["name"] != "probe"]
        ),
    )
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


def test_replay_detects_tampered_predecessor(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    proof_dir = universe.build_proof()
    previous_dir = tmp_path / "prev"
    previous_dir.mkdir()
    (previous_dir / "proof.json").write_text(
        json.dumps({"graph_digest": "0" * 64, "proof_digest": "1" * 64})
    )
    assert universe.replay(proof_dir, previous_proof=str(previous_dir)) == 2


# --- Verifier boundary vectors (cmd_verify layer) --------------------------
# The claim may not declare its own expected boundary: these prove the
# external boundary input actually decides.


def _verify_ns(universe: Universe, **overrides):
    paths = universe.paths
    args = {
        "graph": str(paths["related"]),
        "fixed": str(paths["fixed"]),
        "promotion_result": str(paths["claim"]),
        "lib_dir": str(REPO / "lib"),
        "coherence": str(paths["coherence"]),
        "commons_record": str(paths["commons"]),
        "commons_root": str(COMMONS_ROOT),
        "boundary": str(paths["boundary"]),
        "boundary_template": str(paths["template"]),
        "authority_map": str(paths["authority_map"]),
    }
    args.update(overrides)
    return _ns(**args)


def test_verify_accepts_consistent_closure(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    assert cmd_verify(_verify_ns(universe)) == 0


def test_advance_refuses_noop_constellation(tmp_path: Path):
    # Advancing an already-accepted constellation is noise, not proof:
    # advance must refuse instead of emitting identical contracts.
    universe = Universe(tmp_path / "u")
    paths = universe.paths
    fixed = json.loads(paths["fixed"].read_text())
    for entry in fixed["repositories"]:
        if entry["name"] == "probe":
            entry["revision"] = P1
    noop_fixed = tmp_path / "noop-fixed.json"
    noop_fixed.write_text(json.dumps(fixed))
    code = cmd_advance(
        _ns(
            graph=str(paths["related"]),
            fixed=str(noop_fixed),
            promotion_result=str(paths["claim"]),
            lib_dir=str(REPO / "lib"),
            coherence=str(paths["coherence"]),
            commons_record=str(paths["commons"]),
            commons_root=str(COMMONS_ROOT),
            boundary=str(paths["boundary"]),
            boundary_template=str(paths["template"]),
            authority_map=str(paths["authority_map"]),
            checks_dir=str(paths["checks"]),
            obligations_dir=str(paths["obligations"]),
            output_contracts=str(tmp_path / "p.json"),
            output_graph=str(tmp_path / "a.json"),
        )
    )
    assert code == 2


def test_build_proof_reaffirms_unenriched_acceptance(tmp_path: Path):
    # An accepted file predating acceptance records still bundles: the
    # bundle copy gains the recomputed record; replay passes.
    universe = Universe(tmp_path / "u")
    proof_dir = tmp_path / "proof"
    args = dict(universe.proof_args)
    accepted = json.loads(universe.paths["accepted"].read_text())
    assert "acceptance" in accepted
    del accepted["acceptance"]
    bare = tmp_path / "bare-accepted.json"
    bare.write_text(json.dumps(accepted))
    args["accepted_graph"] = str(bare)
    args["output_dir"] = str(proof_dir)
    assert cmd_build_proof(_ns(**args)) == 0
    bundled = json.loads((proof_dir / "accepted-graph.json").read_text())
    assert bundled["acceptance"]["tier"] in ("core", "full")
    assert universe.replay(proof_dir) == 0


def test_verify_refuses_without_boundary(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, boundary=""))


def test_verify_refuses_valid_pass_under_wrong_boundary(tmp_path: Path):
    # A genuine PASS claim evaluated under a different constellation's
    # boundary must not verify against this graph's boundary.
    universe = Universe(tmp_path / "u")
    other_boundary = tmp_path / "other-boundary.json"
    template = json.loads(universe.paths["template"].read_text())
    template["graph"] = {"digest": "1" * 64, "members": []}
    other_boundary.write_text(json.dumps(template))
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, boundary=str(other_boundary)))


def test_verify_refuses_weaker_boundary(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    weak = tmp_path / "weak.json"
    boundary = json.loads(universe.paths["boundary"].read_text())
    boundary["required_evidence"] = []
    weak.write_text(json.dumps(boundary))
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, boundary=str(weak)))


def test_verify_refuses_modified_boundary(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    modified = tmp_path / "modified.json"
    boundary = json.loads(universe.paths["boundary"].read_text())
    boundary["required_evidence"][0]["authority"] = "someone-else"
    modified.write_text(json.dumps(boundary))
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, boundary=str(modified)))


def test_verify_refuses_boundary_member_mismatch(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    mismatch = tmp_path / "mismatch.json"
    boundary = json.loads(universe.paths["boundary"].read_text())
    boundary["graph"]["members"] = boundary["graph"]["members"][:-1]
    mismatch.write_text(json.dumps(boundary))
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, boundary=str(mismatch)))


def test_verify_refuses_unexpected_authority_binding(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    ghost = tmp_path / "ghost-map.json"
    authority_map = json.loads(universe.paths["authority_map"].read_text())
    authority_map["authorities"]["ghost-check"] = {"provider": "x", "authority": "x"}
    ghost.write_text(json.dumps(authority_map))
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, authority_map=str(ghost)))


def test_verify_refuses_duplicate_authority_binding(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    dup = tmp_path / "dup-map.json"
    raw = universe.paths["authority_map"].read_text()
    dup.write_text(raw.replace('"opt-check": {', '"probe-check": {', 1))
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, authority_map=str(dup)))


def test_verify_refuses_missing_authority_binding(tmp_path: Path):
    universe = Universe(tmp_path / "u")
    missing = tmp_path / "missing-map.json"
    authority_map = json.loads(universe.paths["authority_map"].read_text())
    del authority_map["authorities"]["probe-check"]
    missing.write_text(json.dumps(authority_map))
    with pytest.raises(GraphError):
        cmd_verify(_verify_ns(universe, authority_map=str(missing)))


# --- Predecessor genesis link ---------------------------------------------


def test_genesis_predecessor_carries_documented_reason(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    manifest = json.loads((proof_dir / "proof.json").read_text())
    predecessor = manifest["predecessor"]
    assert predecessor["graph_id"] == "family-graph-8"
    assert predecessor["proof_digest"] is None
    assert predecessor["note"].strip()
    assert universe.replay(proof_dir) == 0


def test_replay_refuses_undocumented_null_predecessor(tmp_path: Path):
    universe, proof_dir = _fresh_bundle(tmp_path)
    _edit_json(proof_dir / "proof.json", lambda m: m["predecessor"].pop("note"))
    _reclose(proof_dir)
    assert universe.replay(proof_dir) == 2


# --- Commons publication --------------------------------------------------


def _publish_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "commons-checkout"
    shutil.copytree(FIXTURES / "src", checkout / "src")
    return checkout


def _publish_ns(proof_dir: Path, checkout: Path):
    return _ns(proof=str(proof_dir), commons_checkout=str(checkout))


def test_publish_stages_changeset_and_is_idempotent(tmp_path: Path):
    _universe, proof_dir = _fresh_bundle(tmp_path)
    checkout = _publish_checkout(tmp_path)
    assert cmd_publish_commons(_publish_ns(proof_dir, checkout)) == 0
    dest = checkout / "family" / "changesets" / "changeset.family-graph-9.json"
    assert dest.read_bytes() == (proof_dir / "commons-record.json").read_bytes()
    # Identical bytes restage as a no-op, never a rewrite.
    assert cmd_publish_commons(_publish_ns(proof_dir, checkout)) == 0
    assert dest.read_bytes() == (proof_dir / "commons-record.json").read_bytes()


def test_publish_refuses_overwrite_of_published_record(tmp_path: Path):
    _universe, proof_dir = _fresh_bundle(tmp_path)
    checkout = _publish_checkout(tmp_path)
    assert cmd_publish_commons(_publish_ns(proof_dir, checkout)) == 0
    dest = checkout / "family" / "changesets" / "changeset.family-graph-9.json"
    dest.write_bytes(dest.read_bytes() + b" ")
    with pytest.raises(GraphError):
        cmd_publish_commons(_publish_ns(proof_dir, checkout))


def test_publish_refuses_record_id_mismatch(tmp_path: Path):
    _universe, proof_dir = _fresh_bundle(tmp_path)
    checkout = _publish_checkout(tmp_path)
    _edit_json(
        proof_dir / "commons-record.json",
        lambda r: r["details"].update({"changesetId": "changeset.other"}),
    )
    _reclose(proof_dir)
    with pytest.raises(GraphError):
        cmd_publish_commons(_publish_ns(proof_dir, checkout))


def test_publish_refuses_wrong_checkout_revision(tmp_path: Path):
    _universe, proof_dir = _fresh_bundle(tmp_path)
    checkout = _publish_checkout(tmp_path)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    # The fresh commit cannot equal the recorded validator revision,
    # so staging into this checkout must refuse.
    with pytest.raises(GraphError):
        cmd_publish_commons(_publish_ns(proof_dir, checkout))
    assert not (checkout / "family").exists()
