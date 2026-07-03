"""Factory helpers for configurable GM components loaded from YAML."""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Mapping
from typing import Any, cast

from silisocs.environments.gm.components.action_prompt import DefaultActionPromptComponent
from silisocs.environments.gm.components.app_update import AppUpdateComponent
from silisocs.environments.gm.components.base import (
    ActionPromptComponent,
    InitializeComponent,
    NextActingComponent,
    NoOpUpdateComponent,
    ObservationComponent,
    ResolveComponent,
    UpdateComponent,
)
from silisocs.environments.gm.components.next_acting import (
    AllAgentsNextActing,
    FixedOrderNextActing,
)
from silisocs.environments.gm.components.observe import (
    AppObservationComponent,
    EpisodeObservation,
)
from silisocs.environments.gm.components.resolve import (
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)
from silisocs.environments.gm.components.social_media.observe import TimelineMakeObservation
from silisocs.environments.gm.components.social_media.update import (
    SocialRecommendationUpdateComponent,
)
from silisocs.environments.gm.context import GameMasterContext
from silisocs.initialization.game_masters.runtime import (
    AppInitializeGameMasterInitializer,
    NoOpGameMasterInitializer,
    SocialMediaGameMasterInitializer,
)

_INITIALIZE_BUILT_INS = {
    "none": NoOpGameMasterInitializer,
    "disabled": NoOpGameMasterInitializer,
    "social_media": SocialMediaGameMasterInitializer,
    "app_initialize": AppInitializeGameMasterInitializer,
}

_ACTION_PROMPT_BUILT_INS = {
    "default": DefaultActionPromptComponent,
}

_OBSERVE_BUILT_INS = {
    "app_observation": AppObservationComponent,
    "timeline_every_turn": TimelineMakeObservation,
    "episode_only": EpisodeObservation,
}

_RESOLVE_BUILT_INS = {
    "parsed_action": ParsedActionResolveComponent,
    "generic_action": GenericActionResolveComponent,
    "tool_calling": ToolCallingResolveComponent,
}

_NEXT_ACTING_BUILT_INS = {
    "all_agents": AllAgentsNextActing,
    "fixed_order": FixedOrderNextActing,
}

# Config-derived activity models moved from this (environment) slot to the sim
# layer. A stale config naming them here gets a migration hint instead of a
# generic unknown-built-in error.
_MOVED_TO_PARTICIPATION = ("activity_markov", "activity_probability")


def _reject_moved_next_acting(slot_cfg: Mapping[str, Any] | None) -> None:
    built_in = str((dict(slot_cfg or {})).get("built_in") or "").strip()
    if built_in in _MOVED_TO_PARTICIPATION:
        raise ValueError(
            f"env.gm.components.next_acting.built_in='{built_in}' has moved to the sim "
            f"layer: set sim.engine.participation.built_in='{built_in}' (same params, "
            "minus agent_names) and use next_acting 'all_agents' or 'fixed_order' here. "
            "Participation filters the step roster before every GM's next_acting runs."
        )


_UPDATE_BUILT_INS = {
    "app_update": AppUpdateComponent,
    "disabled": NoOpUpdateComponent,
    "none": NoOpUpdateComponent,
    "social_recommendation": SocialRecommendationUpdateComponent,
}


_MULTI_INSTANCE_RESERVED_KEYS = {
    "built_in",
    "class_path",
    "params",
    "instances",
    "flow_map",
}


def _load_class(class_path: str) -> type[Any]:
    """Load and return a class from its fully-qualified path."""
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _build_from_slot(
    slot_cfg: Mapping[str, Any] | None,
    *,
    built_ins: Mapping[str, type[Any]],
    default_built_in: str,
    runtime_kwargs: Mapping[str, Any] | None = None,
) -> Any:
    """Build a component from a slot config, using class_path or a built-in name."""
    cfg = dict(slot_cfg or {})
    class_path = cfg.get("class_path")
    params = dict(cfg.get("params") or {})

    if class_path:
        cls = _load_class(str(class_path))
        all_kwargs = dict(runtime_kwargs or {})
        all_kwargs.update(params)
        return _instantiate_with_supported_kwargs(cls, all_kwargs, config_param_keys=params.keys())

    built_in = str(cfg.get("built_in") or default_built_in)
    if built_in not in built_ins:
        options = ", ".join(sorted(built_ins))
        raise ValueError(f"Unknown built_in '{built_in}'. Available: {options}")
    cls = built_ins[built_in]
    all_kwargs = dict(runtime_kwargs or {})
    all_kwargs.update(params)
    return _instantiate_with_supported_kwargs(cls, all_kwargs, config_param_keys=params.keys())


def _instantiate_with_supported_kwargs(
    cls: type[Any],
    kwargs: Mapping[str, Any],
    *,
    config_param_keys: Any = (),
) -> Any:
    """Instantiate a class using only kwargs supported by its constructor."""
    params = inspect.signature(cls.__init__).parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return cls(**dict(kwargs))

    supported = {
        name
        for name, param in params.items()
        if name != "self"
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    if "observation_params" in supported:
        filtered = {k: v for k, v in kwargs.items() if k in supported}
        return cls(**filtered)

    unsupported_config = sorted(set(config_param_keys) - supported)
    if unsupported_config:
        raise ValueError(
            f"Unsupported config param(s) for {cls.__module__}.{cls.__name__}: "
            f"{unsupported_config}. Supported params: {sorted(supported)}"
        )
    filtered = {k: v for k, v in kwargs.items() if k in supported}
    return cls(**filtered)


def _class_to_kebab_case(class_name: str) -> str:
    """Convert ClassName to kebab-case.

    Examples
    --------
        TimelineMakeObservation -> timeline_make_observation
        EpisodeObservation -> episode_observation
        ParsedActionResolveComponent -> parsed_action_resolve_component
    """
    kebab = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name)
    return kebab.lower()


def _slot_instances_cfg(slot_cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    cfg = dict(slot_cfg or {})
    if "instances" in cfg:
        if not isinstance(cfg["instances"], Mapping):
            raise TypeError("GM component `instances` must be a mapping.")
        return {str(key): value for key, value in dict(cfg["instances"]).items()}
    return {
        str(key): value for key, value in cfg.items() if key not in _MULTI_INSTANCE_RESERVED_KEYS
    }


def _instance_key(role: str, instance_name: str, component: Any) -> str:
    raw = str(instance_name or "").strip()
    if raw:
        return f"{role}__{raw}"
    return f"{role}__{_class_to_kebab_case(component.__class__.__name__)}"


def build_initialize_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext | None = None,
) -> InitializeComponent:
    """Build one GM initialize component."""
    return cast(
        InitializeComponent,
        _build_from_slot(
            slot_cfg,
            built_ins=_INITIALIZE_BUILT_INS,
            default_built_in="none",
            runtime_kwargs={"context": context},
        ),
    )


def build_initialize_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext | None = None,
) -> dict[str, InitializeComponent]:
    """Build multiple initialize component instances."""
    components: dict[str, InitializeComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_initialize_component(instance_config, context=context)
        components[_instance_key("initialize", instance_name, component)] = component
    return components


def build_action_prompt_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext | None = None,
    action_prompt_template: str,
    enable_tool_calling: bool,
    tool_calling_mode: str = "single",
) -> ActionPromptComponent:
    """Build one action-prompt component."""
    return cast(
        ActionPromptComponent,
        _build_from_slot(
            slot_cfg,
            built_ins=_ACTION_PROMPT_BUILT_INS,
            default_built_in="default",
            runtime_kwargs={
                "context": context,
                "backend": context.backend if context is not None else None,
                "action_prompt_template": action_prompt_template,
                "enable_tool_calling": enable_tool_calling,
                "tool_calling_mode": tool_calling_mode,
            },
        ),
    )


def build_action_prompt_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext | None = None,
    action_prompt_template: str,
    enable_tool_calling: bool,
    tool_calling_mode: str = "single",
) -> dict[str, ActionPromptComponent]:
    """Build multiple action-prompt component instances."""
    components: dict[str, ActionPromptComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_action_prompt_component(
            instance_config,
            context=context,
            action_prompt_template=action_prompt_template,
            enable_tool_calling=enable_tool_calling,
            tool_calling_mode=tool_calling_mode,
        )
        components[_instance_key("action_prompt", instance_name, component)] = component
    return components


def build_observe_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext,
    episode_observation_flow: str = "fixed_pre",
) -> dict[str, ObservationComponent]:
    """Build multiple observe component instances from config.

    Args:
        slots_cfg: Dict of {instance_name: instance_config}.
        Other args: Passed as runtime_kwargs to all instances.

    Returns
    -------
        Dict of {component_key: component_instance} where keys are
        "observe__{instance_name}".
    """
    components: dict[str, ObservationComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_observe_component(
            instance_config,
            context=context,
            episode_observation_flow=episode_observation_flow,
        )

        components[_instance_key("observe", instance_name, component)] = component

    return components


def build_observe_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext,
    episode_observation_flow: str = "fixed_pre",
) -> ObservationComponent:
    """Build a single observe component from slot config."""
    episode_observation_flows = (
        [episode_observation_flow]
        if isinstance(episode_observation_flow, str)
        else list(episode_observation_flow or [])
    )
    return cast(
        ObservationComponent,
        _build_from_slot(
            slot_cfg,
            built_ins=_OBSERVE_BUILT_INS,
            default_built_in="timeline_every_turn",
            runtime_kwargs={
                "context": context,
                "model": context.model,
                "agent_names": list(context.agent_names),
                "backend": context.backend,
                "agent_flow_tags": dict(context.agent_flow_tags),
                "episode_observation_flow": episode_observation_flow,
                "episode_observation_flows": episode_observation_flows,
                "observation_params": dict(
                    (slot_cfg or {}).get("params", {}) if isinstance(slot_cfg, Mapping) else {}
                ),
            },
        ),
    )


def build_resolve_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext,
    action_prompt_template: str,
    agents_by_name: Mapping[str, Any] | None = None,
) -> ResolveComponent:
    """Build resolve component from slot config."""
    return cast(
        ResolveComponent,
        _build_from_slot(
            slot_cfg,
            built_ins=_RESOLVE_BUILT_INS,
            default_built_in="parsed_action",
            runtime_kwargs={
                "context": context,
                "backend": context.backend,
                "model": context.model,
                "action_prompt_template": action_prompt_template,
                "agents_by_name": agents_by_name or {agent.name: agent for agent in context.agents},
                "agent_flow_tags": dict(context.agent_flow_tags),
            },
        ),
    )


def build_next_acting_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext,
    sim_roles: Mapping[str, str] | None = None,
) -> NextActingComponent:
    """Build next-acting component from slot config."""
    _reject_moved_next_acting(slot_cfg)
    return cast(
        NextActingComponent,
        _build_from_slot(
            slot_cfg,
            built_ins=_NEXT_ACTING_BUILT_INS,
            default_built_in="all_agents",
            runtime_kwargs={
                "context": context,
                "agent_names": list(context.agent_names),
                "sim_roles": dict(sim_roles or {}),
                "sequence": list(context.agent_names),
            },
        ),
    )


def build_update_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext | None = None,
    backend_type: str | None = None,
) -> UpdateComponent:
    """Build update component from slot config."""
    return cast(
        UpdateComponent,
        _build_from_slot(
            slot_cfg,
            built_ins=_UPDATE_BUILT_INS,
            default_built_in="none",
            runtime_kwargs={
                "context": context,
                "backend": context.backend if context is not None else None,
                "backend_type": backend_type,
            }
            if context is not None
            else {
                "backend_type": backend_type,
            },
        ),
    )


def build_resolve_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext,
    action_prompt_template: str,
    agents_by_name: Mapping[str, Any] | None = None,
) -> dict[str, ResolveComponent]:
    """Build multiple resolve component instances."""
    components: dict[str, ResolveComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_resolve_component(
            instance_config,
            context=context,
            action_prompt_template=action_prompt_template,
            agents_by_name=agents_by_name,
        )
        components[_instance_key("resolve", instance_name, component)] = component
    return components


def build_next_acting_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext,
    sim_roles: Mapping[str, str] | None = None,
) -> dict[str, NextActingComponent]:
    """Build multiple next-acting component instances."""
    components: dict[str, NextActingComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_next_acting_component(
            instance_config,
            context=context,
            sim_roles=sim_roles,
        )
        components[_instance_key("next_acting", instance_name, component)] = component
    return components


def build_update_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    context: GameMasterContext | None = None,
    backend_type: str | None = None,
) -> dict[str, UpdateComponent]:
    """Build multiple update component instances."""
    components: dict[str, UpdateComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_update_component(
            instance_config,
            context=context,
            backend_type=backend_type,
        )
        components[_instance_key("update", instance_name, component)] = component
    return components
