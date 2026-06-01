"""Concordia compatibility must stay isolated behind the optional adapter package."""

# ruff: noqa: D103

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "silisocs"
SCAN_ROOTS = [
    SRC_ROOT,
    PROJECT_ROOT / "tests",
]
ALLOWED_PREFIXES = {
    ("adapters", "concordia"),
    ("agents", "concordia.py"),
}


def _imports_concordia(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "concordia" or alias.name.startswith("concordia.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "concordia" or module.startswith("concordia."):
                return True
    return False


def _imports_adapter(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "silisocs.adapters.concordia"
                or alias.name.startswith("silisocs.adapters.concordia.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "silisocs.adapters.concordia" or module.startswith(
                "silisocs.adapters.concordia."
            ):
                return True
    return False


def _is_allowed(path: Path, root: Path) -> bool:
    if root != SRC_ROOT:
        return False
    rel = path.relative_to(root)
    parts = rel.parts
    return any(parts[: len(prefix)] == prefix for prefix in ALLOWED_PREFIXES)


def test_concordia_imports_are_adapter_only() -> None:
    """Native runtime/tests must not import Concordia directly."""
    offenders = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for root in SCAN_ROOTS
        for path in root.rglob("*.py")
        if _imports_concordia(path) and not _is_allowed(path, root)
    )

    assert offenders == []


def test_default_runtime_does_not_import_concordia_adapter() -> None:
    offenders = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in SRC_ROOT.rglob("*.py")
        if _imports_adapter(path) and not _is_allowed(path, SRC_ROOT)
    )

    assert offenders == []


def test_native_gm_components_do_not_expose_concordia_lifecycle_hooks() -> None:
    component_root = SRC_ROOT / "environments" / "gm" / "components"
    forbidden = ("def pre_act", "def post_act", "def set_entity", "def get_entity")
    offenders: list[str] = []
    for path in component_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{marker}")

    assert offenders == []


def test_native_code_does_not_import_removed_gm_routing_module() -> None:
    marker = "components." + "routing"
    offenders = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for root in SCAN_ROOTS
        for path in root.rglob("*.py")
        if marker in path.read_text(encoding="utf-8")
    )

    assert offenders == []


def test_native_code_does_not_import_removed_utility_modules() -> None:
    removed_modules = {
        "silisocs.runtime.action_prompts",
        "silisocs.runtime.agent_building",
        "silisocs.runtime.assembly",
        "silisocs.runtime.config",
        "silisocs.runtime.concurrency",
        "silisocs.runtime.dataclasses",
        "silisocs.runtime.factories",
        "silisocs.runtime.models",
        "silisocs.runtime.logs",
        "silisocs.runtime.checkpoints",
        "silisocs.runtime.projection",
        "silisocs.runtime.html",
        "silisocs.runtime.helpers",
        "silisocs.utils.media",
        "silisocs.utils.network",
        "silisocs.utils.misc",
        "silisocs.environments.gm.components.keys",
        "silisocs.environments.gm.components.update",
        "silisocs.environments.gm.shared_flow_game_master",
        "silisocs.simulation_engines.policies.action_chunk",
        "silisocs.agents.builders",
    }
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in removed_modules:
                            offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module in removed_modules:
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{module}")

    assert sorted(offenders) == []


def test_native_source_does_not_use_removed_backend_or_config_names() -> None:
    forbidden = (
        "write_" + "seed_toot",
        "silisocs." + "_utils",
        "env_" + "cfg(",
        "Social" + "MediaApp",
        "create_" + "social_media_app",
        "Local" + "LanguageModel",
        "language_models." + "local",
        "sm_" + "app",
        "env_" + "app",
        "sm_" + "user_data",
        "social_" + "media_action",
        "runtime_" + "config",
        "platform_" + "type",
        "create_" + "environment_app",
        "app_" + "class_path",
        "app_" + "params",
        "You are answering as",
        "in character",
    )
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if _is_allowed(path, SRC_ROOT):
            continue
        if path.relative_to(SRC_ROOT).as_posix() == "runtime/configuration/legacy.py":
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{marker}")

    assert sorted(offenders) == []
