from __future__ import annotations

from silisocs.simulation_engines.base_engines import (
    BaseRuntimeEngine,
    FlowRuntimeEngine,
)


def test_base_engine_ignores_flow_policies() -> None:
    engine = BaseRuntimeEngine()

    assert not hasattr(engine, "_build_flow_turn_policies")
    assert not hasattr(engine, "_turn_policy_for_group")


def test_flow_engine_uses_only_global_turn_policy() -> None:
    engine = FlowRuntimeEngine()

    assert not hasattr(engine, "_build_flow_turn_policies")
    assert not hasattr(engine, "_turn_policy_for_group")
