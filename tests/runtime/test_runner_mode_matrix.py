"""Tests for GM and engine preset matrix behavior."""

from __future__ import annotations

from omegaconf import OmegaConf

from silisocs.runtime.construction.engines import build_engine
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.steps import BaseStepStrategy, FlowStepStrategy


def _cfg(engine_preset: str):
    step_built_in = "base"
    if engine_preset == "flow":
        step_built_in = "flow"
    if engine_preset == "multi_gm":
        step_built_in = "multi_gm"
    return OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "step": {
                        "built_in": step_built_in,
                        "params": {"flow_order": ["fixed_pre", "default"]},
                    },
                    "turn_policy": {"built_in": "single_action"},
                },
            },
            "env": {
                "gm": {"class_path": "silisocs.environments.gm.game_master.ComponentGameMaster"}
            },
        }
    )


def test_base_gm_with_base_engine() -> None:
    """Base GM preset should pair with base engine preset."""
    cfg = _cfg("base")

    engine = build_engine(cfg)

    assert isinstance(engine, RuntimeEngine)
    assert isinstance(engine.step_strategy, BaseStepStrategy)


def test_base_gm_with_flow_engine() -> None:
    """Base GM preset should still work with flow engine preset."""
    cfg = _cfg("flow")

    engine = build_engine(cfg)

    assert isinstance(engine, RuntimeEngine)
    assert isinstance(engine.step_strategy, FlowStepStrategy)


def test_shared_flow_gm_with_base_engine() -> None:
    """Flow-routed GM class is selected directly in env.gm.class_path."""
    cfg = _cfg("base")
    cfg.env.gm.class_path = "silisocs.environments.gm.game_master.MultiFlowGameMaster"

    engine = build_engine(cfg)

    assert isinstance(engine, RuntimeEngine)
    assert isinstance(engine.step_strategy, BaseStepStrategy)
