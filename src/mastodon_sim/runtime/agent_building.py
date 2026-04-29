"""Builder resolution utilities for scenario agent construction."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

from omegaconf import DictConfig

from mastodon_sim.agents.builders import BaseAgentBuilder


def _resolve_builder_class(cfg: DictConfig) -> type[Any]:
    scenario_name = cfg.sim.scenario_name
    builder_class_name = f"{scenario_name.title()}AgentBuilder"

    # 1. Try in-package builder.
    try:
        mod = importlib.import_module(f"mastodon_sim.scenarios.{scenario_name}.builders")
        builder_cls = getattr(mod, builder_class_name, None)
        if builder_cls is not None:
            return builder_cls
    except (ImportError, ModuleNotFoundError):
        pass

    # 2. Try external scenarios/<name>/builders.py.
    pkg_root = Path(__file__).resolve().parents[1]
    project_root = pkg_root.parents[2]
    external_builder = project_root / "scenarios" / scenario_name / "builders.py"
    if external_builder.is_file():
        spec = importlib.util.spec_from_file_location(
            f"scenarios.{scenario_name}.builders",
            external_builder,
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            builder_cls = getattr(mod, builder_class_name, None)
            if builder_cls is not None:
                return builder_cls

    # 3. Fallback to generic builder.
    return BaseAgentBuilder


def build_agent_configs(cfg: DictConfig):
    """Build agent configs from scenario-configured builder class."""
    builder_cls = _resolve_builder_class(cfg)
    builder = builder_cls(cfg.agent_situation)
    return builder.build_agents()
