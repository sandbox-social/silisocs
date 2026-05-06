"""Agent builder resolution utilities.

This module contains helpers used by the runner to resolve and invoke a
scenario-specific agent builder. Scenarios may provide a ``builders.py``
module that defines a <ScenarioName>AgentBuilder class; when present that
builder is used. Otherwise the project falls back to
:class:`silisocs.agents.builders.BaseAgentBuilder`.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

from omegaconf import DictConfig

from silisocs.agents.builders import BaseAgentBuilder


def _resolve_builder_class(cfg: DictConfig) -> type[Any]:
    """Resolve the agent builder class for a scenario.

    The function attempts the following, in order:

    1. Import ``silisocs.scenarios.<scenario>.builders`` and read
       ``<Scenario>AgentBuilder``.
    2. Load ``scenarios/<scenario>/builders.py`` from the project root and
       read ``<Scenario>AgentBuilder``.
    3. Fall back to :class:`silisocs.agents.builders.BaseAgentBuilder`.

    Parameters
    ----------
    cfg:
        The composed Hydra config containing ``scenario_name``.

    Returns
    -------
    type
        The builder class to instantiate.
    """

    scenario_name = cfg.scenario_name
    builder_class_name = f"{scenario_name.title()}AgentBuilder"

    # 1. Try in-package builder.
    try:
        mod = importlib.import_module(f"silisocs.scenarios.{scenario_name}.builders")
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
    """Build agent instance configurations for the scenario.

    Parameters
    ----------
    cfg:
        The composed Hydra config used to initialize the selected builder.

    Returns
    -------
    list
        A list of :class:`omegaconf.DictConfig`-like instance configurations
        representing agents to be constructed by the simulation runtime.
    """

    builder_cls = _resolve_builder_class(cfg)
    builder = builder_cls(cfg.agents)
    return builder.build_agents()
