#!/usr/bin/env python3
"""Read, validate, and propose MNCS-family revision sets.

The fixed family contract is an explicit compatibility claim.  This module
also defines the deliberately separate candidate format used by moving-head
drift observation.  Candidate generation is mechanical: it resolves a branch
to an exact commit and never writes ``family-contracts.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from mncs_actions import canonical_bytes, sha256_hex  # noqa: E402

FIXED_SCHEMA = "mncs-actions.family-contracts/1"
CANDIDATE_SCHEMA = "mncs-actions.family-contract-candidate/1"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_PATH_RE = re.compile(r"^[^/\\\x00-\x1f\x7f][^\\\x00-\x1f\x7f]*$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_CONTRACT_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_CONTRACT_JSON_DEPTH = 64
MAX_CONTRACT_PATH_LENGTH = 512


class ContractError(ValueError):
    """A family contract or candidate is malformed or inconsistent."""


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_CONTRACT_DOCUMENT_BYTES:
            raise ContractError(
                f"contract document exceeds protocol JSON limit of {MAX_CONTRACT_DOCUMENT_BYTES} bytes"
            )
        depth = 0
        maximum = 0
        in_string = False
        escaped = False
        for byte in raw:
            char = chr(byte)
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
                maximum = max(maximum, depth)
            elif char in "]}":
                depth -= 1
        if maximum > MAX_CONTRACT_JSON_DEPTH:
            raise ContractError(
                f"contract document exceeds protocol JSON depth limit of {MAX_CONTRACT_JSON_DEPTH}"
            )
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON constant {token}")
            ),
        )
    except ContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ContractError(f"cannot read contract document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"contract document must be an object: {path}")
    return value


def _safe_relative_path(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CONTRACT_PATH_LENGTH
        or value.startswith(("/", "\\"))
        or value.startswith(".")
        or "\\" in value
        or ".." in value.split("/")
        or unicodedata.normalize("NFC", value) != value
        or not SAFE_PATH_RE.match(value)
    ):
        raise ContractError(f"{label} must be a safe relative path")


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not FULL_SHA_RE.fullmatch(value):
        raise ContractError(f"{label} must be a lowercase 40-character SHA")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError(f"{label} must be a lowercase 64-character SHA-256 digest")
    return value


def _portable_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _common_entry(entry: Any, index: int) -> dict[str, Any]:
    label = f"repositories[{index}]"
    if not isinstance(entry, dict):
        raise ContractError(f"{label} must be an object")
    for field in ("name", "repository", "checkout_path", "artifacts"):
        if field not in entry:
            raise ContractError(f"{label}.{field} is required")
    if not isinstance(entry["name"], str) or not IDENTIFIER_RE.fullmatch(entry["name"]):
        raise ContractError(f"{label}.name must be a bounded identifier")
    if not isinstance(entry["repository"], str) or len(entry["repository"]) > MAX_CONTRACT_PATH_LENGTH or not re.fullmatch(
        r"[^/\s]+/[^/\s]+", entry["repository"]
    ):
        raise ContractError(f"{label}.repository must be an owner/repository slug")
    _safe_relative_path(entry["checkout_path"], f"{label}.checkout_path")
    artifacts = entry["artifacts"]
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 128 or not all(
        isinstance(item, str) and item for item in artifacts
    ):
        raise ContractError(f"{label}.artifacts must be a non-empty string array")
    for artifact in artifacts:
        _safe_relative_path(artifact, f"{label}.artifacts[]")
    identities = [unicodedata.normalize("NFC", artifact).casefold() for artifact in artifacts]
    if len(set(identities)) != len(identities):
        raise ContractError(f"{label}.artifacts contains ambiguous duplicate paths")
    return entry


def validate_fixed(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema_version") != FIXED_SCHEMA:
        raise ContractError(f"schema_version must be {FIXED_SCHEMA}")
    entries = document.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise ContractError("repositories must be a non-empty array")
    names: set[str] = set()
    repositories: set[str] = set()
    paths: set[str] = set()
    name_identities: set[str] = set()
    repository_identities: set[str] = set()
    path_identities: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(entries):
        entry = _common_entry(raw, index)
        _sha(entry.get("revision"), f"repositories[{index}].revision")
        name_identity = _portable_identity(entry["name"])
        repository_identity = _portable_identity(entry["repository"])
        path_identity = _portable_identity(entry["checkout_path"])
        if entry["name"] in names or name_identity in name_identities:
            raise ContractError(f"duplicate family repository name: {entry['name']}")
        if entry["repository"] in repositories or repository_identity in repository_identities:
            raise ContractError(f"duplicate family repository slug: {entry['repository']}")
        if entry["checkout_path"] in paths or path_identity in path_identities:
            raise ContractError(f"duplicate family checkout path: {entry['checkout_path']}")
        names.add(entry["name"])
        repositories.add(entry["repository"])
        paths.add(entry["checkout_path"])
        name_identities.add(name_identity)
        repository_identities.add(repository_identity)
        path_identities.add(path_identity)
        normalized.append(entry)
    return normalized


def validate_candidate(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema_version") != CANDIDATE_SCHEMA:
        raise ContractError(f"schema_version must be {CANDIDATE_SCHEMA}")
    if document.get("source") != "moving-head":
        raise ContractError("candidate source must be moving-head")
    if document.get("base_schema_version") != FIXED_SCHEMA:
        raise ContractError(f"base_schema_version must be {FIXED_SCHEMA}")
    _digest(document.get("base_contract_digest"), "base_contract_digest")
    branch = document.get("branch")
    if (
        not isinstance(branch, str)
        or not branch
        or len(branch) > MAX_CONTRACT_PATH_LENGTH
        or any(ch.isspace() for ch in branch)
    ):
        raise ContractError("branch must be non-empty text without whitespace")
    entries = document.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise ContractError("repositories must be a non-empty array")
    names: set[str] = set()
    repositories: set[str] = set()
    paths: set[str] = set()
    name_identities: set[str] = set()
    repository_identities: set[str] = set()
    path_identities: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(entries):
        entry = _common_entry(raw, index)
        _sha(entry.get("base_revision"), f"repositories[{index}].base_revision")
        _sha(entry.get("candidate_revision"), f"repositories[{index}].candidate_revision")
        if entry.get("branch") != branch:
            raise ContractError(f"repositories[{index}].branch differs from document branch")
        name_identity = _portable_identity(entry["name"])
        repository_identity = _portable_identity(entry["repository"])
        path_identity = _portable_identity(entry["checkout_path"])
        if entry["name"] in names or name_identity in name_identities:
            raise ContractError(f"duplicate family repository name: {entry['name']}")
        if entry["repository"] in repositories or repository_identity in repository_identities:
            raise ContractError(f"duplicate family repository slug: {entry['repository']}")
        if entry["checkout_path"] in paths or path_identity in path_identities:
            raise ContractError(f"duplicate family checkout path: {entry['checkout_path']}")
        names.add(entry["name"])
        repositories.add(entry["repository"])
        paths.add(entry["checkout_path"])
        name_identities.add(name_identity)
        repository_identities.add(repository_identity)
        path_identities.add(path_identity)
        normalized.append(entry)
    return normalized


def validate_against_fixed(
    candidate: dict[str, Any], fixed: dict[str, Any]
) -> list[dict[str, Any]]:
    fixed_entries = validate_fixed(fixed)
    candidate_entries = validate_candidate(candidate)
    if sha256_hex(canonical_bytes(fixed)) != candidate["base_contract_digest"]:
        raise ContractError("candidate base_contract_digest does not match fixed contract bytes")
    if len(fixed_entries) != len(candidate_entries):
        raise ContractError("candidate is missing a fixed family repository")
    fixed_by_name = {item["name"]: item for item in fixed_entries}
    for candidate_entry in candidate_entries:
        fixed_entry = fixed_by_name.get(candidate_entry["name"])
        if fixed_entry is None:
            raise ContractError(f"candidate contains unknown family repository: {candidate_entry['name']}")
        for field in ("repository", "checkout_path", "artifacts"):
            if candidate_entry[field] != fixed_entry[field]:
                raise ContractError(
                    f"candidate {candidate_entry['name']} changed fixed {field} metadata"
                )
        if candidate_entry["base_revision"] != fixed_entry["revision"]:
            raise ContractError(f"candidate base revision mismatch for {candidate_entry['name']}")
    return candidate_entries


def _repository_url(slug: str) -> str:
    return f"https://github.com/{slug}.git"


def resolve_remote_head(slug: str, branch: str) -> str:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--refs", _repository_url(slug), f"refs/heads/{branch}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"unable to resolve {slug}@{branch}: {exc}") from exc
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) < 2 or rows[0][1] != f"refs/heads/{branch}":
        raise ContractError(f"{slug}@{branch} did not resolve to exactly one branch head")
    return _sha(rows[0][0], f"resolved {slug}@{branch}")


def _local_checkout(local_root: Path, slug: str) -> Path:
    repository_name = slug.rsplit("/", 1)[-1].removesuffix(".git")
    candidates = [local_root / repository_name]
    candidates.extend(
        path
        for path in local_root.iterdir()
        if path.is_dir() and path.name.casefold() == repository_name.casefold()
    )
    for path in candidates:
        if path.is_dir() and (path / ".git").exists():
            return path
    raise ContractError(f"local checkout not found for {slug} under {local_root}")


def resolve_local_head(local_root: Path, slug: str, branch: str) -> str:
    checkout = _local_checkout(local_root, slug)
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", f"refs/remotes/origin/{branch}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"unable to resolve local {slug}@{branch}: {exc}") from exc
    return _sha(result.stdout.strip(), f"resolved local {slug}@{branch}")


def propose(fixed: dict[str, Any], branch: str, local_root: Path | None = None) -> dict[str, Any]:
    entries = validate_fixed(fixed)
    candidate_entries: list[dict[str, Any]] = []
    for entry in entries:
        candidate_entries.append(
            {
                "name": entry["name"],
                "repository": entry["repository"],
                "branch": branch,
                "base_revision": entry["revision"],
                "candidate_revision": (
                    resolve_local_head(local_root, entry["repository"], branch)
                    if local_root is not None
                    else resolve_remote_head(entry["repository"], branch)
                ),
                "checkout_path": entry["checkout_path"],
                "artifacts": entry["artifacts"],
            }
        )
    candidate = {
        "schema_version": CANDIDATE_SCHEMA,
        "source": "moving-head",
        "base_schema_version": FIXED_SCHEMA,
        "base_contract_digest": sha256_hex(canonical_bytes(fixed)),
        "branch": branch,
        "repositories": candidate_entries,
    }
    validate_against_fixed(candidate, fixed)
    return candidate


def write_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def promoted_fixed_document(
    candidate: dict[str, Any], fixed: dict[str, Any]
) -> dict[str, Any]:
    """Return a proposed fixed document without changing either input file."""
    candidate_entries = validate_against_fixed(candidate, fixed)
    by_name = {item["name"]: item for item in candidate_entries}
    result = json.loads(json.dumps(fixed))
    for entry in result["repositories"]:
        entry["revision"] = by_name[entry["name"]]["candidate_revision"]
    return result


def _output_path() -> Path | None:
    value = __import__("os").environ.get("GITHUB_OUTPUT")
    return Path(value) if value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    propose_parser = subparsers.add_parser("propose", help="resolve moving heads into a candidate set")
    propose_parser.add_argument("--contracts", type=Path, default=ROOT / "family-contracts.json")
    propose_parser.add_argument("--output", type=Path, required=True)
    propose_parser.add_argument("--branch", default="main")
    propose_parser.add_argument("--local-root", type=Path)

    validate_parser = subparsers.add_parser("validate", help="validate a fixed or candidate set")
    validate_parser.add_argument("document", type=Path)
    validate_parser.add_argument("--fixed", type=Path)
    validate_parser.add_argument("--checkouts-root", type=Path)

    promote_parser = subparsers.add_parser(
        "promote", help="write a reviewed candidate as a separate proposed fixed document"
    )
    promote_parser.add_argument("--candidate", required=True, type=Path)
    promote_parser.add_argument("--fixed", required=True, type=Path)
    promote_parser.add_argument("--output", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        document = _read(
            args.contracts
            if args.command == "propose"
            else args.document
            if args.command == "validate"
            else args.candidate
        )
        if args.command == "propose":
            fixed = validate_fixed(document)
            del fixed  # validation is repeated by propose for one authoritative path
            candidate = propose(document, args.branch, args.local_root)
            write_document(args.output, candidate)
            output = _output_path()
            if output:
                with output.open("a", encoding="utf-8") as handle:
                    handle.write(f"candidate-file={args.output.resolve()}\n")
                    for entry in candidate["repositories"]:
                        key = re.sub(r"[^A-Za-z0-9_]", "_", entry["name"])
                        handle.write(f"{key}={entry['candidate_revision']}\n")
            moved = sum(
                item["base_revision"] != item["candidate_revision"]
                for item in candidate["repositories"]
            )
            print(json.dumps({"candidate": str(args.output), "moved": moved}, sort_keys=True))
            return 0

        if args.command == "promote":
            if args.output.resolve() == args.fixed.resolve():
                raise ContractError(
                    "promotion output must be a separate file; review cannot be bypassed"
                )
            fixed = _read(args.fixed)
            proposed = promoted_fixed_document(document, fixed)
            write_document(args.output, proposed)
            print(json.dumps({"proposed": str(args.output), "repositories": len(proposed["repositories"])}, sort_keys=True))
            return 0

        if document.get("schema_version") == FIXED_SCHEMA:
            entries = validate_fixed(document)
        else:
            entries = validate_candidate(document)
            if args.fixed:
                validate_against_fixed(document, _read(args.fixed))
        if args.checkouts_root:
            root = args.checkouts_root.resolve()
            for entry in entries:
                checkout = root / entry["checkout_path"]
                if not checkout.is_dir():
                    raise ContractError(f"missing checkout: {checkout}")
                actual = subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                expected = entry.get("revision", entry.get("candidate_revision"))
                if actual != expected:
                    raise ContractError(f"{entry['name']} checkout is {actual}, expected {expected}")
                for artifact in entry["artifacts"]:
                    if not (checkout / artifact).is_file():
                        raise ContractError(f"missing {entry['name']} artifact: {artifact}")
        print(json.dumps({"valid": True, "repositories": len(entries)}, sort_keys=True))
        return 0
    except (ContractError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
