"""Factory helpers for configurable GM components loaded from YAML."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from typing import Any

from concordia.typing import entity_component

from mastodon_sim.environments.gm.components.base import BackendInitializer, FlowComponent
from mastodon_sim.environments.gm.components.initialize import DefaultBackendInitializer
from mastodon_sim.environments.gm.components.next_acting import (
    ActivityMarkovNextActing,
    ActivityProbabilityNextActing,
    AllEntitiesNextActing,
    FixedOrderNextActing,
)
from mastodon_sim.environments.gm.components.observe import (
    ChunkStartMakeObservation,
    EpisodeObservation,
    TimelineMakeObservation,
)
from mastodon_sim.environments.gm.components.recommend import RecommendationComponent
from mastodon_sim.environments.gm.components.resolve import (
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)

_OBSERVE_BUILT_INS = {
    "timeline_every_turn": TimelineMakeObservation,
    "chunk_start_only": ChunkStartMakeObservation,
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
    "all_entities": AllEntitiesNextActing,
    "fixed_order": FixedOrderNextActing,
}

_INITIALIZER_BUILT_INS = {
    "backend_default": DefaultBackendInitializer,
}

_RECOMMENDATION_BUILT_INS = {
    "recommendation_component": RecommendationComponent,
}


def _load_class(class_path: str) -> type[Any]:
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
    cfg = dict(slot_cfg or {})
    class_path = cfg.get("class_path")
    params = dict(cfg.get("params") or {})

    if class_path:
        cls = _load_class(str(class_path))
        all_kwargs = dict(runtime_kwargs or {})
        all_kwargs.update(params)
        return _instantiate_with_supported_kwargs(cls, all_kwargs)

    built_in = str(cfg.get("built_in") or default_built_in)
    if built_in not in built_ins:
        options = ", ".join(sorted(built_ins))
        raise ValueError(f"Unknown built_in '{built_in}'. Available: {options}")
    cls = built_ins[built_in]
    all_kwargs = dict(runtime_kwargs or {})
    all_kwargs.update(params)
    return _instantiate_with_supported_kwargs(cls, all_kwargs)


def _instantiate_with_supported_kwargs(cls: type[Any], kwargs: Mapping[str, Any]) -> Any:
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
    filtered = {k: v for k, v in kwargs.items() if k in supported}
    return cls(**filtered)


def _class_to_kebab_case(class_name: str) -> str:
    """Convert ClassName to kebab-case.

    Examples:
        TimelineMakeObservation -> timeline_make_observation
        EpisodeObservation -> episode_observation
        ParsedActionResolveComponent -> parsed_action_resolve_component
    """
    import re
    # Insert underscore before uppercase letters (except first)
    kebab = re.sub(r'(?<!^)(?=[A-Z])', '_', class_name)
    return kebab.lower()


def build_observe_components(
    slots_cfg: Mapping[str, Any] | None = None,
    *,
    model: Any,
    player_names: list[str],
    sm_app: Any,
    entity_action_flows: dict[str, str] | None = None,
    episode_observation_flow: str = "fixed_pre",
    timeline_strategy: str = "follower_chronological",
    timeline_config: Mapping[str, Any] | None = None,
) -> dict[str, entity_component.ContextComponent]:
    """Build multiple observe component instances from config.

    Args:
        slots_cfg: Dict of {instance_name: instance_config}.
        Other args: Passed as runtime_kwargs to all instances.

    Returns:
        Dict of {component_key: component_instance} where keys are
        "observe__{class_as_kebab_case}".
    """
    components = {}
    slots_cfg = dict(slots_cfg or {})

    if not slots_cfg:
        return components

    for instance_name, instance_config in slots_cfg.items():
        component = build_observe_component(
            instance_config,
            model=model,
            player_names=player_names,
            sm_app=sm_app,
            entity_action_flows=entity_action_flows,
            episode_observation_flow=episode_observation_flow,
            timeline_strategy=timeline_strategy,
            timeline_config=timeline_config,
        )

        # Auto-generate key: observe__timeline_make_observation
        class_name = component.__class__.__name__
        kebab_key = _class_to_kebab_case(class_name)
        full_key = f"observe__{kebab_key}"

        components[full_key] = component

    return components


def build_resolve_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    sm_app: Any,
    model: Any,
    call_to_action_str: str,
) -> entity_component.ContextComponent:
    """Build resolve component from slot config."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_RESOLVE_BUILT_INS,
        default_built_in="parsed_action",
        runtime_kwargs={
            "sm_app": sm_app,
            "model": model,
            "call_to_action_str": call_to_action_str,
        },
    )


def build_next_acting_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    player_names: list[str],
    activity_transition_rates: Mapping[str, Mapping[str, float]],
) -> entity_component.ContextComponent:
    """Build next-acting component from slot config."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_NEXT_ACTING_BUILT_INS,
        default_built_in="activity_markov",
        runtime_kwargs={
            "player_names": player_names,
            "activity_transition_rates": activity_transition_rates,
            "sequence": player_names,
        },
    )


def build_backend_initializer(slot_cfg: Mapping[str, Any] | None = None) -> BackendInitializer:
    return _build_from_slot(
        slot_cfg,
        built_ins=_INITIALIZER_BUILT_INS,
        default_built_in="backend_default",
    )


def build_recommendation_component(
    slot_cfg: Mapping[str, Any] | None = None,
) -> entity_component.ContextComponent:
    """Build recommendation component from slot config."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_RECOMMENDATION_BUILT_INS,
        default_built_in="recommendation_component",
    )


def initialize_component_multi_fields(
    component: entity_component.ContextComponent,
    component_config: Mapping[str, Any] | None,
) -> None:
    """Initialize multi-field values on FlowComponent if configured.

    Args:
        component: The component instance to initialize (may or may not be a FlowComponent)
        component_config: Configuration dict that may contain an 'entities' key with
                         entity-level field overrides. Expected format:
                         {
                           'built_in': '...',
                           'entities': {
                             'entity_name': {'field_name': field_value, ...},
                             ...
                           }
                         }

    Example config:
        observe:
          built_in: timeline_every_turn
          entities:
            alice:
              timeline_filter: "trusted"
            bob:
              timeline_filter: "all"
    """
    # Only process if component is a FlowComponent
    if not isinstance(component, FlowComponent):
        return

    # Extract entity configs if present
    component_cfg = dict(component_config or {})
    entities_cfg = component_cfg.get("entities")
    if not entities_cfg:
        return

    # Build entity -> {field_name: field_value} mapping
    entity_field_map: dict[str, dict[str, Any]] = {}
    for entity_name, field_config in entities_cfg.items():
        entity_field_map[entity_name] = dict(field_config or {})

    # Initialize component with the mapping
    component.set_multi_field_values(entity_field_map)
