from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from omegaconf import OmegaConf

from silisocs.agents.base_agent import Agent
from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType, ToolCall
from silisocs.simulation_engines.base_engines import BaseRuntimeEngine, RuntimeEngine
from silisocs.simulation_engines.policies.factory import (
    build_probe_schedule_policy,
    build_turn_policy,
)
from silisocs.simulation_engines.policies.probe_schedule import (
    DisabledProbeSchedulePolicy,
    FixedIntervalProbeSchedulePolicy,
    StepProbeSchedulePolicy,
)
from silisocs.simulation_engines.policies.steps import SequentialStepStrategy
from silisocs.simulation_engines.policies.turns import (
    FixedCountTurnPolicy,
    OpenEndedTurnPolicy,
    SingleActionTurnPolicy,
)
from silisocs.simulation_engines.runtime_base import AgentStepResult


class _FakeEngine:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._responses = list(responses or [])

    def run_agent_step(self, **kwargs):
        self.calls.append(kwargs)
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, AgentStepResult):
                return response
            if isinstance(response, dict):
                raw = response.get("raw", ActionOutput.from_text(str(response.get("rendered", ""))))
                raw_output = (
                    raw if isinstance(raw, ActionOutput) else ActionOutput.from_text(str(raw))
                )
                return AgentStepResult(
                    agent_name="agent",
                    rendered_action=str(response.get("rendered", "")),
                    raw_action=raw_output,
                    resolved_result=str(response.get("resolved", "")),
                )
            return AgentStepResult(
                agent_name="agent",
                rendered_action=str(response),
                raw_action=ActionOutput.from_text(str(response)),
                resolved_result="",
            )
        return AgentStepResult(
            agent_name="agent",
            rendered_action="rendered",
            raw_action=ActionOutput.from_text("rendered"),
            resolved_result="",
        )


class _AgentForEngine:
    def __init__(self, name: str) -> None:
        self.name = name

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        del action_spec
        return ActionOutput.from_text("")


def test_build_turn_policy_defaults_to_single_action() -> None:
    policy = build_turn_policy(None)
    assert isinstance(policy, SingleActionTurnPolicy)


def test_packaged_base_turn_policy_policy_builds() -> None:
    sim_cfg_path = Path("src/silisocs/conf/sim/base.yaml")
    evals_cfg_path = Path("src/silisocs/conf/evals/base.yaml")
    sim_cfg = OmegaConf.load(sim_cfg_path)
    evals_cfg = OmegaConf.load(evals_cfg_path)
    turn_policy = cast(dict[str, Any], OmegaConf.to_container(sim_cfg.engine.turn_policy))
    probe_schedule = cast(dict[str, Any], OmegaConf.to_container(evals_cfg.probes.schedule))

    action_policy = build_turn_policy(turn_policy)
    probe_policy = build_probe_schedule_policy(probe_schedule)

    assert isinstance(action_policy, SingleActionTurnPolicy)
    assert isinstance(probe_policy, StepProbeSchedulePolicy)


def test_build_turn_policy_supports_fixed_count_params() -> None:
    policy = build_turn_policy(
        {"built_in": "fixed_count", "params": {"count": 4, "observe_before_act": "always"}}
    )
    assert isinstance(policy, FixedCountTurnPolicy)
    assert policy.count == 4
    assert policy.observe_before_act == "always"


def test_build_turn_policy_supports_class_path() -> None:
    policy = build_turn_policy(
        {
            "class_path": ("silisocs.simulation_engines.policies.turns.FixedCountTurnPolicy"),
            "params": {"count": 3},
        }
    )
    assert isinstance(policy, FixedCountTurnPolicy)
    assert policy.count == 3


def test_build_turn_policy_rejects_unknown_params() -> None:
    with pytest.raises(ValueError, match="Unsupported config param"):
        build_turn_policy(
            {
                "class_path": ("silisocs.simulation_engines.policies.turns.FixedCountTurnPolicy"),
                "params": {"count": 3, "typo_count": 9},
            }
        )


def test_build_probe_schedule_policy_defaults_and_fixed_interval() -> None:
    default_policy = build_probe_schedule_policy(None)
    assert isinstance(default_policy, StepProbeSchedulePolicy)

    fixed = build_probe_schedule_policy(
        {
            "built_in": "fixed_interval",
            "params": {"start_step": 2, "every_n_steps": 3},
        }
    )
    assert isinstance(fixed, FixedIntervalProbeSchedulePolicy)
    assert fixed.should_run_probe_phase(step=1, orchestrator=None) is False
    assert fixed.should_run_probe_phase(step=2, orchestrator=None) is True
    assert fixed.should_run_probe_phase(step=5, orchestrator=None) is True


def test_build_probe_schedule_policy_supports_disabled() -> None:
    policy = build_probe_schedule_policy({"built_in": "disabled"})
    assert isinstance(policy, DisabledProbeSchedulePolicy)
    assert policy.should_run_probe_phase(step=99, orchestrator=None) is False


def test_engine_single_action_uses_direct_gm_methods_without_name_prefix() -> None:
    class _Agent:
        name = "Alice"

        def __init__(self) -> None:
            self.observations: list[str] = []

        def observe(self, observation: str) -> None:
            self.observations.append(observation)

        def act(self, action_spec: ActionSpec) -> ActionOutput:
            assert action_spec.prompt == "Do it"
            return ActionOutput.from_text('raw {"id": 1}')

    class _GameMaster:
        name = "gm"

        def __init__(self) -> None:
            self.resolved_with: tuple[str, ActionOutput] | None = None

        def make_observation(self, agent_name: str) -> str:
            return f"obs:{agent_name}"

        def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
            self.resolved_with = (agent_name, action)
            return f"resolved:{agent_name}"

    agent = _Agent()
    gm = _GameMaster()
    engine = BaseRuntimeEngine()

    result = engine.run_agent_step(
        game_master=cast(Agent, gm),
        agent=cast(Agent, agent),
        action_spec=ActionSpec("Do it", OutputType.TEXT),
        verbose=False,
    )

    assert result.rendered_action == 'raw {"id": 1}'
    assert gm.resolved_with is not None
    assert gm.resolved_with[0] == "Alice"
    assert gm.resolved_with[1].text == 'raw {"id": 1}'
    assert agent.observations == ["obs:Alice", "resolved:Alice"]


def test_action_policies_count_typed_tool_calls_toward_budget() -> None:
    engine = _FakeEngine(
        [
            {
                "raw": ActionOutput.from_tool_calls(
                    [
                        ToolCall("post", {"content": "a"}),
                        ToolCall("like", {"post_id": "1"}),
                    ]
                ),
                "rendered": "agent: multi-1",
                "resolved": "posted\nliked",
            },
            "SHOULD_NOT_RUN",
        ]
    )

    policy = FixedCountTurnPolicy(count=2)
    result = policy.run(
        engine=engine,
        game_master=object(),
        agent=object(),
        action_spec=object(),
        verbose=False,
    )

    assert result == "agent: multi-1"
    assert len(engine.calls) == 1


def test_turn_policy_observe_before_act_modes() -> None:
    for mode, expected in {
        "first": [True, False, False],
        "always": [True, True, True],
        "never": [False, False, False],
    }.items():
        engine = _FakeEngine(["a1", "a2", "a3"])
        policy = FixedCountTurnPolicy(count=3, observe_before_act=mode)

        policy.run(
            engine=engine,
            game_master=object(),
            agent=object(),
            action_spec=object(),
            verbose=False,
        )

        assert [call["observe_before_action"] for call in engine.calls] == expected


def test_turn_policy_rejects_unknown_observe_before_act_mode() -> None:
    engine = _FakeEngine(["a1"])
    policy = SingleActionTurnPolicy(observe_before_act="sometimes")

    with pytest.raises(ValueError, match="observe_before_act"):
        policy.run(
            engine=engine,
            game_master=object(),
            agent=object(),
            action_spec=object(),
            verbose=False,
        )


def test_engine_runs_three_initialization_phases_once_before_loop(tmp_path) -> None:
    cfg = OmegaConf.create(
        {
            "output_rootname": str(tmp_path),
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": "base", "params": {}},
                }
            },
            "evals": {"probes": {}},
        }
    )

    events: list[str] = []

    class _AgentInitializer:
        def initialize(self, **kwargs: Any) -> None:
            events.append("agents")
            assert [agent.name for agent in kwargs["agents"]] == ["Alice"]

    class _GameMasterInitializer:
        def initialize(self, **kwargs: Any) -> None:
            events.append("game_masters")
            assert [agent.name for agent in kwargs["agents"]] == ["Alice"]
            assert [gm.name for gm in kwargs["game_masters"]] == ["gm"]

    class _SimulationInitializer:
        def initialize(self, **kwargs: Any) -> None:
            events.append("simulation")
            assert [agent.name for agent in kwargs["agents"]] == ["Alice"]
            assert [gm.name for gm in kwargs["game_masters"]] == ["gm"]

    engine = BaseRuntimeEngine(config=cfg)
    game_masters = [cast(Agent, type("_GM", (), {"name": "gm"})())]
    agents = [cast(Agent, _AgentForEngine("Alice"))]
    engine.initialize(
        agents=agents,
        game_masters=game_masters,
        agent_initializer=_AgentInitializer(),
        game_master_initializer=_GameMasterInitializer(),
        simulation_initializer=_SimulationInitializer(),
        initialization_context=object(),
        initializer_model=object(),
    )
    engine.run_loop(
        game_masters=game_masters,
        agents=agents,
        max_steps=0,
        start_step=0,
        verbose=False,
        checkpoint_callback=None,
    )

    assert events == ["agents", "game_masters", "simulation"]


def test_engine_calls_gm_update_before_actor_selection() -> None:
    events: list[str] = []

    class _Agent:
        name = "Alice"

        def observe(self, observation: str) -> None:
            events.append(f"observe:{observation}")

        def act(self, action_spec: ActionSpec) -> ActionOutput:
            events.append(f"act:{action_spec.prompt}")
            return ActionOutput.from_text("hello")

    class _GameMaster:
        name = "gm"

        def update(self, *, step: int, agents: list[Any], context: Any | None = None) -> None:
            del agents, context
            events.append(f"update:{step}")

        def acting_agents(self, candidate_agents: list[Any]) -> list[str]:
            events.append("acting_agents")
            return [candidate_agents[0].name]

        def action_prompt(self, agent_name: str) -> ActionSpec:
            events.append(f"action_prompt:{agent_name}")
            return ActionSpec("Do it", OutputType.TEXT)

        def make_observation(self, agent_name: str) -> str:
            return f"obs:{agent_name}"

        def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
            return f"resolved:{agent_name}:{action.text}"

    engine = BaseRuntimeEngine()
    result = engine.run_step(
        step_index=7,
        game_masters=[_GameMaster()],
        agents=[_Agent()],
        verbose=False,
    )

    assert result.active_agent_names == ("Alice",)
    assert events[:3] == ["update:7", "acting_agents", "action_prompt:Alice"]


def test_build_engine_supports_sequential_step_strategy() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "step": {"built_in": "sequential", "params": {}},
                    "turn_policy": {"built_in": "single_action", "params": {}},
                }
            }
        }
    )

    engine = build_engine(cfg)

    assert isinstance(engine, RuntimeEngine)
    assert isinstance(engine.step_strategy, SequentialStepStrategy)


def test_sequential_step_strategy_runs_one_agent_batches_in_order() -> None:
    events: list[str] = []

    class _Agent:
        def __init__(self, name: str) -> None:
            self.name = name

        def observe(self, observation: str) -> None:
            events.append(f"observe:{self.name}:{observation}")

        def act(self, action_spec: ActionSpec) -> ActionOutput:
            events.append(f"act:{self.name}:{action_spec.prompt}")
            return ActionOutput.from_text(self.name)

    class _GameMaster:
        name = "gm"

        def update(self, *, step: int, agents: list[Any], context: Any | None = None) -> None:
            del step, agents, context

        def acting_agents(self, candidate_agents: list[Any]) -> list[str]:
            return [agent.name for agent in candidate_agents]

        def action_prompt(self, agent_name: str) -> ActionSpec:
            return ActionSpec(f"prompt:{agent_name}", OutputType.TEXT)

        def make_observation(self, agent_name: str) -> str:
            return f"obs:{agent_name}"

        def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
            events.append(f"resolve:{agent_name}:{action.text}")
            return f"resolved:{agent_name}"

    engine = RuntimeEngine(
        step_strategy=SequentialStepStrategy(),
        turn_policy=SingleActionTurnPolicy(),
    )
    result = engine.run_step(
        step_index=0,
        game_masters=[_GameMaster()],
        agents=[_Agent("Alice"), _Agent("Bob")],
        verbose=False,
    )

    assert result.active_agent_names == ("Alice", "Bob")
    assert events == [
        "observe:Alice:obs:Alice",
        "act:Alice:prompt:Alice",
        "resolve:Alice:Alice",
        "observe:Alice:resolved:Alice",
        "observe:Bob:obs:Bob",
        "act:Bob:prompt:Bob",
        "resolve:Bob:Bob",
        "observe:Bob:resolved:Bob",
    ]


def test_fixed_count_policy_runs_exact_number_of_actions() -> None:
    engine = _FakeEngine(["a1", "a2", "a3"])
    policy = FixedCountTurnPolicy(count=3)
    result = policy.run(
        engine=engine,
        game_master=object(),
        agent=object(),
        action_spec=object(),
        verbose=False,
    )

    assert result == "a3"
    assert len(engine.calls) == 3


def test_open_ended_policy_stops_on_finished_action() -> None:
    """Test that open-ended policy stops when agent outputs 'Finished action episode'."""
    engine = _FakeEngine(
        [
            {
                "raw": "ACTION TYPE: POST\nTARGET ID: \nCONTENT: first\nREASONING: test",
                "rendered": "agent: first",
                "resolved": "Alice posted",
            },
            {
                "raw": "ACTION TYPE: FINISHED\nTARGET ID: \nCONTENT: \nREASONING: done",
                "rendered": "agent: FINISHED",
                "resolved": "Finished action episode",
            },
            "SHOULD_NOT_RUN",
        ]
    )
    policy = OpenEndedTurnPolicy(max_actions=5, finished_action_signal="FINISHED")
    result = policy.run(
        engine=engine,
        game_master=object(),
        agent=object(),
        action_spec=object(),
        verbose=False,
    )

    # Should have executed 2 actions (POST, then explicit FINISHED signal stops the loop).
    assert result == "agent: first"
    assert len(engine.calls) == 2


def test_open_ended_policy_does_not_stop_on_finish_word_in_content() -> None:
    engine = _FakeEngine(
        [
            {
                "raw": (
                    "ACTION TYPE: POST\n"
                    "TARGET ID: \n"
                    "CONTENT: We should finish this policy debate tomorrow.\n"
                    "REASONING: includes finish word but not finish action"
                ),
                "rendered": "agent: post-1",
                "resolved": "Alice posted",
            },
            {
                "raw": "ACTION TYPE: POST\nTARGET ID: \nCONTENT: second post\nREASONING: keep going",
                "rendered": "agent: post-2",
                "resolved": "Alice posted",
            },
            {
                "raw": "ACTION TYPE: FINISH\nTARGET ID: \nCONTENT: \nREASONING: done",
                "rendered": "agent: finish",
                "resolved": "Finished action episode",
            },
        ]
    )
    policy = OpenEndedTurnPolicy(max_actions=5, finished_action_signal="FINISHED")
    result = policy.run(
        engine=engine,
        game_master=object(),
        agent=object(),
        action_spec=object(),
        verbose=False,
    )

    assert result == "agent: post-2"
    assert len(engine.calls) == 3


def test_open_ended_policy_counts_multi_tool_calls_toward_cap() -> None:
    engine = _FakeEngine(
        [
            {
                "raw": ActionOutput.from_tool_calls(
                    [
                        ToolCall("post", {"content": "a"}),
                        ToolCall("like", {"post_id": "1"}),
                    ]
                ),
                "rendered": "agent: multi-1",
                "resolved": "posted\nliked",
            },
            {
                "raw": ActionOutput.from_tool_calls([ToolCall("post", {"content": "b"})]),
                "rendered": "agent: single-2",
                "resolved": "posted",
            },
            "SHOULD_NOT_RUN",
        ]
    )

    policy = OpenEndedTurnPolicy(max_actions=3, finished_action_signal="FINISHED")
    result = policy.run(
        engine=engine,
        game_master=object(),
        agent=object(),
        action_spec=object(),
        verbose=False,
    )

    assert result == "agent: single-2"
    assert len(engine.calls) == 2


def test_fixed_count_policy_counts_multi_tool_calls_toward_budget() -> None:
    engine = _FakeEngine(
        [
            {
                "raw": ActionOutput.from_tool_calls(
                    [
                        ToolCall("post", {"content": "a"}),
                        ToolCall("like", {"post_id": "1"}),
                    ]
                ),
                "rendered": "agent: multi-1",
                "resolved": "posted\nliked",
            },
            "SHOULD_NOT_RUN",
        ]
    )

    policy = FixedCountTurnPolicy(count=2)
    result = policy.run(
        engine=engine,
        game_master=object(),
        agent=object(),
        action_spec=object(),
        verbose=False,
    )

    assert result == "agent: multi-1"
    assert len(engine.calls) == 1
