"""Game-master initialization phase and GM-owned initializer implementations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.environments.backends.base import SocialBackendApp
from silisocs.environments.backends.social.network import generate_follow_network
from silisocs.environments.gm.components.base import InitializeComponent
from silisocs.initialization.context import InitializationContext
from silisocs.runtime.class_loading import (
    instantiate_with_supported_kwargs as _instantiate_with_supported_kwargs,
)
from silisocs.runtime.class_loading import (
    load_class as _load_class,
)


class GameMasterInitializer(InitializeComponent):
    """Initialize component owned by one native game master."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        gm_context: Any,
        context: InitializationContext,
    ) -> None:
        """Initialize backend/environment state for one game master."""
        del agents, gm_context, context


class NoOpGameMasterInitializer(GameMasterInitializer):
    """GM initializer that intentionally does nothing."""


class AppInitializeGameMasterInitializer(GameMasterInitializer):
    """Initialize generic backend apps through their public initialize hook."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        gm_context: Any,
        context: InitializationContext,
    ) -> None:
        backend = _backend_for_context(gm_context)
        initializer = getattr(backend, "initialize", None)
        if not callable(initializer):
            raise TypeError(f"Backend {backend!r} has no initialize(...) method.")
        initializer(
            agent_names=[agent.name for agent in agents],
            sim_roles=dict(context.sim_roles or {}),
        )


class SocialMediaGameMasterInitializer(GameMasterInitializer):
    """Initialize social-media users, graph/subreddit state, and action metadata."""

    def __init__(self, *, graph: Mapping[str, Any] | None = None) -> None:
        self.graph = dict(graph or {})

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        gm_context: Any,
        context: InitializationContext,
    ) -> None:
        backend = _backend_for_context(gm_context)
        if not isinstance(backend, SocialBackendApp):
            raise TypeError(
                f"Social GM initializer requires SocialBackendApp, got "
                f"{backend.__class__.__name__}."
            )
        _bind_agent_action_metadata(
            agents=agents,
            backend=backend,
            action_output_mode="",
        )
        setup = getattr(backend, "setup_social_state", None)
        if not callable(setup):
            raise TypeError(
                f"Social backend {backend.__class__.__name__} must expose setup_social_state(...)."
            )
        agent_names = [agent.name for agent in agents]
        sim_roles = dict(context.sim_roles or {})
        setup_config = dict(self.graph)
        following_graph = generate_follow_network(agent_names, sim_roles, setup_config)
        setup(
            agent_names=agent_names,
            sim_roles=sim_roles,
            graph_config=setup_config,
            following_graph=following_graph,
            agent_bios=dict(context.agent_bios or {}),
        )
        logger = getattr(backend, "_log_action_event", None)
        if callable(logger):
            logger(
                "system",
                "initialize",
                dict(getattr(backend, "_last_initialization_stats", {}) or {}),
            )


class GameMasterInitializerStrategy:
    """Engine-level game-master initialization phase strategy."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        game_masters: Sequence[Any],
        context: InitializationContext,
    ) -> None:
        """Initialize all game masters before simulation setup."""
        del agents, game_masters, context


class DefaultGameMasterInitializerStrategy(GameMasterInitializerStrategy):
    """Call every game master's own initialize method."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        game_masters: Sequence[Any],
        context: InitializationContext,
    ) -> None:
        for game_master in game_masters:
            initializer = getattr(game_master, "initialize", None)
            if not callable(initializer):
                raise TypeError("Runtime game masters must expose initialize(agents, context).")
            initializer(agents=agents, context=context)


class NoOpGameMasterInitializerStrategy(GameMasterInitializerStrategy):
    """Engine-level GM initializer strategy that intentionally does nothing."""


def build_game_master_initializer(
    slot_cfg: Mapping[str, Any] | None = None,
) -> GameMasterInitializer:
    """Build a GM-owned initializer from one GM spec's initializer slot."""
    cfg = dict(slot_cfg or {})
    if not cfg:
        raise ValueError("Native GameMaster requires an explicit initializer slot.")
    class_path = str(cfg.get("class_path") or "").strip()
    params = dict(cfg.get("params") or {})
    if class_path:
        initializer = _instantiate_with_supported_kwargs(_load_class(class_path), params)
    else:
        built_in = str(cfg.get("built_in") or "").strip()
        built_ins: dict[str, type[GameMasterInitializer]] = {
            "none": NoOpGameMasterInitializer,
            "disabled": NoOpGameMasterInitializer,
            "social_media": SocialMediaGameMasterInitializer,
            "app_initialize": AppInitializeGameMasterInitializer,
        }
        if built_in not in built_ins:
            options = ", ".join(sorted(built_ins))
            raise ValueError(
                f"Unknown GameMaster initializer built_in '{built_in}'. Available: {options}"
            )
        initializer = _instantiate_with_supported_kwargs(built_ins[built_in], params)
    if not isinstance(initializer, GameMasterInitializer):
        raise TypeError(
            "Game master initializer must subclass "
            "silisocs.initialization.game_masters.runtime.GameMasterInitializer"
        )
    return initializer


def build_game_master_initializer_strategy(
    slot_cfg: Mapping[str, Any] | None = None,
) -> GameMasterInitializerStrategy:
    """Build the engine-level GM initialization strategy."""
    cfg = dict(slot_cfg or {})
    class_path = str(cfg.get("class_path") or "").strip()
    params = dict(cfg.get("params") or {})
    if class_path:
        strategy = _instantiate_with_supported_kwargs(_load_class(class_path), params)
    else:
        built_in = str(cfg.get("built_in") or "default").strip()
        built_ins: dict[str, type[GameMasterInitializerStrategy]] = {
            "default": DefaultGameMasterInitializerStrategy,
            "none": NoOpGameMasterInitializerStrategy,
            "disabled": NoOpGameMasterInitializerStrategy,
        }
        if built_in not in built_ins:
            options = ", ".join(sorted(built_ins))
            raise ValueError(
                f"Unknown sim.initialization.game_masters built_in '{built_in}'. "
                f"Available: {options}"
            )
        strategy = _instantiate_with_supported_kwargs(built_ins[built_in], params)
    if not isinstance(strategy, GameMasterInitializerStrategy):
        raise TypeError(
            "Game-master initialization strategy must subclass "
            "silisocs.initialization.game_masters.runtime.GameMasterInitializerStrategy"
        )
    return strategy


def _backend_for_context(gm_context: Any) -> Any:
    backend = getattr(gm_context, "backend", None)
    if backend is None:
        raise TypeError(f"Game master context {gm_context!r} has no backend.")
    return backend


def _bind_agent_action_metadata(
    *,
    agents: Sequence[Agent],
    backend: Any,
    action_output_mode: str,
) -> None:
    catalog = backend.action_catalog() if callable(getattr(backend, "action_catalog", None)) else []
    allowed_action_types = sorted(
        {
            str(item.get("selectable_name", "")).strip().upper()
            for item in catalog
            if str(item.get("selectable_name", "")).strip()
        }
    )
    for agent in agents:
        setter = getattr(agent, "set_allowed_action_types", None)
        existing = getattr(agent, "_allowed_action_types", None)
        if callable(setter) and not existing:
            setter(allowed_action_types)
        mode_setter = getattr(agent, "set_action_output_mode", None)
        if callable(mode_setter) and action_output_mode:
            mode_setter(action_output_mode)
