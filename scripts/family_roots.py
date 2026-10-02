"""Resolve optional MNCS family members for integration tests.

The hosted canaries use an injected family root when CI checks out several
repositories together.  Local runs may use the nearest common ancestor.  No
machine-specific absolute checkout is part of the test contract.
"""

from __future__ import annotations

import os
from pathlib import Path


def discover_family_repo(
    name: str,
    *,
    environment_name: str,
    start: Path,
) -> Path:
    configured = os.environ.get(environment_name)
    if configured:
        return Path(configured).expanduser().resolve()
    family_root = os.environ.get("MNCS_FAMILY_ROOT")
    if family_root:
        candidate = Path(family_root).expanduser().resolve() / name
        if candidate.is_dir():
            return candidate
    for parent in (start.resolve(), *start.resolve().parents):
        for candidate in (parent / name, parent / "family" / name):
            if candidate.is_dir():
                return candidate
    return start.resolve().parent / name


def discover_stdlib_root(*, start: Path) -> Path:
    """Resolve stdlib ownership; explicit selection never falls back."""
    configured = os.environ.get('MNCS_LIBRARY_ROOT')
    if configured:
        return Path(configured).expanduser().resolve()
    provider = os.environ.get('MNCS_STDLIB_ROOT')
    if provider:
        return Path(provider).expanduser().resolve() / 'library'
    stdlib = discover_family_repo('mncs-stdlib', environment_name='MNCS_STDLIB_REPO', start=start)
    if (stdlib / 'library').is_dir():
        return stdlib / 'library'
    # Legacy owners participate only when no dedicated provider exists.
    language = discover_family_repo('mncs-language', environment_name='MNCS_LANGUAGE_REPO', start=start)
    return language / 'library'
