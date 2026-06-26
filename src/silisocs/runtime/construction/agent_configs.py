"""Agent-builder selection for runtime construction."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf

from silisocs.runtime.construction.agent_builders import (
    AgentBuilder,
    PersonaPipelineAgentBuilder,
)
from silisocs.runtime.construction.agent_builders.common import validate_unique_agent_names
from silisocs.runtime.construction.specs import AgentConfig

_LOGGER = logging.getLogger(__name__)

_RESERVED_BUILDER_PARAMS = {"scenario_name", "project_root"}


def build_agent_configs(cfg: DictConfig) -> list[AgentConfig]:
    """Build agent specs with the configured agent builder."""
    builder_cls = _resolve_builder_class(OmegaConf.select(cfg, "agents.builder.class_path"))
    params = _builder_params(cfg)
    builder = builder_cls(cfg.agents, params=params)
    agents = validate_unique_agent_names(builder.build_agent_configs())
    _warn_on_agent_count_mismatch(cfg, len(agents))
    return agents


def _warn_on_agent_count_mismatch(cfg: DictConfig, actual: int) -> None:
    """Warn when the built agent count diverges from the declared ``num_agents``.

    ``num_agents`` is the declared total (used in the job name and reporting), but
    the actual number of agents is the sum of the per-class ``count`` values — it
    is neither capped nor padded to ``num_agents``. A divergence usually means the
    class counts do not sum to ``num_agents`` (a likely config mistake), so surface
    it instead of silently building a different number of agents than declared.
    """
    declared = OmegaConf.select(cfg, "num_agents", default=None)
    try:
        declared_int = int(declared)
    except (TypeError, ValueError):
        return
    if declared_int <= 0 or declared_int == actual:
        return
    _LOGGER.warning(
        "Built %d agent(s) but num_agents=%d. The actual agent count is the sum "
        "of the per-class `count` values; num_agents neither caps nor pads the "
        "total. Check that your persona_pipeline class counts sum to num_agents "
        "(unused classes should set count: 0).",
        actual,
        declared_int,
    )


def _resolve_builder_class(class_path: str | None) -> type[AgentBuilder]:
    if not class_path:
        return PersonaPipelineAgentBuilder
    module_path, name = str(class_path).rsplit(".", 1)
    builder_cls = getattr(importlib.import_module(module_path), name)
    if not isinstance(builder_cls, type) or not issubclass(builder_cls, AgentBuilder):
        raise TypeError(
            f"Configured agents.builder.class_path `{class_path}` must subclass "
            "silisocs.runtime.construction.agent_builders.AgentBuilder."
        )
    return builder_cls


def _builder_params(cfg: DictConfig) -> dict[str, Any]:
    user_params = OmegaConf.select(cfg, "agents.builder.params", default={}) or {}
    params = (
        OmegaConf.to_container(user_params, resolve=True)
        if isinstance(user_params, (DictConfig, ListConfig))
        else dict(user_params)
    )
    if not isinstance(params, dict):
        raise ValueError("agents.builder.params must be a mapping.")
    reserved = sorted(_RESERVED_BUILDER_PARAMS.intersection(params))
    if reserved:
        raise ValueError(
            "agents.builder.params may not define reserved runtime param(s): "
            f"{', '.join(reserved)}."
        )
    return {
        **params,
        "scenario_name": str(OmegaConf.select(cfg, "scenario_name", default="default")),
        "project_root": str(Path(__file__).resolve().parents[4]),
    }
