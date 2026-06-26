from __future__ import annotations

from silisocs.simulation_engines.base_engines import (
    BaseRuntimeEngine,
    FlowRuntimeEngine,
    MultiGMRuntimeEngine,
)
from silisocs.simulation_engines.policies.steps import FlowStepStrategy, MultiGMStepStrategy

# Per-flow turn policies are resolved in the step strategy and attached to each
# StepBatch (consumed by the engine as ``batch.turn_policy or self.turn_policy``).
# The engine itself must NOT own per-flow policy resolution: a prior attempt that
# added engine-level ``_build_flow_turn_policies`` / ``_turn_policy_for_group``
# methods was removed. These guards keep that responsibility on the strategy.


def test_base_engine_has_no_per_flow_policy_methods() -> None:
    engine = BaseRuntimeEngine()

    assert not hasattr(engine, "_build_flow_turn_policies")
    assert not hasattr(engine, "_turn_policy_for_group")
    # Base/sequential strategies have no flow concept; the engine keeps one global
    # turn policy and applies it uniformly.
    assert engine.turn_policy is not None


def test_flow_engine_keeps_per_flow_resolution_in_the_strategy() -> None:
    engine = FlowRuntimeEngine()

    assert not hasattr(engine, "_build_flow_turn_policies")
    assert not hasattr(engine, "_turn_policy_for_group")
    # The per-flow override map lives on the step strategy, not the engine.
    assert isinstance(engine.step_strategy, FlowStepStrategy)
    assert engine.step_strategy.flow_turn_policies == {}


def test_multi_gm_engine_keeps_per_flow_resolution_in_the_strategy() -> None:
    engine = MultiGMRuntimeEngine()

    assert not hasattr(engine, "_build_flow_turn_policies")
    assert not hasattr(engine, "_turn_policy_for_group")
    assert isinstance(engine.step_strategy, MultiGMStepStrategy)
    assert engine.step_strategy.flow_turn_policies == {}
