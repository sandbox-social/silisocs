from __future__ import annotations

from silisocs.simulation_engines.base_engines import (
    BaseRuntimeEngine,
    FlowRuntimeEngine,
)
from silisocs.simulation_engines.policies.action_chunk import (
    FixedCountActionChunkPolicy,
    SingleActionChunkPolicy,
)


def test_base_engine_ignores_flow_policies() -> None:
    engine = BaseRuntimeEngine()
    default_policy = SingleActionChunkPolicy()

    policies = engine._build_flow_action_loop_policies(
        engine_cfg={
            "flow_policies": {
                "fixed_pre": {
                    "built_in": "fixed_count",
                    "params": {"count": 2},
                }
            }
        },
        default_policy=default_policy,
    )

    assert policies == {}


def test_flow_engine_builds_per_flow_policies() -> None:
    engine = FlowRuntimeEngine()
    default_policy = SingleActionChunkPolicy()

    policies = engine._build_flow_action_loop_policies(
        engine_cfg={
            "flow_policies": {
                "fixed_pre": {
                    "built_in": "fixed_count",
                    "params": {"count": 2},
                },
                "default": {
                    "built_in": "single_action",
                },
            }
        },
        default_policy=default_policy,
    )

    assert isinstance(policies["fixed_pre"], FixedCountActionChunkPolicy)
    assert policies["fixed_pre"].count == 2
    assert isinstance(policies["default"], SingleActionChunkPolicy)


def test_flow_engine_selects_policy_for_group_name() -> None:
    engine = FlowRuntimeEngine()
    default_policy = SingleActionChunkPolicy()
    flow_policy = FixedCountActionChunkPolicy(count=3)

    selected = engine._action_loop_policy_for_group(
        group_name="social_gm:fixed_pre",
        default_policy=default_policy,
        flow_policies={"fixed_pre": flow_policy},
    )
    assert selected is flow_policy

    fallback = engine._action_loop_policy_for_group(
        group_name="social_gm:unconfigured",
        default_policy=default_policy,
        flow_policies={"fixed_pre": flow_policy},
    )
    assert fallback is default_policy
