"""Sim-level participation policies: which agents are in a step's roster at all.

Configured via ``sim.engine.participation`` (``{built_in|class_path, params}``), a
participation policy is the engine-layer half of agent selection: it filters the
step's agent roster BEFORE any scheduling (flow grouping, chain hops, branch
routing) and before every game master's ``next_acting`` component runs. The GM
slot stays the home of environment-derived selection (turn order, backend state);
this slot is the home of config-derived simulation logic (activity models).
Effective acting per hop = participation filter ∩ the GM's next_acting output.

Policies are pure functions of ``(agent_names, step_index, seed)`` — they hold no
runtime state that needs checkpointing, so a resumed run reproduces the exact
same participation draws as an uninterrupted one. The Markov policy derives its
activity chain from per-``(seed, agent, step)`` RNG (memoized forward, never
persisted) instead of carrying per-agent state.
"""

from __future__ import annotations

import hashlib
import random
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


def _rng(seed: int, step_index: int, salt: str) -> random.Random:
    """Deterministic local RNG per (seed, step, salt); never the global one."""
    key = f"{seed}|participation|{step_index}|{salt}"
    seed_int = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    return random.Random(seed_int)


def _rates_for(
    agent_name: str,
    *,
    activity_transition_rates: Mapping[str, Mapping[str, float]],
    sim_roles: Mapping[str, str],
) -> Mapping[str, float]:
    """Per-agent rates, falling back to the agent's sim role's rates."""
    role = sim_roles.get(agent_name, "")
    return activity_transition_rates.get(agent_name, activity_transition_rates.get(role, {}))


class ParticipationPolicy(ABC):
    """Filters the step roster; may only remove agents, never add or reorder them.

    The engine intersects the returned names with the live roster (preserving
    roster order), so returning unknown names has no effect.
    """

    name: str = "participation"

    @abstractmethod
    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        """Return the subset of ``agent_names`` that participates this step."""


class AllParticipation(ParticipationPolicy):
    """Every agent participates every step (the pass-through default)."""

    name = "all"

    def __init__(self, **_ignored: object) -> None:
        # Accepts and ignores any params: switching participation.built_in to
        # "all" merges over the base slot's activity params (Hydra never clears
        # sibling keys), and those leftovers must not be an error here.
        super().__init__()

    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        del step_index, seed
        return list(agent_names)


def _top_up(
    active: list[str], agent_names: Sequence[str], floor: int, seed: int, step_index: int
) -> list[str]:
    """Deterministically extend a too-small active set up to ``floor`` agents."""
    if floor <= 0 or len(active) >= floor:
        return active
    active_set = set(active)
    remaining = [name for name in agent_names if name not in active_set]
    _rng(seed, step_index, "min_active_top_up").shuffle(remaining)
    return active + remaining[: floor - len(active)]


@dataclass
class ActivityProbabilityParticipation(ParticipationPolicy):
    """Independent per-step activation draw per agent.

    ``active_probability`` (when set) applies globally; otherwise each agent's
    probability comes from ``activity_transition_rates`` keyed by agent name or
    sim role (``inactive_to_active``, falling back to ``active_to_inactive``,
    then 0.3). ``min_active_agents`` tops up a too-small draw with a
    deterministic shuffle of the remaining agents.
    """

    activity_transition_rates: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    sim_roles: Mapping[str, str] = field(default_factory=dict)
    active_probability: float | None = None
    min_active_agents: int = 0
    name: str = "activity_probability"

    def _agent_probability(self, agent_name: str) -> float:
        if self.active_probability is not None:
            return max(0.0, min(1.0, float(self.active_probability)))
        rates = _rates_for(
            agent_name,
            activity_transition_rates=self.activity_transition_rates,
            sim_roles=self.sim_roles,
        )
        p = rates.get("inactive_to_active")
        if p is None:
            p = rates.get("active_to_inactive")
        if p is None:
            p = 0.3
        return max(0.0, min(1.0, float(p)))

    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        active = [
            name
            for name in agent_names
            if str(name).strip()
            and _rng(seed, step_index, str(name)).random() < self._agent_probability(str(name))
        ]
        return _top_up(active, agent_names, max(0, int(self.min_active_agents)), seed, step_index)


@dataclass
class ActivityMarkovParticipation(ParticipationPolicy):
    """Role-conditioned active/inactive Markov chain per agent.

    Every agent starts active at step 0. Each step it transitions with
    ``inactive_to_active`` / ``active_to_inactive`` rates keyed by agent name or
    sim role. Every draw is per-``(seed, agent, step)``, so the chain is a pure
    function of the seed: resume and replay land on the exact same activity
    states without any persisted state. An in-instance memo advances the chain
    one draw per step (amortized O(1)); a fresh instance (resume) re-derives
    from step 0 once and lands on the identical states.
    """

    activity_transition_rates: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    sim_roles: Mapping[str, str] = field(default_factory=dict)
    min_active_agents: int = 0
    name: str = "activity_markov"
    # Pure memo of the seed-derived chain: agent -> (seed, last computed step,
    # state after that step). Never checkpointed — a fresh instance re-derives the
    # identical chain from step 0, so resume/replay draws are unchanged; steady
    # forward stepping advances one draw per step instead of re-walking from 0.
    _chain_memo: dict[str, tuple[int, int, int]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def _advance(self, state: int, agent_name: str, step: int, seed: int) -> int:
        rates = _rates_for(
            agent_name,
            activity_transition_rates=self.activity_transition_rates,
            sim_roles=self.sim_roles,
        )
        inactive_to_active = float(rates.get("inactive_to_active", 0.3))
        active_to_inactive = float(rates.get("active_to_inactive", inactive_to_active))
        draw = _rng(seed, step, agent_name).random()
        if state == 0:
            return 1 if draw < inactive_to_active else 0
        return 0 if draw < active_to_inactive else 1

    def _is_active(self, agent_name: str, step_index: int, seed: int) -> bool:
        memo = self._chain_memo.get(agent_name)
        if memo is not None and memo[0] == seed and memo[1] <= step_index:
            start_step, state = memo[1] + 1, memo[2]
        else:
            start_step, state = 0, 1
        for step in range(start_step, step_index + 1):
            state = self._advance(state, agent_name, step, seed)
        self._chain_memo[agent_name] = (seed, step_index, state)
        return state == 1

    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        active = [
            name
            for name in agent_names
            if str(name).strip() and self._is_active(str(name), step_index, seed)
        ]
        return _top_up(active, agent_names, max(0, int(self.min_active_agents)), seed, step_index)
