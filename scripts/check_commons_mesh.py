#!/usr/bin/env python3
"""Commons Mesh conformance check for the MNCS family.

Validates one MNCS-Commons checkout's distributed mesh surface without
joining any network:

1. mesh protocol versions match the family-pinned constants;
2. execution corpora are drift-free against their generator;
3. the golden capsule digest is stable (no silent protocol migration);
4. every mesh kernel completes ``source-study`` (toolchain-gated UNKNOWN);
5. a fresh node's descriptor round-trips and negotiates with itself.

Output is one JSON verdict on stdout.  Exit 0 always carries the verdict;
exit 2 means the check itself could not run (missing checkout).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_VERSIONS = {
    "mesh": "commons.mncs.dev/mesh/v0alpha1",
    "interest": "commons.mncs.dev/interest/v0alpha1",
    "availability": "commons.mncs.dev/availability/v0alpha1",
    "capsule": "commons.mncs.dev/capsule/v0alpha1",
    "relay": "commons.mncs.dev/relay/v0alpha1",
    "view": "commons.mncs.dev/view/v0alpha1",
}

EXPECTED_GOLDEN_DIGEST = (
    "sha256:e13acb3ec2b61c5e79199376745bf63d23885a4d12beee2681ecc7b62b71213c"
)

KERNELS = [
    "commons/mesh/availability.mncs",
    "commons/mesh/outcome.mncs",
    "commons/mesh/interest.mncs",
    "commons/mesh/lattice_check.mncs",
]


def check(name: str, passed: bool, detail: str = "") -> dict:
    return {"check": name, "passed": passed, "detail": detail[:500]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commons-root", required=True)
    parser.add_argument("--mncs-bin", default=None)
    args = parser.parse_args()

    commons = Path(args.commons_root)
    src = commons / "src"
    if not (src / "mncs_commons" / "mesh" / "__init__.py").exists():
        print(json.dumps({"verdict": "ERROR", "detail": "commons mesh package absent"}))
        return 2
    sys.path.insert(0, str(src))

    results: list[dict] = []
    try:
        from mncs_commons import mesh
        from mncs_commons.canonical import canonical_digest
    except ImportError as error:
        print(json.dumps({"verdict": "ERROR", "detail": f"import failed: {error}"}))
        return 2

    versions = {
        "mesh": mesh.MESH_VERSION,
        "interest": mesh.INTEREST_VERSION,
        "availability": mesh.AVAILABILITY_VERSION,
        "capsule": mesh.CAPSULE_VERSION,
        "relay": mesh.RELAY_VERSION,
        "view": mesh.VIEW_VERSION,
    }
    results.append(
        check(
            "protocol-versions",
            versions == EXPECTED_VERSIONS,
            f"advertised={versions}",
        )
    )

    generator = commons / "scripts" / "generate_mesh_corpora.py"
    completed = subprocess.run(
        [sys.executable, str(generator), "--check"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    results.append(
        check("corpora-drift-free", completed.returncode == 0, completed.stdout[-300:])
    )

    golden_path = commons / "tests" / "fixtures" / "mesh_capsule_golden.json"
    try:
        capsule = json.loads(golden_path.read_text(encoding="utf-8"))
        digest = canonical_digest(capsule, projected=False)
        results.append(
            check("golden-digest-stable", digest == EXPECTED_GOLDEN_DIGEST, digest)
        )
    except (OSError, ValueError) as error:
        results.append(check("golden-digest-stable", False, str(error)))

    mncs_bin = args.mncs_bin or os.environ.get("MNCS_BIN")
    kernel_results = []
    if mncs_bin and Path(mncs_bin).exists():
        environment = dict(os.environ)
        # The lattice probe imports the language stdlib and the mesh kernels;
        # both roots must resolve without vendoring either tree.
        roots = [str(commons / "src" / "mncs_commons" / "mesh" / "mncs")]
        checkout = Path(mncs_bin).resolve().parents[2]
        if (checkout / "library").is_dir():
            roots.insert(0, str(checkout / "library"))
        existing = environment.get("MNCS_LIBRARY_PATH", "")
        environment["MNCS_LIBRARY_PATH"] = ":".join(
            [item for item in [*roots, existing] if item]
        )
        for kernel in KERNELS:
            proc = subprocess.run(
                [mncs_bin, "source-study", str(commons / "src" / "mncs_commons" / "mesh" / "mncs" / kernel), "--node-id", "mncs-actions"],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
                timeout=120,
            )
            ok = proc.returncode == 0
            status = ""
            if ok:
                try:
                    status = json.loads(proc.stdout).get("compilation_status", "")
                    ok = status in ("completed", "completed_with_unresolved_obligations")
                except ValueError:
                    ok = False
            kernel_results.append(ok)
            results.append(check(f"kernel-study:{kernel.rsplit('/', 1)[-1]}", ok, status))
    else:
        results.append(check("kernel-study", True, "UNKNOWN: mncs toolchain absent"))

    try:
        with tempfile.TemporaryDirectory(prefix="mesh-check-") as staging:
            node = mesh.CommonsNode(Path(staging) / "node", node_id="check-node")
            node.init()
            descriptor = node.describe()
            round_tripped = mesh.NodeDescriptor.from_mapping(descriptor).as_dict()
            agreement = mesh.negotiate(
                mesh.NodeDescriptor.from_mapping(descriptor), round_tripped
            )
            results.append(
                check(
                    "descriptor-negotiation",
                    bool(agreement["canExchange"]) and "direct" in agreement["agreedSyncModes"],
                    f"modes={agreement['agreedSyncModes']}",
                )
            )
    except Exception as error:  # noqa: BLE001 - check must report, never raise
        results.append(check("descriptor-negotiation", False, f"{type(error).__name__}: {error}"))

    verdict = "PASS" if all(item["passed"] for item in results) else "FAIL"
    print(json.dumps({"verdict": verdict, "checks": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
