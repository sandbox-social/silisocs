"""Factory helpers for configurable GM components loaded from YAML."""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Mapping
from typing import Any

from silisocs.environments.gm.components.action_prompt import DefaultActionPromptComponent
from silisocs.environments.gm.components.base import (
    BaseComponent,
    NoOpUpdateComponent,
)
from silisocs.environments.gm.components.next_acting import (
    ActivityMarkovNextActing,
    ActivityProbabilityNextActing,
    AllAgentsNextActing,
    FixedOrderNextActing,
)
from silisocs.environments.gm.components.observe import (
    AppObservationComponent,
    EpisodeObservation,
    TimelineMakeObservation,
)
from silisocs.environments.gm.components.resolve import (
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)
from silisocs.environments.gm.components.update import SocialRecommendationUpdateComponent
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
    "activity_markov": ActivityMarkovNextActing,
    "activity_probability": ActivityProbabilityNextActing,
    "all_agents": AllAgentsNextActing,
    "fixed_order": FixedOrderNextActing,
}

_UPDATE_BUILT_INS = {
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
    """_load_class.

    :param str class_path:
    :type class_path: str

    :returns: type[Any]
    :rtype: type[Any]
    """
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
    """_build_from_slot.

    :param Mapping[str, Any] | None slot_cfg:
    :type slot_cfg: Mapping[str, Any] | None

    :returns: Any
    :rtype: Any
    """
    cfg = dict(slot_cfg or {})
    if "flows" in cfg:
        raise ValueError(
            "`flows` field overrides have been removed from GM component configs. "
            "Use component `instances` plus `flow_map` for flow-specific behavior."
        )
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
    # Insert underscore before uppercase letters (except first)
    kebab = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name)
    return kebab.lower()


def _slot_instances_cfg(slot_cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    cfg = dict(slot_cfg or {})
    if "flows" in cfg:
        raise ValueError(
            "`flows` field overrides have been removed from GM component configs. "
            "Use component `instances` plus `flow_map` for flow-specific behavior."
        )
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
) -> BaseComponent:
    """Build one GM initialize component."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_INITIALIZE_BUILT_INS,
        default_built_in="none",
    )


def build_initialize_components(
    slots_cfg: Mapping[str, Any] | None = None,
) -> dict[str, BaseComponent]:
    """Build multiple initialize component instances."""
    components: dict[str, BaseComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_initialize_component(instance_config)
        components[_instance_key("initialize", instance_name, component)] = component
    return components


def build_action_prompt_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    app: Any | None = None,
    action_prompt_template: str,
    enable_tool_calling: bool,
) -> BaseComponent:
    """Build one action-prompt component."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_ACTION_PROMPT_BUILT_INS,
        default_built_in="default",
        runtime_kwargs={
            "app": app,
            "action_prompt_template": action_prompt_template,
            "enable_tool_calling": enable_tool_calling,
        },
    )


def build_action_prompt_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    app: Any | None = None,
    action_prompt_template: str,
    enable_tool_calling: bool,
) -> dict[str, BaseComponent]:
    """Build multiple action-prompt component instances."""
    components: dict[str, BaseComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_action_prompt_component(
            instance_config,
            app=app,
            action_prompt_template=action_prompt_template,
            enable_tool_calling=enable_tool_calling,
        )
        components[_instance_key("action_prompt", instance_name, component)] = component
    return components


def build_observe_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    model: Any,
    agent_names: list[str],
    sm_app: Any,
    env_app: Any | None = None,
    agent_flow_tags: dict[str, str] | None = None,
    episode_observation_flow: str = "fixed_pre",
    timeline_mode: str | None = None,
    timeline_posts: int = 10,
    timeline_config: Mapping[str, Any] | None = None,
) -> dict[str, BaseComponent]:
    """Build multiple observe component instances from config.

    Args:
        slots_cfg: Dict of {instance_name: instance_config}.
        Other args: Passed as runtime_kwargs to all instances.

    Returns
    -------
        Dict of {component_key: component_instance} where keys are
        "observe__{instance_name}".
    """
    components: dict[str, BaseComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_observe_component(
            instance_config,
            model=model,
            agent_names=agent_names,
            sm_app=sm_app,
            env_app=env_app if env_app is not None else sm_app,
            agent_flow_tags=agent_flow_tags,
            episode_observation_flow=episode_observation_flow,
            timeline_mode=timeline_mode,
            timeline_posts=timeline_posts,
            timeline_config=timeline_config,
        )

        components[_instance_key("observe", instance_name, component)] = component

    return components


def build_observe_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    model: Any,
    agent_names: list[str],
    sm_app: Any,
    env_app: Any | None = None,
    agent_flow_tags: dict[str, str] | None = None,
    episode_observation_flow: str = "fixed_pre",
    timeline_mode: str | None = None,
    timeline_posts: int = 10,
    timeline_config: Mapping[str, Any] | None = None,
) -> BaseComponent:
    """Build a single observe component from slot config."""
    episode_observation_flows = (
        [episode_observation_flow]
        if isinstance(episode_observation_flow, str)
        else list(episode_observation_flow or [])
    )
    return _build_from_slot(
        slot_cfg,
        built_ins=_OBSERVE_BUILT_INS,
        default_built_in="timeline_every_turn",
        runtime_kwargs={
            "model": model,
            "agent_names": agent_names,
            "sm_app": sm_app,
            "env_app": env_app if env_app is not None else sm_app,
            "agent_flow_tags": agent_flow_tags,
            "episode_observation_flow": episode_observation_flow,
            "episode_observation_flows": episode_observation_flows,
            "timeline_mode": timeline_mode,
            "timeline_posts": timeline_posts,
            "timeline_config": dict(timeline_config or {}),
            "observation_params": dict(
                (slot_cfg or {}).get("params", {}) if isinstance(slot_cfg, Mapping) else {}
            ),
        },
    )


def build_resolve_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    sm_app: Any,
    model: Any,
    action_prompt_template: str,
    agents_by_name: Mapping[str, Any] | None = None,
) -> BaseComponent:
    """Build resolve component from slot config."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_RESOLVE_BUILT_INS,
        default_built_in="parsed_action",
        runtime_kwargs={
            "sm_app": sm_app,
            "model": model,
            "action_prompt_template": action_prompt_template,
            "agents_by_name": agents_by_name or {},
        },
    )


def build_next_acting_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    agent_names: list[str],
    activity_transition_rates: Mapping[str, Mapping[str, float]],
) -> BaseComponent:
    """Build next-acting component from slot config."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_NEXT_ACTING_BUILT_INS,
        default_built_in="activity_markov",
        runtime_kwargs={
            "agent_names": agent_names,
            "activity_transition_rates": activity_transition_rates,
            "sequence": agent_names,
        },
    )


def build_update_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    sm_app: Any | None = None,
    platform_type: str | None = None,
    timeline_mode: str | None = None,
) -> BaseComponent:
    """Build update component from slot config."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_UPDATE_BUILT_INS,
        default_built_in="none",
        runtime_kwargs={
            "sm_app": sm_app,
            "platform_type": platform_type,
            "timeline_mode": timeline_mode,
        }
        if sm_app
        else {
            "platform_type": platform_type,
            "timeline_mode": timeline_mode,
        },
    )


def build_resolve_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    sm_app: Any,
    model: Any,
    action_prompt_template: str,
    agents_by_name: Mapping[str, Any] | None = None,
) -> dict[str, BaseComponent]:
    """Build multiple resolve component instances."""
    components: dict[str, BaseComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_resolve_component(
            instance_config,
            sm_app=sm_app,
            model=model,
            action_prompt_template=action_prompt_template,
            agents_by_name=agents_by_name,
        )
        components[_instance_key("resolve", instance_name, component)] = component
    return components


def build_next_acting_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    agent_names: list[str],
    activity_transition_rates: Mapping[str, Mapping[str, float]],
) -> dict[str, BaseComponent]:
    """Build multiple next-acting component instances."""
    components: dict[str, BaseComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_next_acting_component(
            instance_config,
            agent_names=agent_names,
            activity_transition_rates=activity_transition_rates,
        )
        components[_instance_key("next_acting", instance_name, component)] = component
    return components


def build_update_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    sm_app: Any | None = None,
    platform_type: str | None = None,
    timeline_mode: str | None = None,
) -> dict[str, BaseComponent]:
    """Build multiple update component instances."""
    components: dict[str, BaseComponent] = {}
    for instance_name, instance_config in _slot_instances_cfg(slots_cfg).items():
        component = build_update_component(
            instance_config,
            sm_app=sm_app,
            platform_type=platform_type,
            timeline_mode=timeline_mode,
        )
        components[_instance_key("update", instance_name, component)] = component
    return components
