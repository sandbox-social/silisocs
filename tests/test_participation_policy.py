"""Tests for sim-level participation policies (``sim.engine.participation``).

Covers the policy primitives (pass-through, probability, Markov — all pure
functions of (agent_names, step_index, seed), so replay- and resume-stable), the
factory (defaults, sim_roles injection, custom class_path), the engine-level
roster filter in ``run_step``, and the migration error for configs still naming
the moved built-ins at the GM next_acting slot.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.environments.gm.components.factory import build_next_acting_component
from silisocs.runtime.construction.engines import build_engine
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.policies.factory import build_participation_policy
from silisocs.simulation_engines.policies.participation import (
    ActivityMarkovParticipation,
    ActivityProbabilityParticipation,
    AllParticipation,
    ParticipationPolicy,
)
from silisocs.simulation_engines.runtime_base import StepResult

_NAMES = [f"agent_{i}" for i in range(12)]


# ----------------------------------------------------------------- policy units


def test_all_participation_is_pass_through() -> None:
    policy = AllParticipation()
    assert policy.participating_agents(agent_names=_NAMES, step_index=3, seed=7) == _NAMES


def test_probability_policy_is_deterministic_per_seed_and_step() -> None:
    policy = ActivityProbabilityParticipation(active_probability=0.5)
    first = policy.participating_agents(agent_names=_NAMES, step_index=2, seed=7)
    second = policy.participating_agents(agent_names=_NAMES, step_index=2, seed=7)
    assert first == second
    # A fresh instance draws identically: there is no hidden state to persist.
    fresh = ActivityProbabilityParticipation(active_probability=0.5)
    assert fresh.participating_agents(agent_names=_NAMES, step_index=2, seed=7) == first


def test_probability_policy_extremes() -> None:
    everyone = ActivityProbabilityParticipation(active_probability=1.0)
    assert everyone.participating_agents(agent_names=_NAMES, step_index=0, seed=1) == _NAMES
    nobody = ActivityProbabilityParticipation(active_probability=0.0, min_active_agents=0)
    assert nobody.participating_agents(agent_names=_NAMES, step_index=0, seed=1) == []


def test_probability_policy_min_active_top_up_is_deterministic() -> None:
    policy = ActivityProbabilityParticipation(active_probability=0.0, min_active_agents=3)
    active = policy.participating_agents(agent_names=_NAMES, step_index=4, seed=9)
    assert len(active) == 3
    assert set(active) <= set(_NAMES)
    again = policy.participating_agents(agent_names=_NAMES, step_index=4, seed=9)
    assert active == again


def test_probability_policy_uses_role_rates_via_sim_roles() -> None:
    policy = ActivityProbabilityParticipation(
        activity_transition_rates={
            "always_on": {"inactive_to_active": 1.0},
            "always_off": {"inactive_to_active": 0.0, "active_to_inactive": 0.0},
        },
        sim_roles={"alice": "always_on", "bob": "always_off"},
    )
    active = policy.participating_agents(agent_names=["alice", "bob"], step_index=0, seed=1)
    assert active == ["alice"]


def test_markov_policy_extremes_and_statelessness() -> None:
    always = ActivityMarkovParticipation(
        activity_transition_rates={"user": {"inactive_to_active": 1.0, "active_to_inactive": 0.0}},
        sim_roles=dict.fromkeys(_NAMES, "user"),
    )
    never = ActivityMarkovParticipation(
        activity_transition_rates={"user": {"inactive_to_active": 0.0, "active_to_inactive": 1.0}},
        sim_roles=dict.fromkeys(_NAMES, "user"),
    )
    for step in range(4):
        assert always.participating_agents(agent_names=_NAMES, step_index=step, seed=3) == _NAMES
        assert never.participating_agents(agent_names=_NAMES, step_index=step, seed=3) == []


def test_markov_policy_resume_matches_uninterrupted_run() -> None:
    """A policy queried fresh at step N (resume) equals the uninterrupted trajectory."""
    rates = {"user": {"inactive_to_active": 0.4, "active_to_inactive": 0.4}}
    roles = dict.fromkeys(_NAMES, "user")
    continuous = ActivityMarkovParticipation(activity_transition_rates=rates, sim_roles=roles)
    trajectory = [
        continuous.participating_agents(agent_names=_NAMES, step_index=step, seed=11)
        for step in range(6)
    ]
    resumed = ActivityMarkovParticipation(activity_transition_rates=rates, sim_roles=roles)
    assert resumed.participating_agents(agent_names=_NAMES, step_index=5, seed=11) == trajectory[5]


# ---------------------------------------------------------------------- factory


class _NoRolesPolicy(ParticipationPolicy):
    """Custom policy whose constructor takes no sim_roles (injection must be skipped)."""

    name = "no_roles"

    def __init__(self, keep: str = "") -> None:
        self.keep = keep

    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        del step_index, seed
        return [name for name in agent_names if name == self.keep]


def test_build_participation_policy_defaults_to_all() -> None:
    assert isinstance(build_participation_policy(None), AllParticipation)
    assert isinstance(build_participation_policy({"built_in": "all"}), AllParticipation)


def test_build_participation_policy_injects_sim_roles_when_supported() -> None:
    policy = build_participation_policy(
        {"built_in": "activity_probability", "params": {"min_active_agents": 2}},
        sim_roles={"alice": "user"},
    )
    assert isinstance(policy, ActivityProbabilityParticipation)
    assert policy.min_active_agents == 2
    assert dict(policy.sim_roles) == {"alice": "user"}


def test_build_participation_policy_skips_injection_for_custom_class() -> None:
    policy = build_participation_policy(
        {"class_path": f"{__name__}._NoRolesPolicy", "params": {"keep": "bob"}},
        sim_roles={"alice": "user"},
    )
    assert isinstance(policy, _NoRolesPolicy)
    assert policy.participating_agents(agent_names=["alice", "bob"], step_index=0, seed=0) == [
        "bob"
    ]


def test_build_participation_policy_rejects_unknown_built_in() -> None:
    with pytest.raises(ValueError, match="Unknown built_in"):
        build_participation_policy({"built_in": "nope"})


def test_switching_built_in_tolerates_base_slot_params() -> None:
    """Hydra merges never clear sibling keys: overriding participation.built_in
    keeps the base slot's activity params, which every built-in must tolerate.
    """
    leftover = {
        "active_probability": None,
        "min_active_agents": 1,
        "activity_transition_rates": {"user": {"inactive_to_active": 0.3}},
    }
    for built_in, expected in (
        ("all", AllParticipation),
        ("activity_probability", ActivityProbabilityParticipation),
        ("activity_markov", ActivityMarkovParticipation),
    ):
        policy = build_participation_policy({"built_in": built_in, "params": dict(leftover)})
        assert isinstance(policy, expected)


def test_markov_min_active_top_up() -> None:
    policy = ActivityMarkovParticipation(
        activity_transition_rates={"user": {"inactive_to_active": 0.0, "active_to_inactive": 1.0}},
        sim_roles=dict.fromkeys(_NAMES, "user"),
        min_active_agents=2,
    )
    active = policy.participating_agents(agent_names=_NAMES, step_index=3, seed=5)
    assert len(active) == 2
    assert active == policy.participating_agents(agent_names=_NAMES, step_index=3, seed=5)


# ------------------------------------------------------------- engine integration


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name


class _GM:
    name = "gm"

    def __init__(self) -> None:
        self.update_rosters: list[list[str]] = []

    def update(self, *, step: int, agents: list[Any], context: Any | None = None) -> None:
        del step, context
        self.update_rosters.append([agent.name for agent in agents])


class _CapturingStrategy:
    name = "capture"

    def __init__(self) -> None:
        self.rosters: list[list[str]] = []

    def run(self, *, engine, step_index, game_masters, agents, verbose) -> StepResult:
        del engine, step_index, game_masters, verbose
        self.rosters.append([agent.name for agent in agents])
        return StepResult()


class _KeepFirstPolicy(ParticipationPolicy):
    """Keeps the first agent and claims an unknown one (which must be ignored)."""

    name = "keep_first"

    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        del step_index, seed
        return [agent_names[0], "not_a_real_agent"]


def _run_one_step(engine: RuntimeEngine, gm: _GM, agents: list[_Agent]) -> None:
    engine.run_step(step_index=0, game_masters=[gm], agents=agents, verbose=False)


def test_engine_filters_step_roster_but_not_gm_updates() -> None:
    strategy = _CapturingStrategy()
    engine = RuntimeEngine(step_strategy=strategy, participation=_KeepFirstPolicy(), seed=5)
    gm = _GM()
    agents = [_Agent("alice"), _Agent("bob"), _Agent("carol")]
    _run_one_step(engine, gm, agents)
    # The strategy sees only the participating subset (unknown names ignored)...
    assert strategy.rosters == [["alice"]]
    # ...while GM updates still see the full roster.
    assert gm.update_rosters == [["alice", "bob", "carol"]]


def test_engine_without_participation_passes_full_roster() -> None:
    strategy = _CapturingStrategy()
    engine = RuntimeEngine(step_strategy=strategy)
    gm = _GM()
    _run_one_step(engine, gm, [_Agent("alice"), _Agent("bob")])
    assert strategy.rosters == [["alice", "bob"]]


# ------------------------------------------------------------------ build_engine


def _cfg(participation: dict[str, Any] | None = None, seed: int = 0) -> DictConfig:
    engine: dict[str, Any] = {"step": {"built_in": "base", "params": {}}}
    if participation is not None:
        engine["participation"] = participation
    return OmegaConf.create({"seed": seed, "sim": {"engine": engine}})


def test_build_engine_defaults_to_all_participation() -> None:
    engine = build_engine(_cfg())
    assert isinstance(engine.participation, AllParticipation)


def test_build_engine_builds_participation_with_seed_and_roles() -> None:
    engine = build_engine(
        _cfg(participation={"built_in": "activity_probability"}, seed=42),
        sim_roles={"alice": "user"},
    )
    assert isinstance(engine.participation, ActivityProbabilityParticipation)
    assert dict(engine.participation.sim_roles) == {"alice": "user"}
    assert engine.seed == 42


# -------------------------------------------------------------------- migration


def test_moved_next_acting_built_in_gets_migration_hint() -> None:
    for moved in ("activity_probability", "activity_markov"):
        with pytest.raises(ValueError, match="sim.engine.participation"):
            build_next_acting_component({"built_in": moved}, context=None)  # type: ignore[arg-type]
