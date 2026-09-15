"""Transport-only locator for the family-owned Commons contract."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


def _module() -> ModuleType:
    configured = os.environ.get("MNCS_COMMONS_ROOT")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(__file__).resolve().parents[2] / "MNCS-Commons")
    for root in candidates:
        source = root / "src"
        module_path = source / "mncs_commons" / "verification_plan.py"
        if module_path.is_file():
            # Load the canonical module by file identity instead of relying on
            # the process-wide ``mncs_commons`` package name.  Actions tests
            # and adapters may temporarily install compatibility package
            # stubs, and those must not change the transport authority.
            name = "_mncs_commons_verification_plan_canonical"
            existing = sys.modules.get(name)
            if existing is not None:
                return existing
            spec = importlib.util.spec_from_file_location(name, module_path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load canonical verification-plan module: {module_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
    raise RuntimeError(
        "canonical MNCS-Commons verification-plan contract is unavailable; "
        "set MNCS_COMMONS_ROOT to a checked-out Commons repository"
    )


def validate_plan(value: Any, **kwargs: Any) -> dict[str, Any]:
    return _module().validate_plan(value, **kwargs)


def plan_identity(value: Any) -> str:
    return _module().plan_identity(value)


def _graph_module() -> ModuleType:
    verification_module = _module()
    module_path = Path(verification_module.__file__).with_name("family_graph.py")
    name = "_mncs_commons_family_graph_canonical"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load canonical family-graph module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_family_graph(path: Path) -> dict[str, Any]:
    return _graph_module().load_graph(path)


def generate_family_graph(
    declarations: list[Mapping[str, Any]], repositories: list[Mapping[str, str]]
) -> dict[str, Any]:
    return _graph_module().generate_graph(declarations, repositories)


def bind_declaration_evidence(root: Path, value: Any) -> dict[str, Any]:
    return _graph_module().bind_declaration_evidence(root, value)


def declaration_identity(value: Any) -> str:
    return _graph_module().declaration_identity(value)


def validate_verification_manifest(value: Any, *, repository_id: str) -> dict[str, Any]:
    return _graph_module().validate_verification_manifest(value, repository_id=repository_id)
