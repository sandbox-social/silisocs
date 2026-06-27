"""Factory helpers for runtime components."""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Mapping
from typing import Any, cast

from omegaconf import DictConfig, OmegaConf

_LOGGER = logging.getLogger(__name__)

from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.factory import (
    build_flow_turn_policies,
    build_gm_concurrency_caps,
    build_turn_policy,
)
from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy
from silisocs.simulation_engines.policies.steps import (
    FlowStepStrategy,
    MultiGMSerialStepStrategy,
    MultiGMStagedStepStrategy,
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


# Multi-GM step built-ins mapped to their flow-chain traversal strategy. The
# default ``multi_gm`` is the concurrent (independent-pipeline) traversal;
# ``multi_gm_serial`` is the legacy row-major one and ``multi_gm_staged`` runs a
# global per-stage barrier (column-major, supports empty/idle slots).
_MULTI_GM_STRATEGIES: dict[str, type[Any]] = {
    "multi_gm": MultiGMStepStrategy,
    "multi_gm_serial": MultiGMSerialStepStrategy,
    "multi_gm_staged": MultiGMStagedStepStrategy,
}


def _build_step_strategy(
    step_cfg: Mapping[str, Any], flow_chains: Mapping[str, Any] | None = None
) -> Any:
    class_path = str(step_cfg.get("class_path") or "").strip()
    params = dict(step_cfg.get("params") or {})
    chains = dict(flow_chains or {})
    if class_path:
        strategy = _instantiate_with_supported_kwargs(_load_class(class_path), params)
        # A custom step strategy may be a multi-GM router (e.g. a MultiGMStepStrategy
        # subclass). Such strategies previously read the chains off the default game
        # master; now they are supplied here. Set the field post-construction (only
        # when the strategy declares it and the user did not pass chains explicitly),
        # so a strategy that does not use chains is never rejected for an extra kwarg.
        if "flow_chains" not in params and hasattr(strategy, "flow_chains"):
            strategy.flow_chains = chains
        return strategy
    _reject_retired_chain_execution(params)
    built_in = str(step_cfg.get("built_in") or "base").strip()
    flow_order = tuple(str(item) for item in (params.get("flow_order") or ["fixed_pre", "default"]))
    flow_turn_policies = build_flow_turn_policies(params.get("flow_turn_policies"))
    if built_in == "base":
        return None
    if built_in == "sequential":
        return SequentialStepStrategy()
    if built_in == "flow":
        return FlowStepStrategy(
            flow_order=flow_order,
            flow_turn_policies=flow_turn_policies,
        )
    strategy_cls = _MULTI_GM_STRATEGIES.get(built_in)
    if strategy_cls is not None:
        if not chains:
            _LOGGER.warning(
                "sim.engine.step.built_in=%r is a multi-GM routing strategy but no flow "
                "chains were resolved (env.gm_orchestration.flow_bindings.flow_to_gms is "
                "empty/unset); every flow will route to the default game master only.",
                built_in,
            )
        return strategy_cls(
            flow_order=flow_order,
            flow_turn_policies=flow_turn_policies,
            flow_chains=chains,
        )
    raise ValueError(f"Unknown sim.engine.step.built_in='{built_in}'.")


def _reject_retired_chain_execution(params: Mapping[str, Any]) -> None:
    """Fail loudly if a config still sets the removed ``chain_execution`` knob.

    ``chain_execution`` was replaced by dedicated step strategies; a stale config
    that still sets it would otherwise be silently ignored and change scheduling.
    """
    if "chain_execution" in params:
        raise ValueError(
            "sim.engine.step.params.chain_execution has been removed. Select the "
            "traversal via sim.engine.step.built_in instead: 'multi_gm' (concurrent, "
            "the default), 'multi_gm_serial' (legacy flow-by-flow), or 'multi_gm_staged' "
            "(global per-stage barrier)."
        )


def _build_turn_policy(engine_cfg: Mapping[str, Any]) -> Any:
    turn_policy_cfg = _slot_to_mapping(
        engine_cfg.get("turn_policy"),
        default={"built_in": "single_action", "class_path": None, "params": {}},
    )
    return build_turn_policy(turn_policy_cfg)


def build_engine(cfg: DictConfig, flow_chains: Mapping[str, Any] | None = None):
    """Build runtime engine from strategy-based config.

    ``flow_chains`` is the resolved flow -> GM-sequence routing topology (engine
    config) handed to a multi-GM step strategy; callers source it via
    ``game_masters.resolve_flow_chains(cfg)``. When omitted it defaults to empty,
    which is correct for base/sequential/flow modes; a ``multi_gm*`` strategy built
    without chains logs a warning and routes every flow to the default game master.
    """
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
                "step_strategy": _build_step_strategy(step_cfg, flow_chains),
                "turn_policy": turn_policy,
                "gm_turn_policies": build_flow_turn_policies(
                    dict(step_cfg.get("params") or {}).get("gm_turn_policies")
                ),
                "gm_concurrency_caps": build_gm_concurrency_caps(
                    dict(step_cfg.get("params") or {}).get("gm_concurrency_caps")
                ),
            },
        )

    step_built_in = str(step_cfg.get("built_in") or "base").strip()
    step_params = dict(step_cfg.get("params") or {})
    if step_built_in in {"base", "sequential"} and step_params.get("flow_turn_policies"):
        _LOGGER.warning(
            "sim.engine.step.params.flow_turn_policies is set but step.built_in=%r does not group "
            "agents by flow; per-flow turn policies are ignored. Use built_in 'flow' or a "
            "'multi_gm' strategy.",
            step_built_in,
        )
    # One engine; the traversal is chosen entirely by the step strategy.
    # ``_build_step_strategy`` resolves base/sequential/flow/multi_gm* (and any custom
    # ``class_path``) and rejects the retired ``chain_execution`` knob; ``base`` yields
    # None, so the engine falls back to its default ``BaseStepStrategy``.
    return RuntimeEngine(
        config=cfg,
        loop_strategy=_build_loop_strategy(loop_cfg),
        step_strategy=_build_step_strategy(step_cfg, flow_chains),
        turn_policy=turn_policy,
        gm_turn_policies=build_flow_turn_policies(step_params.get("gm_turn_policies")),
        gm_concurrency_caps=build_gm_concurrency_caps(step_params.get("gm_concurrency_caps")),
    )
