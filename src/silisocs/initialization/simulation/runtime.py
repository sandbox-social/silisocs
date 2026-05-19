"""Simulation initialization phase implementations."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.initialization.context import InitializationContext
from silisocs.initialization.simulation.seed_posts import (
    SeedPostProvider,
    build_seed_post_action,
    build_seed_post_provider,
)
from silisocs.runtime.language_models import LanguageModel
from silisocs.runtime.types import OutputType


class SimulationInitializer:
    """Base class for engine-owned simulation initialization."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        game_masters: Sequence[Any],
        model: LanguageModel,
        context: InitializationContext,
    ) -> None:
        """Initialize application-level simulation content."""
        del agents, game_masters, model, context


class NoOpSimulationInitializer(SimulationInitializer):
    """Simulation initializer that intentionally does nothing."""


class SeedPostsSimulationInitializer(SimulationInitializer):
    """Generate/load seed posts and resolve them through owning game masters."""

    def __init__(
        self,
        *,
        seed_post_provider: SeedPostProvider | None = None,
        type: str = "agent",  # noqa: A002
        params: Mapping[str, Any] | None = None,
        action_mappings: Mapping[str, Any] | None = None,
    ) -> None:
        provider_cfg = {"type": type, "params": dict(params or {})}
        self.seed_post_provider = seed_post_provider or build_seed_post_provider(provider_cfg)
        self.action_mappings = dict(action_mappings or {})

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        game_masters: Sequence[Any],
        model: LanguageModel,
        context: InitializationContext,
    ) -> None:
        del model
        if not game_masters:
            raise ValueError("Seed-post simulation initialization requires at least one GM.")
        seed_posts = self.seed_post_provider.get_seed_posts(agents=agents, context=context)
        for agent in agents:
            post_text = str(seed_posts.get(agent.name, "") or "").strip()
            if not post_text:
                continue
            game_master = _owning_game_master(agent.name, game_masters, context)
            platform_type = str(getattr(getattr(game_master, "app", None), "platform_type", ""))
            if not platform_type:
                platform_type = str(
                    getattr(getattr(game_master, "app", None), "name", lambda: "")()
                )
            action = build_seed_post_action(
                platform_type=platform_type,
                agent_name=agent.name,
                post_text=post_text,
                context=context,
                mapping_overrides=self.action_mappings,
            )
            if action.output_type == OutputType.SKIP:
                continue
            game_master.resolve_action(agent.name, action)


def build_simulation_initializer(
    slot_cfg: Mapping[str, Any] | None = None,
) -> SimulationInitializer:
    """Build a simulation initializer from ``sim.initialization.simulation`` config."""
    cfg = dict(slot_cfg or {})
    class_path = str(cfg.get("class_path") or "").strip()
    params = dict(cfg.get("params") or {})
    if class_path:
        initializer = _instantiate_with_supported_kwargs(_load_class(class_path), params)
    else:
        built_in = str(cfg.get("built_in") or "none").strip()
        built_ins: dict[str, type[SimulationInitializer]] = {
            "none": NoOpSimulationInitializer,
            "disabled": NoOpSimulationInitializer,
            "seed_posts": SeedPostsSimulationInitializer,
        }
        if built_in not in built_ins:
            options = ", ".join(sorted(built_ins))
            raise ValueError(
                f"Unknown sim.initialization.simulation built_in '{built_in}'. Available: {options}"
            )
        initializer = _instantiate_with_supported_kwargs(built_ins[built_in], params)
    if not isinstance(initializer, SimulationInitializer):
        raise TypeError(
            "Simulation initializer must subclass "
            "silisocs.initialization.simulation.runtime.SimulationInitializer"
        )
    return initializer


def _owning_game_master(
    agent_name: str,
    game_masters: Sequence[Any],
    context: InitializationContext,
) -> Any:
    flow = str(context.agent_flow_tags.get(agent_name, "default") or "default")
    for game_master in game_masters:
        orchestration = getattr(game_master, "gm_orchestration", {})
        owned_flows = (
            orchestration.get("owned_flows", []) if isinstance(orchestration, dict) else []
        )
        if flow in {str(item) for item in owned_flows}:
            return game_master
    return game_masters[0]


def _load_class(class_path: str) -> type[Any]:
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _instantiate_with_supported_kwargs(cls: type[Any], kwargs: Mapping[str, Any]) -> Any:
    params = inspect.signature(cls.__init__).parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return cls(**dict(kwargs))
    supported = {
        name
        for name, param in params.items()
        if name != "self"
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    unsupported = sorted(set(kwargs) - supported)
    if unsupported:
        raise ValueError(
            f"Unsupported config param(s) for {cls.__module__}.{cls.__name__}: "
            f"{unsupported}. Supported params: {sorted(supported)}"
        )
    return cls(**{key: value for key, value in kwargs.items() if key in supported})
