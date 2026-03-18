"""Factory helpers for configurable GM components loaded from YAML."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from typing import Any

from concordia.typing import entity_component

from mastodon_sim.environments.gm.components.base import BackendInitializer
from mastodon_sim.environments.gm.components.initialize import DefaultBackendInitializer
from mastodon_sim.environments.gm.components.next_acting import (
    ActivityMarkovNextActing,
    AllEntitiesNextActing,
    FixedOrderNextActing,
)
from mastodon_sim.environments.gm.components.observe import (
    ChunkStartMakeObservation,
    TimelineMakeObservation,
)
from mastodon_sim.environments.gm.components.resolve import (
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)

_OBSERVE_BUILT_INS = {
    "timeline_every_turn": TimelineMakeObservation,
    "chunk_start_only": ChunkStartMakeObservation,
}

_RESOLVE_BUILT_INS = {
    "parsed_action": ParsedActionResolveComponent,
    "generic_action": GenericActionResolveComponent,
    "tool_calling": ToolCallingResolveComponent,
}

_NEXT_ACTING_BUILT_INS = {
    "activity_markov": ActivityMarkovNextActing,
    "all_entities": AllEntitiesNextActing,
    "fixed_order": FixedOrderNextActing,
}

_INITIALIZER_BUILT_INS = {
    "backend_default": DefaultBackendInitializer,
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


def build_observe_component(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    model: Any,
    player_names: list[str],
    sm_app: Any,
    entity_action_flows: dict[str, str] | None = None,
    episode_observation_flows: list[str] | None = None,
) -> entity_component.ContextComponent:
    """Build make-observation component from slot config."""
    return _build_from_slot(
        slot_cfg,
        built_ins=_OBSERVE_BUILT_INS,
        default_built_in="timeline_every_turn",
        runtime_kwargs={
            "model": model,
            "player_names": player_names,
            "sm_app": sm_app,
            "entity_action_flows": entity_action_flows,
            "episode_observation_flows": episode_observation_flows,
        },
    )


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
