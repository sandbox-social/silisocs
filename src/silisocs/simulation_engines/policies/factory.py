"""Factories for YAML-selectable engine policies."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from typing import Any

from omegaconf import DictConfig, OmegaConf

from silisocs.simulation_engines.policies.participation import (
    ActivityMarkovParticipation,
    ActivityProbabilityParticipation,
    AllParticipation,
)
from silisocs.simulation_engines.policies.probe_schedule import (
    DisabledProbeSchedulePolicy,
    FixedIntervalProbeSchedulePolicy,
    StepProbeSchedulePolicy,
)
from silisocs.simulation_engines.policies.routers import RandomChoiceRouter
from silisocs.simulation_engines.policies.turns import (
    FixedCountTurnPolicy,
    OpenEndedTurnPolicy,
    SingleActionTurnPolicy,
)

_TURN_BUILT_INS = {
    "single_action": SingleActionTurnPolicy,
    "fixed_count": FixedCountTurnPolicy,
    "open_ended": OpenEndedTurnPolicy,
}

_ROUTER_BUILT_INS = {
    "random": RandomChoiceRouter,
}

_PARTICIPATION_BUILT_INS = {
    "all": AllParticipation,
    "activity_probability": ActivityProbabilityParticipation,
    "activity_markov": ActivityMarkovParticipation,
}

_PROBE_BUILT_INS = {
    "step_schedule": StepProbeSchedulePolicy,
    "fixed_interval": FixedIntervalProbeSchedulePolicy,
    "disabled": DisabledProbeSchedulePolicy,
}


def _load_class(class_path: str) -> type[Any]:
    """Load and return a class from its fully-qualified path."""
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


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
    unsupported = sorted(set(kwargs) - supported)
    if unsupported:
        raise ValueError(
            f"Unsupported config param(s) for {cls.__module__}.{cls.__name__}: "
            f"{unsupported}. Supported params: {sorted(supported)}"
        )
    filtered = {k: v for k, v in kwargs.items() if k in supported}
    return cls(**filtered)


def _build_policy(
    slot_cfg: Mapping[str, Any] | None,
    *,
    built_ins: Mapping[str, type[Any]],
    default_built_in: str,
    runtime_kwargs: Mapping[str, Any] | None = None,
) -> Any:
    """Build a policy from a slot config, using class_path or a built-in name.

    ``runtime_kwargs`` are construction-time injections (e.g. ``sim_roles``): each
    is passed only when the resolved class's constructor accepts it and the slot's
    explicit ``params`` don't already set it, so custom ``class_path`` policies
    never have to declare parameters they don't use.
    """
    cfg = dict(slot_cfg or {})
    class_path = cfg.get("class_path")
    params = dict(cfg.get("params") or {})

    if class_path:
        cls = _load_class(str(class_path))
    else:
        built_in = str(cfg.get("built_in") or default_built_in)
        if built_in not in built_ins:
            options = ", ".join(sorted(built_ins))
            raise ValueError(f"Unknown built_in '{built_in}'. Available: {options}")
        cls = built_ins[built_in]

    if runtime_kwargs:
        # Inject only into explicitly named constructor params: a policy opts in
        # to an injection by declaring it (a bare **kwargs or no __init__ at all
        # must not receive surprise keywords).
        signature = inspect.signature(cls.__init__).parameters
        for key, value in runtime_kwargs.items():
            if key not in params and key in signature:
                params[key] = value
    return _instantiate_with_supported_kwargs(cls, params)


def build_turn_policy(slot_cfg: Mapping[str, Any] | None = None) -> Any:
    """Build turn policy from YAML config."""
    return _build_policy(slot_cfg, built_ins=_TURN_BUILT_INS, default_built_in="single_action")


def build_router(slot_cfg: Mapping[str, Any] | None = None) -> Any:
    """Build a branch :class:`Router` from a slot config (``{built_in|class_path, params}``).

    A ``class_path`` router is the extension seam for "any function chooses": point it
    at a custom :class:`~silisocs.simulation_engines.policies.routers.Router` subclass.
    """
    return _build_policy(slot_cfg, built_ins=_ROUTER_BUILT_INS, default_built_in="random")


def build_participation_policy(
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    sim_roles: Mapping[str, str] | None = None,
) -> Any:
    """Build the sim-level :class:`ParticipationPolicy` from ``sim.engine.participation``.

    ``sim_roles`` (agent name -> sim role) is injected only when the resolved
    policy's constructor accepts it, so a custom ``class_path`` policy without
    role logic needs no extra parameters. ``null`` params are dropped before
    instantiation: switching ``built_in`` merges over the base slot's params
    (Hydra never clears sibling keys), and an explicitly-nulled leftover such as
    ``active_probability: null`` must read as "unset" for every policy.
    """
    cfg = dict(slot_cfg or {})
    cfg["params"] = {k: v for k, v in dict(cfg.get("params") or {}).items() if v is not None}
    return _build_policy(
        cfg,
        built_ins=_PARTICIPATION_BUILT_INS,
        default_built_in="all",
        runtime_kwargs={"sim_roles": dict(sim_roles or {})},
    )


def build_flow_turn_policies(raw: Any) -> dict[str, Any]:
    """Resolve a ``{flow_tag: turn_policy_slot}`` mapping into per-flow policies.

    Each value mirrors the ``sim.engine.turn_policy`` slot shape
    (``{built_in|class_path, params}``). Flows absent from the returned map fall
    back to the engine's single global turn policy at execution time, so an empty
    or omitted config is a pure no-op.
    """
    if raw is None:
        return {}
    if isinstance(raw, DictConfig):
        raw = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(raw, Mapping):
        raise ValueError("sim.engine.step.params.flow_turn_policies must be a mapping.")
    resolved: dict[str, Any] = {}
    for flow, slot in raw.items():
        flow_name = str(flow).strip()
        if not flow_name:
            raise ValueError("flow_turn_policies contains an empty flow name.")
        if slot is not None and not isinstance(slot, Mapping):
            raise ValueError(
                f"flow_turn_policies['{flow_name}'] must be a mapping of "
                "{built_in|class_path, params}."
            )
        resolved[flow_name] = build_turn_policy(dict(slot or {}))
    return resolved


def build_gm_concurrency_caps(raw: Any) -> dict[str, int]:
    """Resolve a ``{gm_name: int}`` mapping into per-GM concurrency caps.

    Each value caps how many of that GM's agent turns run concurrently. An empty or
    omitted config is a pure no-op (the global worker limit governs everything).
    """
    if raw is None:
        return {}
    if isinstance(raw, DictConfig):
        raw = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(raw, Mapping):
        raise ValueError("sim.engine.step.params.gm_concurrency_caps must be a mapping.")
    resolved: dict[str, int] = {}
    for gm, cap in raw.items():
        gm_name = str(gm).strip()
        if not gm_name:
            raise ValueError("gm_concurrency_caps contains an empty GM name.")
        try:
            value = int(cap)
        except (TypeError, ValueError):
            raise ValueError(f"gm_concurrency_caps[{gm_name}] must be an integer >= 1") from None
        if value < 1:
            raise ValueError(f"gm_concurrency_caps[{gm_name}] must be an integer >= 1")
        resolved[gm_name] = value
    return resolved


def build_probe_schedule_policy(slot_cfg: Mapping[str, Any] | None = None) -> Any:
    """Build probe schedule policy from YAML config."""
    return _build_policy(slot_cfg, built_ins=_PROBE_BUILT_INS, default_built_in="step_schedule")
