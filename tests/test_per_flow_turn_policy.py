"""Tests for per-flow turn policy overrides (StepBatch.turn_policy)."""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.types import ActionOutput, ActionSpec, OutputType
from silisocs.simulation_engines.base_engines import FlowRuntimeEngine
from silisocs.simulation_engines.policies.factory import build_flow_turn_policies
from silisocs.simulation_engines.policies.steps import FlowStepStrategy
from silisocs.simulation_engines.policies.turns import (
    FixedCountTurnPolicy,
    SingleActionTurnPolicy,
)
from silisocs.simulation_engines.runtime_base import StepBatch


class _Agent:
    def __init__(self, name: str, *, scripted: list[str] | None = None) -> None:
        self.name = name
        self.observations: list[str] = []
        self.actions: list[str] = []
        self._scripted = list(scripted or [])

    def observe(self, observation: str) -> None:
        self.observations.append(observation)

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        index = len(self.actions)
        if self._scripted:
            text = self._scripted[min(index, len(self._scripted) - 1)]
        else:
            text = f"{self.name}:{index}"
        self.actions.append(text)
        return ActionOutput.from_text(text)


class _GameMaster:
    def __init__(self, *, name: str, selected: list[str], agent_flow_tags: dict[str, str]) -> None:
        self.name = name
        self.selected = selected
        self.agent_flow_tags = agent_flow_tags
        self.flow_chains: dict[str, list[str]] = {}
        self.resolved: list[tuple[str, ActionOutput]] = []

    def update(self, *, step: int, agents: list[_Agent], context: object | None = None) -> None:
        del step, agents, context

    def acting_agents(self, candidate_agents: list[_Agent]) -> list[str]:
        available = {agent.name for agent in candidate_agents}
        return [name for name in self.selected if name in available]

    def action_prompt(self, agent_name: str) -> ActionSpec:
        return ActionSpec(prompt=f"{self.name}:{agent_name}", output_type=OutputType.TEXT)

    def make_observation(self, agent_name: str) -> str:
        return f"obs:{agent_name}"

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        self.resolved.append((agent_name, action))
        return f"resolved:{agent_name}"


# --------------------------------------------------------------------------- #
# StepBatch carrier
# --------------------------------------------------------------------------- #


def test_step_batch_defaults_to_no_turn_policy() -> None:
    batch = StepBatch(flow_name="default", game_master=object(), turns=[])
    assert batch.turn_policy is None


def test_step_batch_accepts_turn_policy_override() -> None:
    policy = SingleActionTurnPolicy()
    batch = StepBatch(flow_name="x", game_master=object(), turns=[], turn_policy=policy)
    assert batch.turn_policy is policy


# --------------------------------------------------------------------------- #
# build_flow_turn_policies resolution + validation
# --------------------------------------------------------------------------- #


def test_build_flow_turn_policies_resolves_each_slot() -> None:
    resolved = build_flow_turn_policies(
        {
            "chatty": {"built_in": "fixed_count", "params": {"count": 3}},
            "quiet": {"built_in": "single_action"},
        }
    )
    assert isinstance(resolved["chatty"], FixedCountTurnPolicy)
    assert resolved["chatty"].count == 3
    assert isinstance(resolved["quiet"], SingleActionTurnPolicy)


def test_build_flow_turn_policies_empty_and_none_are_noops() -> None:
    assert build_flow_turn_policies(None) == {}
    assert build_flow_turn_policies({}) == {}


def test_build_flow_turn_policies_rejects_non_mapping() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        build_flow_turn_policies(["chatty"])


def test_build_flow_turn_policies_rejects_empty_flow_name() -> None:
    with pytest.raises(ValueError, match="empty flow name"):
        build_flow_turn_policies({"  ": {"built_in": "single_action"}})


def test_build_flow_turn_policies_rejects_non_mapping_slot() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        build_flow_turn_policies({"chatty": "single_action"})


# --------------------------------------------------------------------------- #
# Preset engine wiring
# --------------------------------------------------------------------------- #


def _flow_engine(flow_turn_policies: dict | None = None) -> FlowRuntimeEngine:
    step_params: dict = {"flow_order": ["chatty", "default"]}
    if flow_turn_policies is not None:
        step_params["flow_turn_policies"] = flow_turn_policies
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "turn_policy": {"built_in": "single_action"},
                    "step": {"built_in": "flow", "params": step_params},
                }
            }
        }
    )
    return FlowRuntimeEngine(config=cfg)


def test_flow_engine_threads_per_flow_policies_into_strategy() -> None:
    engine = _flow_engine({"chatty": {"built_in": "fixed_count", "params": {"count": 2}}})
    strategy = engine.step_strategy
    assert isinstance(strategy, FlowStepStrategy)
    assert isinstance(strategy.flow_turn_policies["chatty"], FixedCountTurnPolicy)
    assert "default" not in strategy.flow_turn_policies


def test_flow_engine_without_overrides_has_empty_map() -> None:
    engine = _flow_engine()
    assert isinstance(engine.step_strategy, FlowStepStrategy)
    assert engine.step_strategy.flow_turn_policies == {}


# --------------------------------------------------------------------------- #
# End-to-end behavior: per-flow policy applied, unlisted flows use global
# --------------------------------------------------------------------------- #


def test_per_flow_policy_changes_action_count_per_flow() -> None:
    engine = _flow_engine({"chatty": {"built_in": "fixed_count", "params": {"count": 3}}})
    alice = _Agent("Alice")  # chatty -> fixed_count(3)
    bob = _Agent("Bob")  # default -> global single_action
    gm = _GameMaster(
        name="gm",
        selected=["Alice", "Bob"],
        agent_flow_tags={"Alice": "chatty", "Bob": "default"},
    )

    result = engine.run_step(step_index=0, game_masters=[gm], agents=[alice, bob], verbose=False)

    assert len(alice.actions) == 3
    assert len(bob.actions) == 1
    assert result.active_agent_names == ("Alice", "Bob")
    assert result.action_phase["failed_turns"] == 0


def test_mixed_open_ended_and_single_action_in_one_step() -> None:
    # chatty flow runs open_ended (stops early on FINISHED); default runs the
    # global single_action policy. Both flows execute in the same step under the
    # shared worker pool without deadlock.
    engine = _flow_engine({"chatty": {"built_in": "open_ended", "params": {"max_actions": 5}}})
    alice = _Agent("Alice", scripted=["post", "FINISHED", "post"])  # open_ended
    bob = _Agent("Bob")  # single_action
    gm = _GameMaster(
        name="gm",
        selected=["Alice", "Bob"],
        agent_flow_tags={"Alice": "chatty", "Bob": "default"},
    )

    result = engine.run_step(step_index=0, game_masters=[gm], agents=[alice, bob], verbose=False)

    # Alice stops after emitting FINISHED on her 2nd action (before max_actions=5).
    assert alice.actions == ["post", "FINISHED"]
    assert len(bob.actions) == 1
    # Telemetry stays well-formed under a mixed-policy step.
    assert result.active_agent_names == ("Alice", "Bob")
    assert result.worker_limit >= 1
    assert result.requested_workers == 2
    assert result.action_phase["active_agents"] == 2
    assert "retry" in result.action_phase
    assert not result.degraded
