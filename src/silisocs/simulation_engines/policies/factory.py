"""Factories for YAML-selectable engine policies."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from typing import Any

from silisocs.simulation_engines.policies.action_chunk import (
    FixedCountActionChunkPolicy,
    OpenEndedActionChunkPolicy,
    SingleActionChunkPolicy,
)
from silisocs.simulation_engines.policies.probe_schedule import (
    DisabledProbeSchedulePolicy,
    FixedIntervalProbeSchedulePolicy,
    StepProbeSchedulePolicy,
)

_ACTION_BUILT_INS = {
    "single_action": SingleActionChunkPolicy,
    "fixed_count": FixedCountActionChunkPolicy,
    "open_ended": OpenEndedActionChunkPolicy,
}

_PROBE_BUILT_INS = {
    "step_schedule": StepProbeSchedulePolicy,
    "fixed_interval": FixedIntervalProbeSchedulePolicy,
    "disabled": DisabledProbeSchedulePolicy,
}


def _load_class(class_path: str) -> type[Any]:
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _instantiate_with_supported_kwargs(cls: type[Any], kwargs: Mapping[str, Any]) -> Any:
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


def _build_policy(
    slot_cfg: Mapping[str, Any] | None,
    *,
    built_ins: Mapping[str, type[Any]],
    default_built_in: str,
) -> Any:
    cfg = dict(slot_cfg or {})
    class_path = cfg.get("class_path")
    params = dict(cfg.get("params") or {})

    if class_path:
        return _instantiate_with_supported_kwargs(_load_class(str(class_path)), params)

    built_in = str(cfg.get("built_in") or default_built_in)
    if built_in not in built_ins:
        options = ", ".join(sorted(built_ins))
        raise ValueError(f"Unknown built_in '{built_in}'. Available: {options}")
    return _instantiate_with_supported_kwargs(built_ins[built_in], params)


def build_action_loop_policy(slot_cfg: Mapping[str, Any] | None = None) -> Any:
    """Build action-loop policy from YAML config."""
    return _build_policy(slot_cfg, built_ins=_ACTION_BUILT_INS, default_built_in="single_action")


def build_probe_schedule_policy(slot_cfg: Mapping[str, Any] | None = None) -> Any:
    """Build probe schedule policy from YAML config."""
    return _build_policy(slot_cfg, built_ins=_PROBE_BUILT_INS, default_built_in="step_schedule")
