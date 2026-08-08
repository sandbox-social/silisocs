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

import random
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from silisocs.simulation_engines.policies._rng import stable_rng

_RATE_KEYS = ("inactive_to_active", "active_to_inactive")


def _rng(seed: int, step_index: int, salt: str) -> random.Random:
    """Deterministic local RNG per (seed, step, salt); never the global one."""
    # Key format is domain-specific to participation; only the digest→Random
    # mechanic is shared (see policies/_rng.stable_rng).
    return stable_rng(f"{seed}|participation|{step_index}|{salt}")


def _rates_for(
    agent_name: str,
    *,
    activity_transition_rates: Mapping[str, Mapping[str, float]],
    sim_roles: Mapping[str, str],
) -> Mapping[str, float]:
    """Per-agent rates, falling back to the agent's sim role's rates."""
    role = sim_roles.get(agent_name, "")
    return activity_transition_rates.get(agent_name, activity_transition_rates.get(role, {}))


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, value))


def _activation_rates(
    agent_name: str,
    *,
    activity_transition_rates: Mapping[str, Mapping[str, float]],
    sim_roles: Mapping[str, str],
) -> tuple[float, float]:
    """Resolve ``(inactive_to_active, active_to_inactive)`` for one agent.

    A rates entry may declare either key; the missing one mirrors the declared one
    (symmetric switching). Both missing is a config error that :func:`_require_rates`
    has already failed the run on, so the raise here is a safety net, not a path.
    """
    rates = _rates_for(
        agent_name,
        activity_transition_rates=activity_transition_rates,
        sim_roles=sim_roles,
    )
    inactive_to_active = rates.get("inactive_to_active")
    active_to_inactive = rates.get("active_to_inactive")
    if inactive_to_active is None:
        inactive_to_active = active_to_inactive
    if active_to_inactive is None:
        active_to_inactive = inactive_to_active
    if inactive_to_active is None or active_to_inactive is None:
        raise ValueError(f"No activity_transition_rates entry matches agent '{agent_name}'.")
    return (
        _clamp_probability(float(inactive_to_active)),
        _clamp_probability(float(active_to_inactive)),
    )


def _entry_activation_rates(entry: Any) -> tuple[float, float] | None:
    """Lenient ``(inactive_to_active, active_to_inactive)`` for one rates entry.

    Mirrors a missing key like :func:`_activation_rates` (symmetric switching)
    but returns ``None`` for an unusable entry instead of raising — this feeds
    the ``expected_active_share`` estimate hooks, which must never fail a
    preflight the way a real scheduling call fails a run.
    """
    if not isinstance(entry, Mapping):
        return None
    try:
        raw_on, raw_off = entry.get("inactive_to_active"), entry.get("active_to_inactive")
        on = None if raw_on is None else float(raw_on)
        off = None if raw_off is None else float(raw_off)
    except (TypeError, ValueError):
        return None
    on = off if on is None else on
    off = on if off is None else off
    if on is None or off is None:
        return None
    return _clamp_probability(on), _clamp_probability(off)


def _require_rates(policy: Any, agent_names: Sequence[str]) -> None:
    """Fail the run when any agent matches no activity rate (checked once, on the
    real roster).

    Rates are keyed by agent name or sim role, so a role that is left out — or rates
    written for roles this run doesn't have — would otherwise throttle those agents
    on an invented default: a run that looks configured while part of its population
    barely acts. That is a config error, so it stops the run instead.
    """
    if policy._rates_checked:
        return
    policy._rates_checked = True
    unmatched = sorted(
        str(name)
        for name in agent_names
        if str(name).strip()
        and not any(
            key
            in _rates_for(
                str(name),
                activity_transition_rates=policy.activity_transition_rates,
                sim_roles=policy.sim_roles,
            )
            for key in _RATE_KEYS
        )
    )
    if not unmatched:
        return
    roles = sorted({policy.sim_roles.get(name, "") for name in unmatched} - {""})
    raise ValueError(
        f"sim.engine.participation ({policy.name}): no activity_transition_rates entry "
        f"declares 'inactive_to_active' or 'active_to_inactive' for {len(unmatched)} agent(s) "
        f"(e.g. {unmatched[:3]}; unmatched sim roles: {roles or 'none'}). Add rates keyed by "
        "those agent names or sim roles, set "
        "'sim.engine.participation.params.active_probability' for one global rate "
        "(activity_probability only), or use 'sim.engine.participation.built_in: all' so every "
        "agent acts every step."
    )


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

    def expected_active_share(self) -> float:
        """Mean fraction of the roster expected to act per step (estimate hook).

        Studio preflight builds the real policy and asks it, so estimate
        semantics live beside scheduling semantics and cannot drift. Override
        in policies that gate activity; the default — every agent acts — is
        right for pass-through policies and a safe ceiling for custom ones.
        """
        return 1.0


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
    sim role. This policy reads ONE rate: ``inactive_to_active`` IS the per-step
    probability that the agent acts at all (there is no chain to transition), and
    ``active_to_inactive`` is read only as the mirror value when
    ``inactive_to_active`` is absent — configure the Markov chain's second rate
    here and it is inert. An agent matching no entry is a config error, not a
    default.
    ``min_active_agents`` tops up a too-small draw with a deterministic shuffle of
    the remaining agents.
    """

    activity_transition_rates: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    sim_roles: Mapping[str, str] = field(default_factory=dict)
    active_probability: float | None = None
    min_active_agents: int = 0
    name: str = "activity_probability"
    _rates_checked: bool = field(default=False, repr=False, compare=False)

    def _agent_probability(self, agent_name: str) -> float:
        if self.active_probability is not None:
            return _clamp_probability(float(self.active_probability))
        return _activation_rates(
            agent_name,
            activity_transition_rates=self.activity_transition_rates,
            sim_roles=self.sim_roles,
        )[0]

    def expected_active_share(self) -> float:
        """Per-step activation probability, averaged over the rate entries.

        Entries are averaged because agent counts per role are unknown before a
        run; ``min_active_agents`` top-ups are ignored (a floor, not a rate).
        """
        if self.active_probability is not None:
            return _clamp_probability(float(self.active_probability))
        shares = []
        for entry in self.activity_transition_rates.values():
            pair = _entry_activation_rates(entry)
            if pair is not None:
                shares.append(pair[0])
        return sum(shares) / len(shares) if shares else 1.0

    def participating_agents(
        self, *, agent_names: Sequence[str], step_index: int, seed: int
    ) -> list[str]:
        if self.active_probability is None:
            _require_rates(self, agent_names)
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
    _rates_checked: bool = field(default=False, repr=False, compare=False)

    def _advance(self, state: int, agent_name: str, step: int, seed: int) -> int:
        inactive_to_active, active_to_inactive = _activation_rates(
            agent_name,
            activity_transition_rates=self.activity_transition_rates,
            sim_roles=self.sim_roles,
        )
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
        _require_rates(self, agent_names)
        active = [
            name
            for name in agent_names
            if str(name).strip() and self._is_active(str(name), step_index, seed)
        ]
        return _top_up(active, agent_names, max(0, int(self.min_active_agents)), seed, step_index)

    def expected_active_share(self) -> float:
        """Long-run markov active share ``on / (on + off)``, averaged over entries.

        Entries are averaged because agent counts per role are unknown before a
        run; a never-switching entry (both rates 0) is skipped as unusable.
        """
        shares = []
        for entry in self.activity_transition_rates.values():
            pair = _entry_activation_rates(entry)
            if pair is not None and sum(pair) > 0:
                shares.append(pair[0] / (pair[0] + pair[1]))
        return sum(shares) / len(shares) if shares else 1.0
