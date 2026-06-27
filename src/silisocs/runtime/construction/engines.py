"""Factory helpers for runtime components."""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Mapping
from typing import Any, cast

from omegaconf import DictConfig, OmegaConf

_LOGGER = logging.getLogger(__name__)

from silisocs.simulation_engines.base_engines import (
    BaseRuntimeEngine,
    FlowRuntimeEngine,
    MultiGMRuntimeEngine,
    RuntimeEngine,
)
from silisocs.simulation_engines.policies.factory import (
    build_flow_turn_policies,
    build_turn_policy,
)
from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy
from silisocs.simulation_engines.policies.steps import (
    FlowStepStrategy,
    MultiGMStepStrategy,
    SequentialStepStrategy,
)


def _load_class(class_path: str) -> type[Any]:
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    loaded = getattr(module, class_name)
    if not inspect.isclass(loaded):
        raise TypeError(f"{class_path} is not a class.")
    return cast(type[Any], loaded)


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


def _slot_to_mapping(slot: Any, *, default: Mapping[str, Any]) -> dict[str, Any]:
    if slot is None:
        return dict(default)
    if isinstance(slot, DictConfig):
        slot = OmegaConf.to_container(slot, resolve=True)
    if not isinstance(slot, Mapping):
        raise ValueError("Engine slot config must be a mapping.")
    cfg = dict(slot)
    cfg.setdefault("class_path", None)
    cfg.setdefault("params", {})
    return cfg


def _build_loop_strategy(loop_cfg: Mapping[str, Any]) -> Any:
    class_path = str(loop_cfg.get("class_path") or "").strip()
    params = dict(loop_cfg.get("params") or {})
    if class_path:
        return _instantiate_with_supported_kwargs(_load_class(class_path), params)
    built_in = str(loop_cfg.get("built_in") or "fixed_steps").strip()
    if built_in == "fixed_steps":
        return FixedStepsLoopStrategy()
    raise ValueError(f"Unknown sim.engine.loop.built_in='{built_in}'.")


def _build_step_strategy(step_cfg: Mapping[str, Any]) -> Any:
    class_path = str(step_cfg.get("class_path") or "").strip()
    params = dict(step_cfg.get("params") or {})
    if class_path:
        return _instantiate_with_supported_kwargs(_load_class(class_path), params)
    built_in = str(step_cfg.get("built_in") or "base").strip()
    flow_order = tuple(str(item) for item in (params.get("flow_order") or ["fixed_pre", "default"]))
    flow_turn_policies = build_flow_turn_policies(params.get("flow_turn_policies"))
    chain_execution = str(params.get("chain_execution") or "concurrent").strip().lower()
    if built_in == "base":
        return None
    if built_in == "sequential":
        return SequentialStepStrategy()
    if built_in == "flow":
        return FlowStepStrategy(
            flow_order=flow_order,
            flow_turn_policies=flow_turn_policies,
            chain_execution=chain_execution,
        )
    if built_in == "multi_gm":
        return MultiGMStepStrategy(
            flow_order=flow_order,
            flow_turn_policies=flow_turn_policies,
            chain_execution=chain_execution,
        )
    raise ValueError(f"Unknown sim.engine.step.built_in='{built_in}'.")


def _build_turn_policy(engine_cfg: Mapping[str, Any]) -> Any:
    turn_policy_cfg = _slot_to_mapping(
        engine_cfg.get("turn_policy"),
        default={"built_in": "single_action", "class_path": None, "params": {}},
    )
    return build_turn_policy(turn_policy_cfg)


def build_engine(cfg: DictConfig):
    """Build runtime engine from strategy-based config."""
    raw_engine_cfg = getattr(cfg.sim, "engine", object())
    if isinstance(raw_engine_cfg, DictConfig):
        materialized = OmegaConf.to_container(raw_engine_cfg, resolve=True)
        if not isinstance(materialized, Mapping):
            raise ValueError("`sim.engine` must resolve to a mapping.")
        engine_cfg = dict(cast(Mapping[str, Any], materialized))
    elif isinstance(raw_engine_cfg, Mapping):
        engine_cfg = dict(cast(Mapping[str, Any], raw_engine_cfg))
    else:
        raise ValueError("`sim.engine` must be a mapping.")

    class_path = str(engine_cfg.get("class_path") or "").strip()
    loop_cfg = _slot_to_mapping(
        engine_cfg.get("loop"),
        default={"built_in": "fixed_steps", "class_path": None, "params": {}},
    )
    step_cfg = _slot_to_mapping(
        engine_cfg.get("step"),
        default={"built_in": "base", "class_path": None, "params": {}},
    )
    turn_policy = _build_turn_policy(engine_cfg)

    if class_path:
        cls = _load_class(class_path)
        return _instantiate_with_supported_kwargs(
            cls,
            {
                "config": cfg,
                "loop_strategy": _build_loop_strategy(loop_cfg),
                "step_strategy": _build_step_strategy(step_cfg),
                "turn_policy": turn_policy,
            },
        )

    step_built_in = str(step_cfg.get("built_in") or "base").strip()
    if step_built_in in {"base", "sequential"} and dict(step_cfg.get("params") or {}).get(
        "flow_turn_policies"
    ):
        _LOGGER.warning(
            "sim.engine.step.params.flow_turn_policies is set but step.built_in=%r does not group "
            "agents by flow; per-flow turn policies are ignored. Use built_in 'flow' or 'multi_gm'.",
            step_built_in,
        )
    # A configured sim.engine.loop must be honored regardless of step mode, so the
    # built loop strategy is passed to every preset (presets only setdefault the
    # FixedStepsLoopStrategy, so an explicit one wins).
    loop_strategy = _build_loop_strategy(loop_cfg)
    if step_built_in == "base":
        return BaseRuntimeEngine(config=cfg, loop_strategy=loop_strategy, turn_policy=turn_policy)
    if step_built_in == "sequential":
        return RuntimeEngine(
            config=cfg,
            loop_strategy=loop_strategy,
            step_strategy=SequentialStepStrategy(),
            turn_policy=turn_policy,
        )
    if step_built_in == "flow":
        return FlowRuntimeEngine(config=cfg, loop_strategy=loop_strategy, turn_policy=turn_policy)
    if step_built_in == "multi_gm":
        return MultiGMRuntimeEngine(
            config=cfg, loop_strategy=loop_strategy, turn_policy=turn_policy
        )

    return RuntimeEngine(
        config=cfg,
        loop_strategy=_build_loop_strategy(loop_cfg),
        step_strategy=_build_step_strategy(step_cfg),
        turn_policy=turn_policy,
    )
