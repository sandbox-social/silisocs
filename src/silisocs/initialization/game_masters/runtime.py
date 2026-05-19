"""Game-master initialization phase and GM-owned initializer implementations."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.environments.backends.social.network import generate_follow_network
from silisocs.environments.gm.components.base import InitializeComponent
from silisocs.initialization.context import InitializationContext


class GameMasterInitializer(InitializeComponent):
    """Initialize component owned by one native game master."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        game_master: Any,
        context: InitializationContext,
    ) -> None:
        """Initialize backend/environment state for one game master."""
        del agents, game_master, context


class NoOpGameMasterInitializer(GameMasterInitializer):
    """GM initializer that intentionally does nothing."""


class AppInitializeGameMasterInitializer(GameMasterInitializer):
    """Initialize generic backend apps through their public initialize hook."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        game_master: Any,
        context: InitializationContext,
    ) -> None:
        app = _app_for_game_master(game_master)
        if app is None:
            raise TypeError(f"Game master {game_master!r} has no backend app.")
        initializer = getattr(app, "initialize", None)
        if not callable(initializer):
            raise TypeError(f"Backend app {app!r} has no initialize(...) method.")
        initializer(
            agent_names=[agent.name for agent in agents],
            sim_roles=dict(context.sim_roles or {}),
            social_network=dict(context.social_network or {}),
        )


class SocialMediaGameMasterInitializer(GameMasterInitializer):
    """Initialize social-media users, graph/subreddit state, and action metadata."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        game_master: Any,
        context: InitializationContext,
    ) -> None:
        app = _app_for_game_master(game_master)
        if app is None:
            raise TypeError(f"Game master {game_master!r} has no backend app.")
        _bind_agent_action_metadata(
            agents=agents,
            app=app,
            action_output_mode=str(getattr(game_master, "action_output_mode", "") or ""),
        )
        setup = getattr(app, "setup_social_state", None)
        if not callable(setup):
            raise TypeError(
                f"Social backend app {app.__class__.__name__} must expose setup_social_state(...)."
            )
        agent_names = [agent.name for agent in agents]
        sim_roles = dict(context.sim_roles or {})
        social_network = dict(context.social_network or {})
        following_graph = generate_follow_network(agent_names, sim_roles, social_network)
        setup(
            agent_names=agent_names,
            sim_roles=sim_roles,
            social_network=social_network,
            following_graph=following_graph,
            agent_bios=dict(context.agent_bios or {}),
        )
        logger = getattr(app, "_log_action_event", None)
        if callable(logger):
            logger(
                "system", "initialize", dict(getattr(app, "_last_initialization_stats", {}) or {})
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


def _app_for_game_master(game_master: Any) -> Any | None:
    return getattr(
        game_master,
        "app",
        getattr(game_master, "sm_app", getattr(game_master, "env_app", None)),
    )


def _bind_agent_action_metadata(
    *,
    agents: Sequence[Agent],
    app: Any,
    action_output_mode: str,
) -> None:
    catalog = app.action_catalog() if callable(getattr(app, "action_catalog", None)) else []
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
