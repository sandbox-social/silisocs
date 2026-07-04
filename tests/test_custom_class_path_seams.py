"""Custom ``class_path`` / registration extension seams, positively exercised.

These cover the "user extends without editing core" contract for two seams the
audit found registered-but-untested on the custom path:

* GM components (``env.gm.components.{role}.class_path``): a custom class that fills
  a slot must subclass that role's ABC, and a wrong class is rejected loudly at build
  time (not as a late AttributeError mid-run).
* Replay mappers (``register_replay_mapper``): a real logged event is driven through a
  *user-registered* mapper for a custom ``backend_type`` end-to-end.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from omegaconf import OmegaConf

from silisocs.environments.gm.components.base import NextActingComponent, ResolveComponent
from silisocs.environments.gm.components.factory import _build_from_slot
from silisocs.runtime.checkpointing import register_replay_mapper
from silisocs.runtime.checkpointing.restore import _map_event_for_gm
from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.types import ActionOutput, ToolCall
from silisocs.simulation_engines.base_engines import RuntimeEngine


class _CustomNextActing(NextActingComponent):
    """A minimal custom next-acting component reachable by class_path."""

    def __init__(self, only: str = "solo") -> None:
        super().__init__()
        self._only = only

    def acting_agent_names(self) -> list[str]:
        return [self._only]


def test_custom_component_correct_base_builds() -> None:
    component = _build_from_slot(
        {"class_path": f"{__name__}._CustomNextActing", "params": {"only": "alice"}},
        built_ins={},
        default_built_in="all_agents",
        expected_base=NextActingComponent,
    )
    assert isinstance(component, NextActingComponent)
    assert component.acting_agent_names() == ["alice"]


def test_custom_component_wrong_base_rejected_at_build_time() -> None:
    # A ResolveComponent named in the next_acting slot fails loudly with the role,
    # instead of building and dying as an AttributeError when scheduled.
    with pytest.raises(TypeError, match="must subclass NextActingComponent"):
        _build_from_slot(
            {
                "class_path": "silisocs.environments.gm.components.resolve.ParsedActionResolveComponent"
            },
            built_ins={},
            default_built_in="all_agents",
            expected_base=NextActingComponent,
        )


def test_wrong_base_check_names_the_class_path() -> None:
    bad_path = "silisocs.environments.gm.components.next_acting.AllAgentsNextActing"
    with pytest.raises(TypeError, match="must subclass ResolveComponent"):
        _build_from_slot(
            {"class_path": bad_path},
            built_ins={},
            default_built_in="parsed_action",
            expected_base=ResolveComponent,
        )


class _FakeGM:
    """A stand-in game master exposing only the backend_type restore reads."""

    def __init__(self, backend_type: str) -> None:
        self.backend_type = backend_type
        self.name = "custom_gm"


def test_custom_replay_mapper_drives_a_real_event() -> None:
    # A third-party backend registers a mapper for its own backend_type; a logged
    # event is then reconstructed through it with no core edit. (The autouse
    # _isolate_replay_mappers fixture keeps this registration out of other tests.)
    def _my_mapper(label: str, data: Mapping[str, Any]) -> ActionOutput | None:
        if label == "shout":
            return ActionOutput.from_tool_calls(
                [ToolCall("broadcast", {"text": str(data["text"])})]
            )
        return None

    register_replay_mapper("my_custom_backend", _my_mapper)
    gm = _FakeGM("my_custom_backend")

    action = _map_event_for_gm(gm, "shout", {"text": "hello world"})
    assert action is not None
    assert action.tool_calls[0].name == "broadcast"
    assert action.tool_calls[0].arguments == {"text": "hello world"}

    # A label the custom mapper does not recognize maps to None (skip), not a crash.
    assert _map_event_for_gm(gm, "whisper", {"text": "x"}) is None


def test_unregistered_backend_type_has_no_mapper() -> None:
    gm = _FakeGM("never_registered_backend")
    assert _map_event_for_gm(gm, "shout", {"text": "x"}) is None


class _CustomSchedule:
    """A custom probe-schedule policy reachable by class_path (runs on even steps)."""

    name = "even_only"

    def __init__(self, *, stride: int = 2) -> None:
        self._stride = stride

    def should_run_probe_phase(self, *, step: int, orchestrator: object) -> bool:
        del orchestrator
        return step % self._stride == 0


def test_custom_probe_schedule_policy_via_class_path() -> None:
    from silisocs.simulation_engines.policies.factory import build_probe_schedule_policy

    policy = build_probe_schedule_policy(
        {"class_path": f"{__name__}._CustomSchedule", "params": {"stride": 3}}
    )
    assert isinstance(policy, _CustomSchedule)
    assert policy.should_run_probe_phase(step=3, orchestrator=None) is True
    assert policy.should_run_probe_phase(step=4, orchestrator=None) is False


class _MarkerEngine(RuntimeEngine):
    """A custom engine reachable via ``sim.engine.class_path`` (no core edit)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.marker = "custom-engine"


def test_custom_engine_via_engine_class_path() -> None:
    cfg = OmegaConf.create(
        {
            "seed": 3,
            "sim": {
                "engine": {
                    "class_path": f"{__name__}._MarkerEngine",
                    "loop": {"built_in": "fixed_steps", "class_path": None, "params": {}},
                    "step": {"built_in": "base", "class_path": None, "params": {}},
                    "turn_policy": {"built_in": "single_action", "class_path": None, "params": {}},
                    "participation": {"built_in": "all", "class_path": None, "params": {}},
                }
            },
        }
    )
    engine = build_engine(cfg)
    assert isinstance(engine, _MarkerEngine)
    assert engine.marker == "custom-engine"
    assert engine.seed == 3
