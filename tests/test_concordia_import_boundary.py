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


def test_docs_and_configs_do_not_use_removed_checkpoint_or_html_keys() -> None:
    checked_roots = [
        SRC_ROOT / "conf",
        PROJECT_ROOT / "scenarios",
        PROJECT_ROOT / "replications",
        PROJECT_ROOT / "docs",
        PROJECT_ROOT / "agent_docs",
        PROJECT_ROOT / ".github",
    ]
    forbidden = (
        "write_html_log",
        "checkpoint_replay",
        "resume_file",
        "resume_step",
        "prefab_module",
        "silisocs.agents.entity",
        "silisocs.agents.fixed_entity",
        "SMAct",
        "sample_tool_call",
        "runtime.models",
        "runtime.logs",
        "runtime.html",
        "runtime.helpers",
        "runtime.dataclasses",
        "runtime.factories",
        "runtime.action_prompts",
        "runtime.projection",
        "sim.engine.action_loop",
        "sim.engine.probe_schedule",
        "engine.probe_schedule",
        "flow_routing",
        "silisocs.agents.builders",
        "build_agents(",
        "mkdocs build",
    )
    offenders: list[str] = []
    for root in checked_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".md", ".yaml", ".yml"}:
                continue
            if path.name.endswith("_old.md"):
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{marker}")
    readme = PROJECT_ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    for marker in forbidden:
        if marker in text:
            offenders.append(f"{readme.relative_to(PROJECT_ROOT)}:{marker}")

    assert sorted(offenders) == []


def test_shipped_configs_do_not_use_removed_recommend_slot() -> None:
    checked_roots = [
        SRC_ROOT / "conf",
        PROJECT_ROOT / "scenarios",
        PROJECT_ROOT / "replications",
    ]
    offenders = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for root in checked_roots
        if root.exists()
        for path in root.rglob("*.yaml")
        if "recommend:" in path.read_text(encoding="utf-8")
    )

    assert offenders == []


def test_config_generators_emit_current_engine_keys() -> None:
    checked_paths = [
        SRC_ROOT / "dashboard" / "launch_app.py",
        SRC_ROOT / "scenario_gen" / "writer.py",
    ]
    forbidden = (
        "engine.action_loop",
        "sim.engine.action_loop",
        "engine.probe_schedule",
        "sim.engine.probe_schedule",
        "engine.preset",
        "sim.engine.preset",
        "flow_routing",
        "done_token",
        "all_entities",
        "queries:",
        "query_type",
        "query_data",
        "include_entities",
        "exclude_entities",
    )
    offenders: list[str] = []
    for path in checked_paths:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{marker}")

    assert offenders == []

    dashboard_text = (SRC_ROOT / "dashboard" / "launch_app.py").read_text(encoding="utf-8")
    assert "probes.probes" in dashboard_text
    assert "probes_include_agents" in dashboard_text
    assert "probes_exclude_agents" in dashboard_text
